"""API 路由 — htmx JSON 端点（文件上传、提取、计算、下载）。"""

import json
import logging
import os
import tempfile
import threading
import io

from flask import Blueprint, request, jsonify, send_file, current_app

from ..services import session_store as ss
from ..services.pdf_utils import render_page_full, get_page_count
from ..services.deepseek import task_b_extract
from ..services.task_b_pipeline import run_task_b
from ..services.task_a_pipeline import run_task_a_web
from ..state import SessionStep

logger = logging.getLogger(__name__)

api_bp = Blueprint("api", __name__)


# ── 会话管理 ────────────────────────────────────────────────────────────────


@api_bp.route("/session/create", methods=["POST"])
def create_session():
    sid = ss.create_session(current_app.config["SESSION_DIR"])
    return jsonify({"session_id": sid})


@api_bp.route("/session/config", methods=["GET"])
def session_config():
    """返回当前配置状态（如是否已配置 DeepSeek Key）。"""
    key = current_app.config.get("DEEPSEEK_API_KEY", "")
    return jsonify({
        "deepseek_api_key": key if key else "",
        "has_api_key": bool(key),
    })


@api_bp.route("/session/preview", methods=["POST"])
def session_preview():
    """返回指定年份页面的高分辨率预览图（用于悬浮放大）。
    POST: {session_id, year, page}
    """
    data = request.get_json(force=True) or {}
    sid = data.get("session_id", "")
    year = data.get("year", "")
    page = data.get("page")
    if not sid or not year or page is None:
        return jsonify({"base64": "", "error": "missing params"})

    meta = ss.get_session(current_app.config["SESSION_DIR"], sid)
    if not meta:
        return jsonify({"base64": "", "error": "session not found"})

    fpath = meta.get("files", {}).get(year)
    if not fpath or not os.path.exists(fpath):
        return jsonify({"base64": "", "error": "file not found"})

    from ..services.pdf_utils import render_page_preview
    b64 = render_page_preview(fpath, int(page), max_width=800)
    return jsonify({"base64": b64, "page": page})


# ── Task B: 上传 PDF ───────────────────────────────────────────────────────


@api_bp.route("/task-b/upload", methods=["POST"])
def task_b_upload():
    sid = request.form.get("session_id") or ss.create_session(current_app.config["SESSION_DIR"])
    sdir = current_app.config["SESSION_DIR"]
    upload_dir = current_app.config["UPLOAD_DIR"]

    import re

    # 从文件名自动识别年度
    files_info = []  # [(file_obj, priority_index)]
    for key in ["file_0", "file_1", "file_2"]:
        f = request.files.get(key)
        if f and f.filename:
            files_info.append((f, int(key.split("_")[1])))

    for f, idx in files_info:
        ext = os.path.splitext(f.filename)[1]
        # 尝试从文件名提取年份
        match = re.search(r'(20\d{2})', f.filename)
        if match:
            year = match.group(1)
        else:
            # 按先后顺序映射为 2022/2023/2024（相对偏移）
            year = str(2022 + idx)
        tmp = os.path.join(upload_dir, f"{sid}_file_{year}{ext}")
        f.save(tmp)
        ss.add_file(sdir, sid, year, tmp)
        ss.update_step(sdir, sid, SessionStep.B_UPLOAD_PDFS)

    # 检查上传了几个文件
    meta = ss.get_session(sdir, sid)
    uploaded = sorted(meta.get("files", {}).keys())
    ss.set_step(sdir, sid, SessionStep.B_SELECT_PAGES)

    return jsonify({
        "session_id": sid,
        "uploaded_years": uploaded,
        "redirect": f"/task-b/select-pages/{sid}",
    })


# ── Task B: 选择页面 → 调用 DeepSeek ──────────────────────────────────────


