"""
财务数据提取服务 — 三层混合引擎。

自动分级策略：
  1. text_pdf  → pdfplumber/pdfminer（文本型PDF，免费、秒级）
  2. ocr_pdf   → PaddleOCR（扫描件，免费、中文优化，可选安装）
  3. ai_pdf    → DeepSeek 视觉 API（兜底，需 API Key）

如果 ① 提取覆盖度 ≥80%，直接出结果，无需 AI。
只在关键字段不足时才调用 DeepSeek，省时省钱。
"""

import json
import logging
import os
from typing import Optional

from .pdf_extractor import hybrid_extract


def task_b_extract(api_key: str, years_pages: dict[str, list[bytes]],
                   years_pdfs: Optional[dict[str, str]] = None,
                   base_url: str = "https://api.deepseek.com/v1",
                   extract_mode: str = "auto") -> dict:
    """提取多个年度的全部三表数据 — 使用混合引擎。

    如果提供了 PDF 路径 (years_pdfs)，优先尝试文本提取；
    文本提取覆盖不足时才调用 DeepSeek 视觉 API。

    提取模式 (extract_mode):
      - "auto": 默认，自动降级 text → OCR → AI
      - "text": 仅文本PDF提取
      - "ocr":  仅OCR提取
      - "ai":   仅DeepSeek视觉API
    """
    result = {}

    for year in years_pages:
        pdf_path = years_pdfs.get(year) if years_pdfs else None
        fin = None
        engine_used = None

        # 根据模式选择提取策略
        if extract_mode == "ai":
            # 仅 AI 视觉
            vision_key = os.environ.get("VISION_API_KEY", "") or api_key
            deepseek_result = _deepseek_vision_extract(vision_key, years_pages.get(year, []), base_url)
            if deepseek_result:
                result[year] = {
                    "三表全科目": deepseek_result.get("三表全科目", {}),
                    "mapped_keys": deepseek_result.get("mapped_keys", {}),
                    "_engine": "vision_ai",
                    "_coverage": 1.0,
                }
            continue

        if extract_mode == "ocr":
            # 仅 OCR
            if pdf_path:
                from .pdf_extractor import extract_text_by_ocr
                ocr_text = extract_text_by_ocr(pdf_path)
                if ocr_text:
                    from scripts.financial_analysis import extract_fin
                    fin = extract_fin(ocr_text)
                    result[year] = {
                        "三表全科目": {},
                        "mapped_keys": {k: v for k, v in fin.items()
                                        if not k.startswith("_")},
                        "_engine": "ocr_pdf",
                        "_coverage": 1.0,
                    }
            continue

        if extract_mode == "text":
            # 仅文本提取
            if pdf_path:
                from .pdf_extractor import extract_text_from_pdf
                text = extract_text_from_pdf(pdf_path)
                if text:
                    from scripts.financial_analysis import extract_fin
                    fin = extract_fin(text)
                    result[year] = {
                        "三表全科目": {},
                        "mapped_keys": {k: v for k, v in fin.items()
                                        if not k.startswith("_")},
                        "_engine": "text_pdf",
                        "_coverage": 1.0,
                    }
            continue

        # ===== auto 模式（默认）：text → OCR → AI =====
        if pdf_path:
            hybrid = hybrid_extract(pdf_path, api_key)
            coverage = hybrid.get("_coverage", 0)
            engine_used = hybrid.get("_engine_used")

            if coverage >= 0.8:
                result[year] = {
                    "三表全科目": {},
                    "mapped_keys": {k: v for k, v in hybrid.items()
                                    if not k.startswith("_")},
                    "_engine": engine_used,
                    "_coverage": coverage,
                }
                continue

            fin = hybrid

        if engine_used != "text_pdf" and engine_used != "ocr_pdf":
            # 尝试多模态视觉提取
            vision_key = os.environ.get("VISION_API_KEY", "") or api_key
            if not api_key:
                # 没有 DS key 但有 vision key
                pass
            deepseek_result = _deepseek_vision_extract(vision_key, years_pages.get(year, []), base_url)
            if deepseek_result:
                mk = deepseek_result.get("mapped_keys", {})
                if fin:
                    for k in mk:
                        if mk[k] is None and k in fin and fin[k] is not None:
                            mk[k] = fin[k]
                result[year] = {
                    "三表全科目": deepseek_result.get("三表全科目", {}),
                    "mapped_keys": mk,
                    "_engine": "vision_ai",
                    "_coverage": 1.0,
                }
                continue

        # 全部失败，返回文本提取的结果（如有）
        if fin:
            result[year] = {
                "三表全科目": {},
                "mapped_keys": {k: v for k, v in fin.items()
                                if not k.startswith("_")},
                "_engine": engine_used or "all_failed",
                "_coverage": fin.get("_coverage", 0),
            }

    return result


