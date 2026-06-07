"""
Task B 编排：验证→全科目总表→指标→异动→Excel。
复用 financial_analysis.py 中的校验和计算逻辑。
"""

import os
import sys

# 确保能找到 scripts 模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from scripts.financial_analysis import (
    _check_balance_sheet,
    _check_income_stmt,
    _ocr_correction_hints,
    extract_fin,
)
import pandas as pd


def run_task_b(deepseek_data: dict, years: list[str], output_dir: str) -> dict:
    """执行 Task B 完整流程。

    deepseek_data: {"2022": {三表全科目, mapped_keys}, "2023": {...}, "2024": {...}}
    years: ["2022", "2023", "2024"]
    output_dir: 输出目录
    返回: {indicators, anomalies, corrections, excel_path, ocr_path, full_subjects}
    """
    os.makedirs(output_dir, exist_ok=True)
    corrections = []
    all_fin_data = {}
    full_subjects = {}

    for year in years:
        yd = deepseek_data.get(year, {})
        fin = yd.get("mapped_keys", {})
        all_fin_data[year] = fin

        # 保存全科目树
        full_subjects[year] = yd.get("三表全科目", {})

        # OCR 校验（模拟 extract_fin 格式）
        bs_errors = _check_balance_sheet(
            {f"{year}_{k}": v for k, v in fin.items()}, year
        )
        is_errors = _check_income_stmt(
            {f"{year}_{k}": v for k, v in fin.items()}, year
        )
        for e in bs_errors + is_errors:
            corrections.extend(_ocr_correction_hints([e]))

    # 构建全科目总表
    rows = _build_subject_table(full_subjects, years)

    # 计算指标
    indicators = _calc_indicators(all_fin_data, years)

    # 异动检测
    anomalies = _detect_anomalies(all_fin_data, years)

    # 生成 Excel
    excel_path = _gen_excel(rows, indicators, anomalies, corrections, years, output_dir, full_subjects)

    # 生成 OCR 文本报告
    ocr_path = _gen_ocr_report(corrections, output_dir)

    return {
        "indicators": indicators,
        "anomalies": anomalies,
        "corrections": corrections,
        "excel_path": excel_path,
        "ocr_path": ocr_path,
        "full_subjects": full_subjects,
        "fin_data": all_fin_data,
    }


def _build_subject_table(full_subjects: dict, years: list[str]) -> list:
    """从全科目树构建表格行，自动对齐异名同质科目。"""
    from .subject_alias import to_canonical, get_canonical_set

    # first pass: collect all raw names per year
    raw_items: dict[str, dict[str, dict]] = {}  # raw_name -> {year -> {val, stmt_type}}
    for year in years:
        for stmt_name in ["资产负债表", "利润表", "现金流量表"]:
            for item in full_subjects.get(year, {}).get(stmt_name, []):
                name = item.get("科目", "")
                if not name:
                    continue
                if name not in raw_items:
                    raw_items[name] = {}
                if stmt_name == "资产负债表":
                    # prefer 期末 over 期初
                    val = item.get("期末") if item.get("期末") is not None else item.get("期初")
                else:
                    val = item.get("本期") if item.get("本期") is not None else item.get("上期")
                raw_items[name][year] = {"val": val, "stmt": stmt_name}

    # second pass: alias normalization
    all_raw_names = list(raw_items.keys())
    can_map = get_canonical_set(all_raw_names)

    # build aggregated rows: canonical_name -> {year -> value}
    aggregated: dict[str, dict[str, str]] = {}
    for canonical, raw_names in can_map.items():
        row = {}
        for year in years:
            vals = []
            for rn in raw_names:
                if year in raw_items.get(rn, {}):
                    v = raw_items[rn][year]["val"]
                    if v is not None and v != "—":
                        vals.append(v)
            if vals:
                row[year] = f"{sum(vals) / len(vals):.2f}"  # 同名科目取均值
            else:
                row[year] = "—"
        aggregated[canonical] = row

    rows = []
    for name, vals in sorted(aggregated.items()):
        row = [name]
        for year in years:
            row.append(vals.get(year, "—"))
        rows.append(row)
    return rows