@api_bp.route("/task-b/extract", methods=["POST"])
def task_b_extract_start():
    sid = request.json.get("session_id")
    page_selection = request.json.get("page_selection", {})
    api_key = request.json.get("api_key", "") or current_app.config.get("DEEPSEEK_API_KEY", "")
    extract_mode = request.json.get("extract_mode", "auto")

    sdir = current_app.config["SESSION_DIR"]
    ss.save_data(sdir, sid, "page_selection", page_selection)
    ss.save_data(sdir, sid, "deepseek_api_key", api_key)
    ss.save_data(sdir, sid, "extract_mode", extract_mode)
    ss.set_step(sdir, sid, SessionStep.B_EXTRACTING)

    # 在创建线程前捕获配置，避免后台线程访问 app context 报错
    deepseek_base_url = current_app.config.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    threading.Thread(target=_do_task_b_extract, args=(sdir, sid, api_key, deepseek_base_url), daemon=True).start()

    return jsonify({"session_id": sid, "status": "extracting"})


def _do_task_b_extract(sdir: str, sid: str, api_key: str, deepseek_base_url: str = "https://api.deepseek.com/v1"):
    """后台执行文本/OCR/AI 混合提取。"""

    def update_log(msg):
        ss.save_data(sdir, sid, "extract_log", msg)
        logger.info(f"[{sid}] {msg}")

    try:
        meta = ss.get_session(sdir, sid)
        files = meta.get("files", {})
        sel = meta.get("page_selection", {})
        extract_mode = meta.get("extract_mode", "auto")

        update_log("开始提取，渲染页面图像...")
        years_pages = {}
        years_pdfs = {}
        for year, stmts in sel.items():
            fpath = files.get(year)
            if not fpath or not os.path.exists(fpath):
                continue
            years_pdfs[year] = fpath
            # AI 模式需要渲染页面图像
            if extract_mode in ("auto", "ai"):
                images = []
                for stmt_type, pages in stmts.items():
                    for p in pages:
                        images.append(render_page_full(fpath, int(p)))
                if images:
                    years_pages[year] = images

        if not years_pages and extract_mode in ("auto", "ai"):
            # 纯文本/OCR模式不需要 images
            if not years_pdfs:
                raise ValueError("未选择有效页面")

        update_log("页面渲染完成，发送至 OCR/DeepSeek 提取...")

        # 传 extract_mode 给提取器
        result = task_b_extract(api_key, years_pages, years_pdfs=years_pdfs,
                                extract_mode=extract_mode)

        update_log("提取完成，开始数据校验...")
        ss.save_data(sdir, sid, "deepseek_task_b_raw", result)

        # 提取 fin_data（mapped_keys per year）
        fin_data = {}
        for year, data in result.items():
            fin_data[year] = data.get("mapped_keys", {})
        ss.save_data(sdir, sid, "fin_data", fin_data)

        # 执行勾稽校验 + AI 修正
        from ..services.ocr_corrector import validate_and_report, ai_correct, apply_corrections
        years_list = sorted(fin_data.keys())
        validation = validate_and_report(fin_data, years_list)

        if validation["has_issues"] and api_key:
            try:
                ai_result = ai_correct(
                    fin_data, years_list, validation,
                    api_key, deepseek_base_url
                )
                if ai_result and ai_result.get("corrections"):
                    corrected = apply_corrections(fin_data, ai_result["corrections"])
                    ss.save_data(sdir, sid, "fin_data", corrected)
                    ss.save_data(sdir, sid, "ai_corrections", {
                        "corrections": ai_result.get("corrections", {}),
                        "changes": ai_result.get("changes", []),
                        "confidence": ai_result.get("confidence", "medium"),
                    })
                    for year, vals in corrected.items():
                        if year in result:
                            result[year]["mapped_keys"] = vals
                    ss.save_data(sdir, sid, "deepseek_task_b_raw", result)
                    update_log("AI 修正完成")
            except Exception as e:
                logger.warning(f"AI correction failed after PDF extract: {e}")

        update_log("提取完成")
        ss.save_data(sdir, sid, "validation_report", validation)
        ss.set_step(sdir, sid, SessionStep.B_EXTRACTION_DONE)
    except Exception as e:
        ss.save_data(sdir, sid, "extract_error", str(e))
        ss.set_step(sdir, sid, SessionStep.ERROR)