def task_a_single_extract(api_key: str, page_images: list[bytes],
                          base_url: str = "https://api.deepseek.com/v1") -> dict:
    """倒退到纯 DeepSeek 视觉提取（兼容旧调用）。"""
    return _deepseek_vision_extract(api_key, page_images, base_url) or {}


def _deepseek_vision_extract(api_key: str, images: list[bytes],
                              base_url: str) -> Optional[dict]:
    """OCR + DeepSeek 文本结构化提取。

    流程：本地 pytesseract OCR 识别 → DeepSeek 结构化整理为 JSON。
    无需多模态模型，适用于不支持 image_url 的纯文本模型。
    """
    if not api_key or not images:
        return None

    logger = logging.getLogger(__name__)

    # 检查是否配置了独立的多模态视觉 API（可选）
    vision_config = {
        "enabled": bool(os.environ.get("VISION_API_KEY", "")),
        "api_key": os.environ.get("VISION_API_KEY", ""),
        "base_url": os.environ.get("VISION_BASE_URL", "https://api.siliconflow.cn/v1"),
        "model": os.environ.get("VISION_MODEL", "deepseek-ai/deepseek-vl2"),
    }

    if vision_config["enabled"]:
        # 走独立视觉模型
        return _vision_api_extract(vision_config, images, logger)
    else:
        # 走本地 OCR + DeepSeek 文本
        return _ocr_then_deepseek(api_key, images, base_url, logger)