def _calc_indicators(all_fin: dict, years: list[str]) -> list[dict]:
    """计算财务指标，按盈利能力/营运能力/偿债能力分组输出。
    与目标模板一致：已获利息倍数、带次数的指标。
    """
    indicators = []

    for i, year in enumerate(years):
        d = all_fin.get(year, {})
        ind = {"年份": year}

        rev = d.get("营业收入", 0) or 0
        cost = d.get("营业成本", 0) or 0
        np_val = d.get("归母净利润", 0) or d.get("净利润", 0) or 0
        op_p = d.get("营业利润", 0) or 0
        ta = d.get("资产总计", 0) or 0
        tl = d.get("负债合计", 0) or 0
        eq = d.get("所有者权益合计", 0) or 0

        # ── 盈利能力 ──
        if rev > 0:
            ind["毛利率"] = f"{(rev - cost) / rev * 100:.2f}%"
            ind["净利率"] = f"{np_val / rev * 100:.2f}%"
            ind["营业利润率"] = f"{op_p / rev * 100:.2f}%"
        else:
            ind["毛利率"] = ind["净利率"] = ind["营业利润率"] = "N/A"

        prev_eq = all_fin.get(years[i - 1], {}).get("所有者权益合计", 0) or 0 if i > 0 else eq
        avg_eq = (eq + prev_eq) / 2
        ind["ROE (净资产收益率)"] = f"{np_val / avg_eq * 100:.2f}%" if avg_eq > 0 else "N/A"

        prev_ta = all_fin.get(years[i - 1], {}).get("资产总计", 0) or 0 if i > 0 else ta
        avg_ta = (ta + prev_ta) / 2
        ind["ROA (总资产净利率)"] = f"{np_val / avg_ta * 100:.2f}%" if avg_ta > 0 else "N/A"

        # ── 营运能力 ──
        if i > 0:
            prev = all_fin.get(years[i - 1], {})
            curr_ar = d.get("应收账款", 0) or 0
            prev_ar = prev.get("应收账款", 0) or 0
            curr_inv = d.get("存货", 0) or 0
            prev_inv = prev.get("存货", 0) or 0
            curr_ca = d.get("流动资产合计", 0) or 0
            prev_ca = prev.get("流动资产合计", 0) or 0

            if (curr_ar + prev_ar) / 2 > 0 and rev > 0:
                ind["应收账款周转率（次）"] = f"{rev / ((curr_ar + prev_ar) / 2):.2f}"
            if (curr_inv + prev_inv) / 2 > 0 and cost > 0:
                ind["存货周转率（次）"] = f"{cost / ((curr_inv + prev_inv) / 2):.2f}"
            if (curr_ca + prev_ca) / 2 > 0 and rev > 0:
                ind["流动资产周转率（次）"] = f"{rev / ((curr_ca + prev_ca) / 2):.2f}"
            if avg_ta > 0 and rev > 0:
                ind["总资产周转率（次）"] = f"{rev / avg_ta:.2f}"

        # ── 偿债能力 ──
        ind["资产负债率"] = f"{tl / ta * 100:.2f}%" if ta > 0 else "N/A"
        cl = d.get("流动负债合计", 0) or 0
        inv = d.get("存货", 0) or 0
        prep = d.get("预付款项", 0) or 0
        ca = d.get("流动资产合计", 0) or 0
        if cl > 0:
            ind["流动比率"] = f"{ca / cl:.2f}"
            ind["速动比率"] = f"{(ca - inv - prep) / cl:.2f}"

        # 已获利息倍数 = 息税前利润 / 利息支出
        # EBIT = 净利润 + 所得税 + 利息支出（财务费用）
        fin_exp = d.get("财务费用", 0) or 0
        ebit = np_val + (d.get("所得税费用", 0) or 0) + fin_exp
        interest_cost = d.get("其中利息支出", 0) or fin_exp
        if interest_cost and interest_cost != 0:
            ind["已获利息倍数"] = f"{ebit / interest_cost:.2f}"
        else:
            ind["已获利息倍数"] = "N/A"

        indicators.append(ind)

    return indicators