@api_bp.route("/task-b/extract-status/<sid>")
def task_b_extract_status(sid):
    meta = ss.get_session(current_app.config["SESSION_DIR"], sid)
    if not meta:
        return jsonify({"status": "not_found"})

    step = SessionStep(meta["current_step"])
    if step == SessionStep.B_EXTRACTION_DONE:
        # 检测是否有修正
        meta = ss.get_session(current_app.config["SESSION_DIR"], sid)
        ai_corr = meta.get("ai_corrections", {})
        if ai_corr and ai_corr.get("changes"):
            return jsonify({"status": "done", "redirect": f"/task-b/corrections/{sid}"})
        return jsonify({"status": "done", "redirect": f"/task-b/preview/{sid}"})
    elif step == SessionStep.ERROR:
        return jsonify({"status": "error", "message": meta.get("extract_error", "未知错误")})
    elif step == SessionStep.B_EXTRACTING:
        log = meta.get("extract_log", "")
        statuses = ["提取中"]
        if "OCR extracted" in log:
            statuses = ["OCR识别完成", "发送至 DeepSeek 结构化..."]
        elif "DeepSeek parsed" in log:
            statuses = ["DeepSeek 结构化完成", "勾稽校验中..."]
        elif "提取完成" in log:
            statuses = ["提取完成"]
        return jsonify({"status": "extracting", "log": statuses[-1] if statuses else "处理中..."})
    else:
        return jsonify({"status": "idle"})


# ── Task B: 手动上传 Excel（AI 效果不好时备用） ────────────────────────────