def _ocr_then_deepseek(api_key: str, images: list[bytes],
                        base_url: str, logger) -> Optional[dict]:
    """本地 OCR + DeepSeek 结构化。"""
    try:
        import pytesseract
        from PIL import Image
        import io
        from openai import OpenAI

        # OCR 识别
        all_text = ""
        for i, img_bytes in enumerate(images):
            try:
                pil_img = Image.open(io.BytesIO(img_bytes))
                ocr = pytesseract.image_to_string(pil_img, lang='chi_sim+eng')
                if ocr.strip():
                    all_text += f"\n=== 第{i+1}页 ===\n{ocr}\n"
                else:
                    all_text += f"\n=== 第{i+1}页 ===（无文字）\n"
            except Exception as e:
                logger.warning(f"Page {i+1} OCR failed: {e}")
                all_text += f"\n=== 第{i+1}页 ===（OCR失败）\n"

        if not all_text.strip():
            logger.warning("OCR: no text from any page")
            return None

        logger.info(f"OCR extracted {len(all_text)} chars")

        client = OpenAI(api_key=api_key, base_url=base_url)

        prompt = f"""你是一位中国注册会计师(CPA)。以下是企业财务报表的OCR识别文本，请提取所有科目和数值并输出JSON。

注意：1)识别期末/期初、本期/上期 2)保留原始数值单位（元），不要做单位转换 3)OCR可能有小数点错位，请按合理量级修正 4)科目名称不一致时归一化

OCR文本：
{all_text[-25000:]}

输出JSON格式（只返回JSON）：
{{"三表全科目":{{"资产负债表":[{{"科目":"名称","期末":数值,"期初":数值}}],"利润表":[{{"科目":"名称","本期":数值,"上期":数值}}],"现金流量表":[]}},"mapped_keys":{{"营业收入":数值,"营业成本":数值,"净利润":数值,"资产总计":数值,"负债合计":数值,"所有者权益合计":数值,"短期借款":数值,"长期借款":数值,"应付债券":数值,"流动资产合计":数值,"流动负债合计":数值,"存货":数值,"应收账款":数值,"营业利润":数值,"利润总额":数值,"所得税费用":数值,"归母净利润":数值}}}}"""

        for attempt in range(2):
            try:
                resp = client.chat.completions.create(
                    model="deepseek-chat",
                    messages=[
                        {"role": "system", "content": "你是一位中国注册会计师，输出JSON。"},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.0,
                    response_format={"type": "json_object"},
                    max_tokens=8192,
                    timeout=120,
                )
                text = resp.choices[0].message.content
                if not text or not text.strip():
                    logger.warning(f"Empty response (attempt {attempt+1})")
                    continue
                if "```json" in text:
                    text = text.split("```json")[1].split("```")[0]
                elif "```" in text:
                    text = text.split("```")[1].split("```")[0]
                result = json.loads(text.strip())
                logger.info(f"DeepSeek parsed: {len(result.get('mapped_keys', {}))} keys")
                return result
            except Exception as e:
                logger.warning(f"DeepSeek attempt {attempt+1}: {e}")
                if attempt < 1:
                    import time
                    time.sleep(2)
                    continue
    except Exception as e:
        logger.error(f"OCR+DeepSeek failed: {e}")
    return None


def _vision_api_extract(config: dict, images: list[bytes],
                         logger) -> Optional[dict]:
    """直接用多模态视觉 API 识别图片。"""
    try:
        from openai import OpenAI
        import base64

        client = OpenAI(api_key=config["api_key"], base_url=config["base_url"])

        prompt = """从财务报表图像中提取数据。输出JSON：
{"三表全科目":{"资产负债表":[],"利润表":[],"现金流量表":[]},"mapped_keys":{"营业收入":数值,"营业成本":数值,"净利润":数值,"资产总计":数值,"负债合计":数值,"所有者权益合计":数值,"短期借款":数值,"长期借款":数值,"应付债券":数值,"流动资产合计":数值,"流动负债合计":数值,"存货":数值,"应收账款":数值,"营业利润":数值,"利润总额":数值,"所得税费用":数值,"归母净利润":数值}}
只返回JSON。保留原始数值（元）。"""

        content = [{"type": "text", "text": prompt}]
        for img in images:
            b64 = base64.b64encode(img).decode("utf-8")
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

        for attempt in range(2):
            try:
                resp = client.chat.completions.create(
                    model=config["model"],
                    messages=[{"role": "user", "content": content}],
                    temperature=0.0,
                    max_tokens=8192,
                    timeout=180,
                )
                text = resp.choices[0].message.content
                if not text:
                    continue
                if "```json" in text:
                    text = text.split("```json")[1].split("```")[0]
                elif "```" in text:
                    text = text.split("```")[1].split("```")[0]
                result = json.loads(text.strip())
                logger.info(f"Vision parsed: {len(result.get('mapped_keys', {}))} keys")
                return result
            except Exception as e:
                logger.warning(f"Vision attempt {attempt+1}: {e}")
                if attempt < 1:
                    import time
                    time.sleep(2)
                    continue
    except Exception as e:
        logger.error(f"Vision API failed: {e}")
    return None


def task_b_extract_from_text(api_key: str, years_text: dict[str, str],
                              base_url: str = "https://api.deepseek.com/v1") -> dict:
    """从文本提取财务数据（替代视觉提取）。"""
    return _deepseek_text_extract(api_key, years_text, base_url) or {}


def _deepseek_text_extract(api_key: str, years_text: dict[str, str],
                            base_url: str) -> Optional[dict]:
    """从各年份的文本中提取结构化的财务数据。"""
    if not api_key or not years_text:
        return None

    logger = logging.getLogger(__name__)
    result = {}

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url)

        for year, text in years_text.items():
            if not text.strip():
                continue

            system_prompt = "你是一位专业的中国注册会计师(CPA)，从财务报表文本中提取结构化数据并输出JSON。"

            user_prompt = f"""以下是 {year} 年审计报告的OCR文本。请提取所有财务科目和数值，输出JSON。

文本内容：
{text[:30000]}

输出JSON格式：
{{
  "三表全科目": {{"资产负债表": [], "利润表": [], "现金流量表": []}},
  "mapped_keys": {{
    "营业收入": 数值, "营业成本": 数值,
    "净利润": 数值, "归母净利润": 数值,
    "资产总计": 数值, "负债合计": 数值, "所有者权益合计": 数值,
    "短期借款": 数值, "长期借款": 数值, "应付债券": 数值,
    "流动资产合计": 数值, "流动负债合计": 数值,
    "存货": 数值, "应收账款": 数值,
    "营业利润": 数值, "利润总额": 数值, "所得税费用": 数值
  }}
}}"""

            resp = client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0,
                response_format={"type": "json_object"},
                max_tokens=8192,
                timeout=120,
            )
            text = resp.choices[0].message.content
            if text:
                if "```json" in text:
                    text = text.split("```json")[1].split("```")[0]
                elif "```" in text:
                    text = text.split("```")[1].split("```")[0]
                parsed = json.loads(text.strip())
                result[year] = {
                    "三表全科目": parsed.get("三表全科目", {}),
                    "mapped_keys": parsed.get("mapped_keys", {}),
                    "_engine": "deepseek_text",
                    "_coverage": 1.0,
                }

        return result
    except Exception as e:
        logger.error(f"DeepSeek text extract failed: {e}")
        return None
