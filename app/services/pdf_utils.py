"""PyMuPDF 工具：页面渲染、缩略图生成。"""

import base64
import io
import os

import fitz


def get_page_count(pdf_path: str) -> int:
    with fitz.open(pdf_path) as doc:
        return len(doc)


def render_page_preview(pdf_path: str, page_num: int, max_width: int = 800) -> str:
    """返回 base64 JPEG 预览图（用于悬浮放大）。"""
    with fitz.open(pdf_path) as doc:
        page = doc[page_num]
        scale = max_width / page.rect.width
        mat = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=mat)
        buf = pix.tobytes("jpeg")
    return base64.b64encode(buf).decode("utf-8")


def render_page_full(pdf_path: str, page_num: int) -> bytes:
    """返回高分辨率 PNG 字节（用于修正对比预览）。"""
    with fitz.open(pdf_path) as doc:
        page = doc[page_num]
        scale = 2000 / page.rect.width
        mat = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=mat)
        buf = pix.tobytes("png")
    return buf


def get_all_page_previews(pdf_path: str, max_width: int = 200) -> list[dict]:
    """返回 [{page_num, base64}, ...] 用于页面选择器。
    200px 小图快速加载，悬浮预览由前端单独 API 获取 800px 大图。
    """
    result = []
    try:
        with fitz.open(pdf_path) as doc:
            for i in range(len(doc)):
                page = doc[i]
                scale = max_width / page.rect.width
                mat = fitz.Matrix(scale, scale)
                pix = page.get_pixmap(matrix=mat)
                buf = pix.tobytes("jpeg")
                b64 = base64.b64encode(buf).decode("utf-8")
                result.append({"page_num": i, "base64": b64})
        return result
    except Exception:
        return result