@api_bp.route("/task-b/upload-excel", methods=["POST"])
def task_b_upload_excel():
    """上传用户手动整理的 Excel 财务数据表。
    支持三种独立文件或一个合并文件。
    """
    sid = request.form.get("session_id")
    sdir = current_app.config["SESSION_DIR"]
    upload_dir = current_app.config["UPLOAD_DIR"]

    if not sid:
        sid = ss.create_session(sdir)

    try:
        import pandas as pd
        pd.set_option('future.no_silent_downcasting', True)
    except ImportError:
        return jsonify({"error": "pandas 未安装"}), 500

    try:
        from ..services.subject_alias import to_canonical, classify_subject
        import re

        # ── 收集三个独立 Excel 文件 ──
        single_files = []
        for key in ["excel_0", "excel_1", "excel_2"]:
            f = request.files.get(key)
            if f and f.filename:
                single_files.append(f)

        if not single_files:
            return jsonify({"error": "请选择 Excel 文件"}), 400

        years_sorted = []
        dfs = {}
        for idx, f in enumerate(single_files):
            ext = os.path.splitext(f.filename)[1].lower()
            m = re.search(r'(20\d{2})', f.filename)
            year = m.group(1) if m else str(2022 + idx)
            tmp = os.path.join(upload_dir, f"{sid}_excel_{year}{ext}")
            f.save(tmp)
            if ext in (".xlsx", ".xls"):
                df = pd.read_excel(tmp, dtype=str)
            else:
                df = pd.read_csv(tmp, dtype=str, encoding="utf-8-sig")
            years_sorted.append(year)
            dfs[year] = df

        fin_data = {}
        full_subjects = {year: {"资产负债表": [], "利润表": [], "现金流量表": []} for year in years_sorted}
        aligned_years = sorted(years_sorted)

        for year in years_sorted:
            df = dfs[year]
            name_col = None
            for col in df.columns:
                col_s = str(col).strip()
                if col_s in ("科目", "科目名称", "项目", "项目名称"):
                    name_col = col_s
                    break
            if not name_col:
                return jsonify({"error": f"{year}年文件未找到『科目』或『科目名称』列"}), 400

            val_col = None
            for col in df.columns:
                if str(col).strip() != name_col:
                    val_col = col
                    break
            if val_col is None:
                return jsonify({"error": f"{year}年文件缺少数值列"}), 400

            df[val_col] = pd.to_numeric(df[val_col], errors="coerce")
            df["标准名"] = df[name_col].apply(to_canonical)
            df["报表类别"] = df["标准名"].apply(classify_subject)

            year_data = {}
            for _, row in df.iterrows():
                key = row["标准名"]
                val = row[val_col]
                if pd.notna(val) and val != 0:
                    year_data[key] = float(val)

                raw_name = row[name_col]
                if pd.notna(val):
                    try:
                        cat = row["报表类别"]
                        is_bs = (cat == "资产负债表")
                        item = {
                            "科目": raw_name,
                            "期末": float(val) if is_bs else None,
                            "本期": float(val) if not is_bs else None,
                        }
                        if is_bs:
                            full_subjects[year]["资产负债表"].append(item)
                        elif cat == "利润表":
                            full_subjects[year]["利润表"].append(item)
                        elif cat == "现金流量表":
                            full_subjects[year]["现金流量表"].append(item)
                    except (ValueError, TypeError):
                        pass

            fin_data[year] = year_data

        # ── 保存原始数据 + 校验 + AI 修正（两模式共用） ──
        from ..services.ocr_corrector import validate_and_report, ai_correct, apply_corrections
        validation = validate_and_report(fin_data, aligned_years)
        ss.save_data(sdir, sid, "fin_data_original", fin_data)

        api_key = current_app.config.get("DEEPSEEK_API_KEY", "")
        corrected_data = fin_data
        correction_changes = []

        if api_key and validation["has_issues"]:
            try:
                ai_result = ai_correct(
                    fin_data, aligned_years, validation,
                    api_key, current_app.config.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
                )
                if ai_result and ai_result.get("corrections"):
                    corrected_data = apply_corrections(fin_data, ai_result["corrections"])
                    correction_changes = ai_result.get("changes", [])
            except Exception as e:
                logger.warning(f"AI correction failed: {e}")

        result = {
            year: {
                "mapped_keys": corrected_data.get(year, {}),
                "三表全科目": full_subjects.get(year, {"资产负债表": [], "利润表": [], "现金流量表": []}),
                "_engine": "excel_validated",
                "_coverage": 1.0,
            }
            for year in aligned_years
        }

        ss.save_data(sdir, sid, "deepseek_task_b_raw", result)
        ss.save_data(sdir, sid, "fin_data", corrected_data)
        ss.save_data(sdir, sid, "validation_report", validation)

        subjects_count = len({k for y in fin_data.values() for k in y})

        if correction_changes:
            ss.save_data(sdir, sid, "ai_corrections", {
                "corrections": ai_result.get("corrections", {}),
                "changes": correction_changes,
                "confidence": ai_result.get("confidence", "medium"),
            })
            ss.set_step(sdir, sid, SessionStep.B_EXTRACTION_DONE)
            return jsonify({
                "session_id": sid, "years": aligned_years,
                "subjects_count": subjects_count, "has_corrections": True,
                "correction_count": len(correction_changes),
                "issues_count": validation["summary"]["total"],
                "redirect": f"/task-b/corrections/{sid}",
            })
        else:
            ss.set_step(sdir, sid, SessionStep.B_EXTRACTION_DONE)
            return jsonify({
                "session_id": sid, "years": aligned_years,
                "subjects_count": subjects_count, "has_corrections": False,
                "issues_count": validation["summary"]["total"],
                "redirect": f"/task-b/preview/{sid}",
            })

    except Exception as e:
        return jsonify({"error": f"Excel 解析失败: {e}"}), 500


@api_bp.route("/task-b/skip-corrections/<sid>", methods=["POST"])
def task_b_skip_corrections(sid):
    """用户选择跳过 AI 修正，恢复原始数据。"""
    sdir = current_app.config["SESSION_DIR"]
    original = ss.get_data(sdir, sid, "fin_data_original", {})
    if original:
        ss.save_data(sdir, sid, "fin_data", original)
        # 用原始数据更新 deepseek_task_b_raw
        meta = ss.get_session(sdir, sid)
        raw = meta.get("deepseek_task_b_raw", {})
        for year, vals in original.items():
            if year in raw:
                raw[year]["mapped_keys"] = vals
        ss.save_data(sdir, sid, "deepseek_task_b_raw", raw)
    return jsonify({"status": "ok", "redirect": f"/task-b/preview/{sid}"})


