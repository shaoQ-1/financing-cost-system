"""页面路由 — 渲染 Bootstrap + htmx 页面。"""

import os
import tempfile
import shutil

from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify, current_app

from ..services import session_store as ss
from ..services.pdf_utils import get_all_page_previews
from ..state import SessionStep

web_bp = Blueprint("web", __name__)


@web_bp.route("/")
def index():
    return render_template("index.html")


# ── Task B: 三年财务分析 ──────────────────────────────────────────────────


@web_bp.route("/task-b")
def task_b_start():
    return render_template("task_b/index.html")


@web_bp.route("/task-b/select-pages/<sid>")
def task_b_select_pages(sid):
    meta = ss.get_session(current_app.config["SESSION_DIR"], sid)
    if not meta:
        return redirect(url_for("web.index"))
    files = meta.get("files", {})
    pages_by_year = {}
    years = sorted(files.keys()) if files else ["2022", "2023", "2024"]
    for y in years:
        fpath = files.get(y)
        if fpath and os.path.exists(fpath):
            pages_by_year[y] = get_all_page_previews(fpath)
    years = [y for y in years if y in pages_by_year]
    return render_template("task_b/step2_select_pages.html",
                           sid=sid, pages_by_year=pages_by_year, years=years)


@web_bp.route("/task-b/preview/<sid>")
def task_b_preview(sid):
    meta = ss.get_session(current_app.config["SESSION_DIR"], sid)
    if not meta:
        return redirect(url_for("web.index"))
    deepseek_raw = meta.get("deepseek_task_b_raw", {})
    validation = meta.get("validation_report", {"issues": [], "summary": {"total": 0, "high": 0, "medium": 0, "low": 0}})
    # 获取完整三表和异动信息
    results = meta.get("task_b_results") or {}
    full_subjects = results.get("full_subjects", {})
    anomalies = results.get("anomalies", {})
    return render_template("task_b/step4_preview.html",
                           sid=sid, data=deepseek_raw,
                           validation=validation,
                           full_subjects=full_subjects,
                           anomalies=anomalies,
                           years=sorted(deepseek_raw.keys()) if deepseek_raw else ["2022","2023","2024"])


@web_bp.route("/task-b/corrections/<sid>")
def task_b_corrections(sid):
    """AI 修正预览页：展示勾稽校验发现的问题及 AI 修正建议。"""
    meta = ss.get_session(current_app.config["SESSION_DIR"], sid)
    if not meta:
        return redirect(url_for("web.index"))
    validation = meta.get("validation_report", {"issues": [], "summary": {"total": 0}})
    ai_corrections = meta.get("ai_corrections", {})
    deepseek_raw = meta.get("deepseek_task_b_raw", {})
    page_selection = meta.get("page_selection", {})
    # 获取完整三表数据
    full_subjects = {}
    for year, yd in deepseek_raw.items():
        full_subjects[year] = yd.get("三表全科目", {})
    fin_data = meta.get("fin_data", {})
    return render_template("task_b/step3_correction.html",
                           sid=sid,
                           validation=validation,
                           ai_corrections=ai_corrections,
                           data=deepseek_raw,
                           full_subjects=full_subjects,
                           fin_data=fin_data,
                           page_selection=page_selection,
                           years=sorted(full_subjects.keys()) if full_subjects else sorted(fin_data.keys()))


@web_bp.route("/task-b/results/<sid>")
def task_b_results(sid):
    meta = ss.get_session(current_app.config["SESSION_DIR"], sid)
    if not meta:
        return redirect(url_for("web.index"))
    results = meta.get("task_b_results", {})
    years = sorted(meta.get("fin_data", {}).keys()) if meta.get("fin_data") else ["2022", "2023", "2024"]
    return render_template("task_b/step5_results.html",
                           sid=sid, results=results,
                           indicators=results.get("indicators", []),
                           anomalies=results.get("anomalies", {}),
                           full_subjects=results.get("full_subjects", {}),
                           fin_data=results.get("fin_data", {}),
                           years=years)


# ── Task A: 债务成本测算 ──────────────────────────────────────────────────


@web_bp.route("/task-a")
def task_a_start():
    return render_template("task_a/step1_upload.html")


@web_bp.route("/task-a/rates/<sid>")
def task_a_rates(sid):
    meta = ss.get_session(current_app.config["SESSION_DIR"], sid)
    if not meta:
        return redirect(url_for("web.index"))
    return render_template("task_a/step2_rates.html", sid=sid)


@web_bp.route("/task-a/results/<sid>")
def task_a_results(sid):
    meta = ss.get_session(current_app.config["SESSION_DIR"], sid)
    if not meta:
        return redirect(url_for("web.index"))
    results = meta.get("task_a_results", {})
    no_liab = results.get("不依赖明细表综合成本", {})
    return render_template("task_a/step4_results.html",
                           sid=sid, results=results, no_liab=no_liab)
