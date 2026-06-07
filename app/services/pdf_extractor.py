"""
多引擎 PDF 财务数据提取器。

支持三种提取模式：
  1. text_pdf  — 文本型 PDF（pdfplumber），免费、秒级
  2. ocr_pdf   — 扫描件 OCR（PaddleOCR），免费、中文优化
  3. ai_pdf    — AI 视觉提取（DeepSeek API），万能兜底

自动分级：先 text → 数据不足则 ocr → 仍不够则 ai。
"""

import io
import os
import re
import tempfile
from typing import Optional


# ── 引擎1: 文本PDF提取 ──────────────────────────────────────────────────────

def extract_text_from_pdf(pdf_path: str) -> Optional[str]:
    """使用 pdfplumber 提取文本型 PDF 内容。"""
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        if len(text.strip()) > 200:
            return text
    except Exception:
        pass

    # 回退 pdfminer
    try:
        from pdfminer.high_level import extract_text
        text = extract_text(pdf_path)
        if len(text.strip()) > 200:
            return text
    except Exception:
        pass

    return None


# ── 引擎2: OCR提取（Tesseract） ─────────────────────────────────────────────

PADDOCK_AVAILABLE = False
try:
    from paddleocr import PaddleOCR
    _ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
    PADDOCK_AVAILABLE = True
except Exception:
    _ocr = None


def extract_text_by_ocr(pdf_path: str, pages: Optional[list[int]] = None) -> Optional[str]:
    """使用 PaddleOCR 提取扫描件文字（中文优化）。"""
    if not PADDOCK_AVAILABLE:
        return None

    try:
        import fitz
        doc = fitz.open(pdf_path)
        all_text = []

        page_range = pages if pages else range(len(doc))
        for i in page_range:
            if i >= len(doc):
                break
            pix = doc[i].get_pixmap(matrix=fitz.Matrix(2, 2))
            img_bytes = pix.tobytes("png")
            img_io = io.BytesIO(img_bytes)

            result = _ocr.ocr(img_io, cls=True)
            if result and result[0]:
                page_text = "\n".join(line[1][0] for line in result[0] if line and len(line) > 1)
                all_text.append(page_text)

        doc.close()
        combined = "\n".join(all_text)
        if len(combined.strip()) > 100:
            return combined
    except Exception:
        pass

    return None


# ── 引擎3: AI 视觉提取（DeepSeek） ─────────────────────────────────────────

def extract_by_ai(pdf_path: str, api_key: str, pages: Optional[list[int]] = None,
                  base_url: str = "https://api.deepseek.com/v1") -> Optional[dict]:
    """使用 DeepSeek 视觉 API 提取结构化财务数据。"""
    if not api_key:
        return None

    try:
        from openai import OpenAI
        import fitz
        import base64

        client = OpenAI(api_key=api_key, base_url=base_url)

        doc = fitz.open(pdf_path)
        page_range = pages if pages else list(range(len(doc)))

        images = []
        for i in page_range:
            if i >= len(doc):
                break
            page = doc[i]
            scale = 1600 / page.rect.width
            mat = fitz.Matrix(scale, scale)
            pix = page.get_pixmap(matrix=mat)
            b64 = base64.b64encode(pix.tobytes("png")).decode("utf-8")
            images.append(f"data:image/png;base64,{b64}")

        doc.close()

        if not images:
            return None

        system_prompt = """你是一位专业的中国注册会计师（CPA），擅长从财务报表图像中提取结构化数据。只返回JSON，不要任何其他文字。"""
        user_prompt = """从提供的财务报表页面图像中提取财务数据。识别所有科目及数值，注意区分期末/期初、本期/上期。
单位统一转换为"万元"。返回JSON格式（只返回JSON）：
{
  "三表全科目": {"资产负债表": [...], "利润表": [...], "现金流量表": [...]},
  "mapped_keys": {
    "归母净利润": 数值, "营业收入": 数值, "营业成本": 数值,
    "资产总计": 数值, "负债合计": 数值, "所有者权益合计": 数值,
    "短期借款": 数值, "长期借款": 数值, "应付债券": 数值,
    "一年内到期非流动负债": 数值, "分配股利利润偿付利息现金": 数值,
    "利息支出_费用化": 数值, "营业利润": 数值, "利润总额": 数值,
    "净利润": 数值, "所得税费用": 数值, "流动资产合计": 数值,
    "流动负债合计": 数值, "存货": 数值, "预付款项": 数值,
    "应收账款": 数值, "盈余公积期末": 数值, "盈余公积期初": 数值,
    "一般风险准备期末": 数值, "一般风险准备期初": 数值,
    "未分配利润期末": 数值, "未分配利润期初": 数值
  }
}"""

        content = [{"type": "text", "text": user_prompt}]
        for img_url in images:
            content.append({"type": "image_url", "image_url": {"url": img_url}})

        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
        )

        import json
        text = resp.choices[0].message.content
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]
        return json.loads(text.strip())

    except Exception:
        return None


# ── 混合提取器（自动降级） ────────────────────────────────────────────────────

_MANDATORY_KEYS = {
    "归母净利润", "营业收入", "营业成本", "资产总计", "负债合计",
    "所有者权益合计", "短期借款", "长期借款", "应付债券",
    "分配股利利润偿付利息现金",
}

def _check_key_coverage(fin: dict) -> float:
    """计算关键字段覆盖度（0-1）。"""
    found = sum(1 for k in _MANDATORY_KEYS if fin.get(k) is not None)
    return found / len(_MANDATORY_KEYS)


def hybrid_extract(pdf_path: str, api_key: str = "",
                   pdf_text: Optional[str] = None) -> dict:
    """混合提取：自动从文本/OCR/AI 中选取最优提取结果。

    返回格式兼容 extract_fin() 的字典。
    """
    result = {"_engine_used": None, "_coverage": 0}

    # 尝试1: 文本PDF提取
    if pdf_text is None:
        pdf_text = extract_text_from_pdf(pdf_path)
    if pdf_text:
        from scripts.financial_analysis import extract_fin
        fin = extract_fin(pdf_text)
        cov = _check_key_coverage(fin)
        if cov >= 0.8:
            result.update(fin)
            result["_engine_used"] = "text_pdf"
            result["_coverage"] = cov
            return result
        result["_text_fallback"] = fin

    # 尝试2: OCR提取
    ocr_text = extract_text_by_ocr(pdf_path)
    if ocr_text:
        from scripts.financial_analysis import extract_fin
        fin = extract_fin(ocr_text)
        cov = _check_key_coverage(fin)
        if cov >= 0.6:
            result.update(fin)
            result["_engine_used"] = "ocr_pdf"
            result["_coverage"] = cov
            return result
        result["_ocr_fallback"] = fin

    # 尝试3: AI 视觉提取
    if api_key:
        ai_result = extract_by_ai(pdf_path, api_key)
        if ai_result:
            mk = ai_result.get("mapped_keys", {})
            cov = _check_key_coverage(mk)
            if cov >= 0.5:
                result.update(mk)
                result["_engine_used"] = "ai_pdf"
                result["_coverage"] = cov
                result["deepseek_raw"] = ai_result
                return result
            result["_ai_fallback"] = mk

    # 全部失败，返回最佳可用结果
    for fb in ["_ai_fallback", "_ocr_fallback", "_text_fallback"]:
        if fb in result:
            result.update(result[fb])
            result["_engine_used"] = fb.replace("_fallback", "")
            result["_coverage"] = _check_key_coverage(result)
            return result

    return result