# ── Task B: 确认并生成分析 ─────────────────────────────────────────────────


@api_bp.route("/task-b/confirm", methods=["POST"])
def task_b_confirm():
    sid = request.json.get("session_id")
    corrections = request.json.get("corrections", {})

    sdir = current_app.config["SESSION_DIR"]

    # 如果有手动修正，更新 fin_data
    if corrections:
        fin_data = ss.get_data(sdir, sid, "fin_data", {})
        for year, corrs in corrections.items():
            for key, val in corrs.items():
                if year in fin_data:
                    fin_data[year][key] = val
        ss.save_data(sdir, sid, "fin_data", fin_data)

        # 同步更新 deepseek_task_b_raw 中的 mapped_keys
        meta = ss.get_session(sdir, sid)
        raw = meta.get("deepseek_task_b_raw", {})
        for year, vals in corrections.items():
            if year in raw:
                mk = raw[year].get("mapped_keys", {})
                for key, val in vals.items():
                    mk[key] = val
        ss.save_data(sdir, sid, "deepseek_task_b_raw", raw)

    # 执行 Task B 分析（含异常检测、指标计算）
    meta = ss.get_session(sdir, sid)
    deepseek_raw = meta.get("deepseek_task_b_raw", {})
    years = sorted(deepseek_raw.keys()) if deepseek_raw else ["2022", "2023", "2024"]
    out_dir = ss.outputs_dir(sdir, sid)

    try:
        results = run_task_b(deepseek_raw, years, out_dir)
        ss.save_data(sdir, sid, "task_b_results", results)
        ss.set_step(sdir, sid, SessionStep.B_RESULTS)
        return jsonify({"status": "done", "redirect": f"/task-b/results/{sid}"})
    except Exception as e:
        ss.save_data(sdir, sid, "extract_error", str(e))
        ss.set_step(sdir, sid, SessionStep.ERROR)
        return jsonify({"status": "error", "message": str(e)}), 500


# ── Task A: 上传明细表 ──────────────────────────────────────────────────────


@api_bp.route("/task-a/upload", methods=["POST"])
def task_a_upload():
    sdir = current_app.config["SESSION_DIR"]
    sid = request.form.get("session_id", "").strip()
    if not sid:
        sid = ss.create_session(sdir)
    upload_dir = current_app.config["UPLOAD_DIR"]

    f = request.files.get("liability_file")
    if f and f.filename:
        ext = os.path.splitext(f.filename)[1]
        tmp = os.path.join(upload_dir, f"{sid}_liability{ext}")
        f.save(tmp)
        ss.add_file(sdir, sid, "liability", tmp)
        ss.set_step(sdir, sid, SessionStep.A_UPLOAD_LIABILITY)

    return jsonify({
        "session_id": sid,
        "redirect": f"/task-a/rates/{sid}",
    })


# ── Task A: 配置利率 → 执行计算 ────────────────────────────────────────────


@api_bp.route("/task-a/calculate", methods=["POST"])
def task_a_calculate():
    sid = request.json.get("session_id")
    bank_rate = float(request.json.get("bank_rate", 3.0))
    bond_rate = float(request.json.get("bond_rate", 5.0))

    sdir = current_app.config["SESSION_DIR"]
    ss.save_data(sdir, sid, "bank_rate", bank_rate)
    ss.save_data(sdir, sid, "bond_rate", bond_rate)
    ss.set_step(sdir, sid, SessionStep.A_RUNNING)

    # 后台线程执行
    threading.Thread(target=_do_task_a_calc, args=(sdir, sid), daemon=True).start()

    return jsonify({"status": "running", "session_id": sid})