def _detect_anomalies(all_fin: dict, years: list[str]) -> dict:
    """检测同比变动 > 30% 的异常科目。"""
    anomalies: dict[str, list] = {}
    watch_keys = ["营业收入", "营业成本", "归母净利润", "净利润",
                  "资产总计", "负债合计", "应收账款", "存货",
                  "短期借款", "长期借款", "应付债券"]

    for i, year in enumerate(years):
        if i == 0:
            continue
        prev_year = years[i - 1]
        for key in watch_keys:
            cv = all_fin.get(year, {}).get(key, 0) or 0
            pv = all_fin.get(prev_year, {}).get(key, 0) or 0
            if pv and pv != 0:
                chg = (cv - pv) / abs(pv) * 100
                if abs(chg) > 30:
                    anomalies.setdefault(key, []).append((year, chg))
    return anomalies


def _gen_excel(rows: list, indicators: list, anomalies: dict,
               corrections: list, years: list[str], output_dir: str,
               full_subjects: dict = None) -> str:
    """生成多工作表 Excel 报告，三表拆分为独立子表。"""
    path = os.path.join(output_dir, "财务分析结果.xlsx")
    import math

    def _to_wan(val):
        """将元转换为万元，保留两位小数。"""
        try:
            v = float(val)
            return round(v / 10000, 2)
        except (ValueError, TypeError):
            return val

    with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
        # Sheet 1-3: 三表（如有全科目数据则用，否则从 rows 拆）
        sheets_created = set()

        if full_subjects:
            stmt_map = [
                ("资产负债表", "资产负债表"),
                ("利润表", "利润表"),
                ("现金流量表", "现金流量表"),
            ]
            for sheet_name, stmt_key in stmt_map:
                # 收集该报表所有科目
                stmt_data = []
                all_names = []
                seen = set()
                for year in years:
                    for item in full_subjects.get(year, {}).get(stmt_key, []):
                        nm = item.get("科目", "")
                        if nm and nm not in seen:
                            seen.add(nm)
                            all_names.append(nm)

                if not all_names:
                    continue

                for nm in all_names:
                    row_data = {"科目": nm}
                    for year in years:
                        vals = [it for it in full_subjects.get(year, {}).get(stmt_key, [])
                                if it.get("科目") == nm]
                        if vals:
                            if stmt_key == "资产负债表":
                                v = vals[0].get("期末") if vals[0].get("期末") is not None else vals[0].get("期初")
                            else:
                                v = vals[0].get("本期") if vals[0].get("本期") is not None else vals[0].get("上期")
                            row_data[year] = _to_wan(v)
                        else:
                            row_data[year] = "—"
                    stmt_data.append(row_data)

                if stmt_data:
                    df = pd.DataFrame(stmt_data, columns=["科目"] + years)
                    df.to_excel(writer, sheet_name=sheet_name, index=False)
                    sheets_created.add(sheet_name)

        # 如果 full_subjects 没有数据，回退到全科目总表
        if not sheets_created:
            subjects_df = pd.DataFrame(rows, columns=["科目名称"] + years)
            subjects_df.to_excel(writer, sheet_name="全科目总表", index=False)

        # Sheet: 财务指标
        if indicators:
            pd.DataFrame(indicators).to_excel(writer, sheet_name="财务指标", index=False)

        # Sheet: 异动简评
        anom_rows = []
        for subj, chgs in sorted(anomalies.items()):
            for y, c in chgs:
                anom_rows.append({"科目": subj, "年度": y, "变动幅度": f"{c:.1f}%"})
        if anom_rows:
            pd.DataFrame(anom_rows).to_excel(writer, sheet_name="异动简评", index=False)
        else:
            pd.DataFrame({"信息": ["未发现明显异动（变动>30%）"]}).to_excel(
                writer, sheet_name="异动简评", index=False)

    return path


def _gen_ocr_report(corrections: list, output_dir: str) -> str:
    path = os.path.join(output_dir, "OCR财务分析报告.txt")
    from datetime import datetime
    lines = [
        "=" * 60,
        "  OCR 数据校验与修正报告",
        "=" * 60,
        f"  生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]
    if corrections:
        lines.append("━" * 60)
        lines.append("  发现的问题与修正建议")
        lines.append("━" * 60)
        lines.extend(corrections)
    else:
        lines.append("  校验通过，未发现明显的 OCR 逻辑错误 ✓")
    lines.extend(["", "=" * 60, "  报告结束", "=" * 60])

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path
