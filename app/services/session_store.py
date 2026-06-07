"""
文件型会话管理器。

每个会话 UUID 目录下：
  metadata.json   —  session state + 用户数据
  uploads/        —  上传的 PDF/Excel 文件
  outputs/        —  生成的报告、图表

24 小时未更新的会话会被清理。
"""

import json
import os
import shutil
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from ..state import SessionStep, VALID_TRANSITIONS

_lock = threading.Lock()


def _sid_dir(session_dir: str, sid: str) -> str:
    return os.path.join(session_dir, sid)


def _meta_path(sid_dir: str) -> str:
    return os.path.join(sid_dir, "metadata.json")


def create_session(session_dir: str) -> str:
    """创建新会话，返回 session_id。"""
    sid = uuid.uuid4().hex[:12]
    d = _sid_dir(session_dir, sid)
    os.makedirs(os.path.join(d, "uploads"), exist_ok=True)
    os.makedirs(os.path.join(d, "outputs"), exist_ok=True)

    meta = {
        "session_id": sid,
        "current_step": SessionStep.IDLE.value,
        "task_type": None,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "deepseek_api_key": "",
        "bank_rate": 3.0,
        "bond_rate": 5.0,
        "files": {},  # {"2022": "path", "2023": "path", "2024": "path", "liability": "path"}
        "page_selection": {},  # {"2022": {"balance_sheet": [0], "income_stmt": [1], "cash_flow": [2]}}
        "deepseek_raw": None,
        "fin_data": {},  # per year: {"2022": {...}, "2023": {...}}
        "deepseek_task_b_raw": None,  # full 三表 tree from DeepSeek
        "task_b_results": None,
        "task_a_results": None,
    }
    with open(_meta_path(d), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return sid


def get_session(session_dir: str, sid: str) -> dict | None:
    """读取会话元数据，不存在返回 None。"""
    p = _meta_path(_sid_dir(session_dir, sid))
    if not os.path.exists(p):
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_meta(session_dir: str, sid: str, meta: dict):
    p = _meta_path(_sid_dir(session_dir, sid))
    meta["updated_at"] = datetime.now().isoformat()
    with open(p, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def update_step(session_dir: str, sid: str, step: SessionStep) -> bool:
    """更新会话步骤，校验合法性。失败返回 False。"""
    with _lock:
        meta = get_session(session_dir, sid)
        if not meta:
            return False
        current = SessionStep(meta["current_step"])
        if step not in VALID_TRANSITIONS.get(current, []):
            return False
        meta["current_step"] = step.value
        _save_meta(session_dir, sid, meta)
        return True


def set_step(session_dir: str, sid: str, step: SessionStep):
    """强制设置步骤（跳过校验，用于初始化）。"""
    with _lock:
        meta = get_session(session_dir, sid)
        if not meta:
            return
        meta["current_step"] = step.value
        _save_meta(session_dir, sid, meta)


def save_data(session_dir: str, sid: str, key: str, value):
    """保存任意数据到会话。"""
    with _lock:
        meta = get_session(session_dir, sid)
        if not meta:
            return
        meta[key] = value
        _save_meta(session_dir, sid, meta)


def get_data(session_dir: str, sid: str, key: str, default=None):
    meta = get_session(session_dir, sid)
    if not meta:
        return default
    return meta.get(key, default)


def add_file(session_dir: str, sid: str, tag: str, src_path: str) -> str:
    """将上传文件复制到会话目录，返回目标路径。"""
    d = _sid_dir(session_dir, sid)
    dest = os.path.join(d, "uploads", f"{tag}_{os.path.basename(src_path)}")
    shutil.copy2(src_path, dest)
    with _lock:
        meta = get_session(session_dir, sid)
        if not meta:
            return ""
        meta.setdefault("files", {})[tag] = dest
        _save_meta(session_dir, sid, meta)
    return dest


def session_dir(session_dir: str, sid: str) -> str:
    return _sid_dir(session_dir, sid)


def outputs_dir(session_dir: str, sid: str) -> str:
    return os.path.join(_sid_dir(session_dir, sid), "outputs")


def cleanup_stale(session_dir: str, max_hours: int = 24):
    """清理超过 max_hours 的会话目录。"""
    now = datetime.now()
    for entry in os.listdir(session_dir):
        d = os.path.join(session_dir, entry)
        if not os.path.isdir(d) or entry.startswith("."):
            continue
        meta_path = os.path.join(d, "metadata.json")
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as f:
                try:
                    meta = json.load(f)
                    updated = datetime.fromisoformat(meta.get("updated_at", "2000-01-01"))
                    if now - updated > timedelta(hours=max_hours):
                        shutil.rmtree(d, ignore_errors=True)
                except (json.JSONDecodeError, ValueError):
                    shutil.rmtree(d, ignore_errors=True)