def _do_task_a_calc(sdir: str, sid: str):
    try:
        meta = ss.get_session(sdir, sid)
        fin_data = meta.get("fin_data", {})
        # 取最近一年的 fin_data（Task A 需要单年）
        latest_year = sorted(fin_data.keys())[-1] if fin_data else ""
        single_fin = fin_data.get(latest_year, {})

        # 兼容 extract_fin 格式 — 需要最全数据（2024 或最新）
        # 如果 Task B 提取了多年度，尝试合并所有年度的数据
        # 但 Task A 真正需要的是最近一年的完整数据
        if not single_fin and "deepseek_task_b_raw" in meta:
            raw = meta["deepseek_task_b_raw"]
            for year in sorted(raw.keys(), reverse=True):
                single_fin = raw[year].get("mapped_keys", {})
                if single_fin:
                    break

        liability_path = meta.get("files", {}).get("liability", "")
        if not liability_path or not os.path.exists(liability_path):
            raise FileNotFoundError("金融负债明细表未找到")

        bank_rate = meta.get("bank_rate", 3.0)
        bond_rate = meta.get("bond_rate", 5.0)
        out_dir = ss.outputs_dir(sdir, sid)

        result = run_task_a_web(single_fin, liability_path, bank_rate, bond_rate, out_dir)
        ss.save_data(sdir, sid, "task_a_results", result)
        ss.set_step(sdir, sid, SessionStep.A_RESULTS)
    except Exception as e:
        ss.save_data(sdir, sid, "extract_error", str(e))
        ss.set_step(sdir, sid, SessionStep.ERROR)


@api_bp.route("/task-a/status/<sid>")
def task_a_status(sid):
    meta = ss.get_session(current_app.config["SESSION_DIR"], sid)
    if not meta:
        return jsonify({"status": "not_found"})

    step = SessionStep(meta["current_step"])
    if step == SessionStep.A_RESULTS:
        return jsonify({"status": "done", "redirect": f"/task-a/results/{sid}"})
    elif step == SessionStep.ERROR:
        return jsonify({"status": "error", "message": meta.get("extract_error", "未知错误")})
    elif step == SessionStep.A_RUNNING:
        return jsonify({"status": "running"})
    else:
        return jsonify({"status": "idle"})


# ── 文件下载 ────────────────────────────────────────────────────────────────


@api_bp.route("/session/<sid>/download/<filename>")
def download_file(sid, filename):
    sdir = current_app.config["SESSION_DIR"]
    out_dir = ss.outputs_dir(sdir, sid)
    fpath = os.path.join(out_dir, filename)
    if os.path.exists(fpath):
        return send_file(fpath, as_attachment=True)
    return jsonify({"error": "文件不存在"}), 404


@api_bp.route("/session/<sid>/chart/<filename>")
def serve_chart(sid, filename):
    sdir = current_app.config["SESSION_DIR"]
    out_dir = ss.outputs_dir(sdir, sid)
    fpath = os.path.join(out_dir, filename)
    if os.path.exists(fpath):
        return send_file(fpath, mimetype="image/png")
    return jsonify({"error": "文件不存在"}), 404


@api_bp.route("/session/<sid>/page-image/<year>/<int:page_num>")
def serve_page_image(sid, year, page_num):
    """返回高分辨率 PNG 页面图像用于修正对比。"""
    sdir = current_app.config["SESSION_DIR"]
    meta = ss.get_session(sdir, sid)
    if not meta:
        return jsonify({"error": "session not found"}), 404
    fpath = meta.get("files", {}).get(year)
    if not fpath or not os.path.exists(fpath):
        return jsonify({"error": "file not found"}), 404
    from ..services.pdf_utils import render_page_full
    png_bytes = render_page_full(fpath, page_num)
    return send_file(io.BytesIO(png_bytes), mimetype="image/png")


# ── 检查 session 是否可用 ──────────────────────────────────────────────────


@api_bp.route("/session/<sid>/state")
def session_state(sid):
    meta = ss.get_session(current_app.config["SESSION_DIR"], sid)
    if not meta:
        return jsonify({"exists": False})
    return jsonify({"exists": True, "step": meta["current_step"]})
