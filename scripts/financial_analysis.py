"""
财务尽调分析工具包 — 穿透式融资成本核算引擎
=============================================
包含两个独立任务：
  - Task A: 债务倒挤测算与尽调推演
  - Task B: 三年财务OCR校验与指标计算

依赖: pandas, openpyxl, xlsxwriter, pdfplumber, pdfminer.six, matplotlib, numpy
"""

import os
import re
import warnings
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ── 全局常量 ───────────────────────────────────────────────────────────────
BANK_KEYWORDS = ["银行", "商行", "农信", "银团", "贷款", "农商", "村镇"]
BOND_KEYWORDS = ["债券", "中票", "公司债", "短融", "PPN", "超短融",
                 "定向工具", "债务融资工具", "中期票据", "企业债"]
CURRENT_DATE = datetime.now()


# ── DeepSeek 映射函数 ──────────────────────────────────────────────────────

def deepseek_extract_fin(deepseek_json: dict) -> dict:
    """将 DeepSeek 结构化输出映射为 extract_fin() 的返回格式。

    deepseek_json 应包含 mapped_keys 字段，
    其键名与 extract_fin() 的输出键名完全对应。
    """
    return deepseek_json.get("mapped_keys", {})


def rebuild_subject_table(deepseek_json: dict) -> dict:
    """提取全科目树用于 Task B 的全科目总表。

    返回 {"资产负债表": [...], "利润表": [...], "现金流量表": [...]}
    """
    return deepseek_json.get("三表全科目", {})


# ============================================================================
# Task A: 债务倒挤测算与尽调推演
# ============================================================================

# ── PDF 读取 ────────────────────────────────────────────────────────────────

def read_pdf_text(pdf_path: str) -> str:
    """读取PDF文本内容（支持pdfplumber和pdfminer双引擎回退）。"""
    text = ""
    # 引擎1: pdfplumber
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        if len(text.strip()) > 200:
            return text
    except Exception:
        pass
    # 引擎2: pdfminer (回退)
    try:
        from pdfminer.high_level import extract_text
        text = extract_text(pdf_path)
    except Exception as e:
        raise RuntimeError(f"PDF读取失败（两个引擎均失效）: {e}") from e
    return text


# ── 财务数据提取 ────────────────────────────────────────────────────────────

def extract_fin(text: str) -> dict:
    """从PDF文本中提取关键财务数据。

    提取科目（支持万元/亿元单位自动换算）：
      - 利润表：归母净利润、盈余公积、一般风险准备、未分配利润
               利息支出(财务费用附注)、营业收入、营业成本
      - 资产负债表：资产总计、负债合计、所有者权益合计
               短期借款、长期借款、应付债券、一年内到期非流动负债
               期初未分配利润（用于算变动）
      - 现金流量表：分配股利利润或偿付利息支付的现金
    """
    data = {}

    def _grep(pattern: str, default=None):
        m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if m:
            raw = m.group(1).replace(",", "").replace(" ", "")
            try:
                return float(raw)
            except ValueError:
                return default
        return default

    # ── 利润表项目 ──
    data["归母净利润"] = _grep(r"(?:归属于母公司(?:所有者)?的净利润|归母净利润)[：:\s]*([\d,.-]+)")
    data["盈余公积期末"] = _grep(r"盈余公积[：:\s]*([\d,.-]+)")
    data["盈余公积期初"] = _grep(r"(?:期初盈余公积|上年末盈余公积|盈余公积.*?期初)[：:\s]*([\d,.-]+)")
    data["一般风险准备期末"] = _grep(r"一般风险准备[：:\s]*([\d,.-]+)")
    data["一般风险准备期初"] = _grep(r"(?:期初一般风险准备|上年末一般风险准备|一般风险准备.*?期初)[：:\s]*([\d,.-]+)")
    data["未分配利润期末"] = _grep(r"未分配利润[：:\s]*([\d,.-]+)")
    data["未分配利润期初"] = _grep(r"(?:期初未分配利润|上年末未分配利润|年初未分配利润)[：:\s]*([\d,.-]+)")
    data["利息支出_费用化"] = _grep(r"(?:利息支出|利息费用)[：:\s]*([\d,.-]+)")
    data["营业收入"] = _grep(r"(?:营业收入|营业总收入)[：:\s]*([\d,.-]+)")
    data["营业成本"] = _grep(r"营业成本[：:\s]*([\d,.-]+)")

    # ── 资产负债表项目 ──
    data["资产总计"] = _grep(r"资产总计[：:\s]*([\d,.-]+)")
    data["负债合计"] = _grep(r"负债合计[：:\s]*([\d,.-]+)")
    data["所有者权益合计"] = _grep(r"(?:所有者权益合计|股东权益合计|净资产)[：:\s]*([\d,.-]+)")
    data["短期借款"] = _grep(r"短期借款[：:\s]*([\d,.-]+)")
    data["长期借款"] = _grep(r"长期借款[：:\s]*([\d,.-]+)")
    data["应付债券"] = _grep(r"应付债券[：:\s]*([\d,.-]+)")
    data["一年内到期非流动负债"] = _grep(r"一年内到期(?:的非流动负债|非流动负债)[：:\s]*([\d,.-]+)")

    # ── 现金流量表项目 ──
    data["分配股利利润偿付利息现金"] = _grep(
        r"分配股利[、,]\s*利润[、,]\s*(?:或)?偿付利息(?:支付)?的?现金[：:\s]*([\d,.-]+)"
    )

    # ── 单位自动识别与归一化 ──
    for key in list(data.keys()):
        val = data.get(key)
        if val is None:
            continue
        abs_val = abs(val)
        if 0 < abs_val < 1:
            # 可能是亿元 → 转万元
            data[key] = val * 10000
        elif abs_val > 1_000_000:
            # 可能是元 → 转万元
            data[key] = val / 10000

    return data


# ── 读取金融负债明细表 ──────────────────────────────────────────────────────

def read_liability_file(file_path: str) -> pd.DataFrame:
    """读取金融负债明细表（自动识别Excel/CSV）。"""

    def _detect_columns(df: pd.DataFrame) -> pd.DataFrame:
        """自动识别和重命名关键列。"""
        col_map = {}
        for col in df.columns:
            col_s = str(col).strip()
            # 借款余额
            if re.search(r"借款[金额余额]|余额", col_s):
                col_map[col] = "借款余额"
            # 融资金额
            elif re.search(r"融资金额", col_s):
                col_map[col] = "借款金额"
            # 融资品种
            elif re.search(r"融资品种|融资类型|借款类型|债务类型|种类", col_s):
                col_map[col] = "融资品种"
            # 融资主体
            elif re.search(r"融资主体|借款主体|单位名称|借款人", col_s):
                col_map[col] = "融资主体"
            # 借款机构
            elif re.search(r"借款机构|贷款机构|放款人|资金方", col_s):
                col_map[col] = "借款机构"
            # 提款日期
            elif re.search(r"提款日期|放款日期|起始日|起息日", col_s):
                col_map[col] = "提款日期"
            # 还款日期
            elif re.search(r"还款日期|到期日|结束日|到期日期|期限截止", col_s):
                col_map[col] = "还款日期"
            # 担保情况
            elif re.search(r"担保|增信|保证", col_s):
                col_map[col] = "担保情况"
        df = df.rename(columns=col_map)
        return df

    ext = os.path.splitext(file_path)[1].lower()
    if ext in (".xls", ".xlsx"):
        # 先尝试读取全部sheet，找数据最多的那个
        xls = pd.ExcelFile(file_path)
        best_sheet, best_rows, best_df = None, 0, None
        for sheet in xls.sheet_names:
            tmp = pd.read_excel(file_path, sheet_name=sheet, dtype=str)
            if len(tmp) > best_rows:
                best_sheet, best_rows, best_df = sheet, len(tmp), tmp
        if best_df is not None:
            df = _detect_columns(best_df)
        else:
            df = pd.read_excel(file_path, dtype=str)
            df = _detect_columns(df)
    elif ext == ".csv":
        df = pd.read_csv(file_path, dtype=str, encoding="utf-8-sig")
        df = _detect_columns(df)
    else:
        raise ValueError(f"不支持的文件格式: {ext}")

    return df


# ── 债务分类 ────────────────────────────────────────────────────────────────

def classify_debt(loan_type: str) -> str:
    """按融资品种名称归为三大类：银行借款 / 公开债券 / 非标及其他。"""
    loan_type = str(loan_type)
    if any(k in loan_type for k in BANK_KEYWORDS):
        return "银行借款"
    elif any(k in loan_type for k in BOND_KEYWORDS):
        return "公开债券"
    else:
        return "非标及其他"


def clean_liability_data(df: pd.DataFrame) -> pd.DataFrame:
    """清洗明细表：余额转数值、解析日期、剔除过期债务、分类。"""
    if "借款余额" not in df.columns:
        # 尝试用借款金额代替
        if "借款金额" in df.columns:
            df["借款余额"] = df["借款金额"]
        else:
            # 找第一个数值列
            for col in df.columns:
                if df[col].dtype == object:
                    try:
                        pd.to_numeric(df[col], errors="coerce")
                    except (ValueError, TypeError):
                        continue
                if np.issubdtype(df[col].dtype, np.number):
                    df["借款余额"] = df[col]
                    break
            if "借款余额" not in df.columns:
                raise KeyError("未找到『借款余额』或『借款金额』列，请检查明细表列名。")

    df["借款余额"] = pd.to_numeric(df["借款余额"], errors="coerce").fillna(0)

    if "还款日期" in df.columns:
        df["还款日期"] = pd.to_datetime(df["还款日期"], errors="coerce")
        # 按需决定是否剔除过期（默认保留，在图表中区分）
        # df = df[df["还款日期"].isna() | (df["还款日期"] >= CURRENT_DATE)].copy()

    # 自动分类
    if "标准分类" not in df.columns and "融资品种" in df.columns:
        df["标准分类"] = df["融资品种"].apply(classify_debt)

    return df


# ── 五步勾稽法 ──────────────────────────────────────────────────────────────

def five_step_reconciliation(fin: dict) -> dict:
    """五步勾稽法：从报表数据推真实利息支出。

    步骤：
      1. 应结转未分配利润 = 归母净利润 - 盈余公积变动 - 一般风险准备变动
      2. 理论分红 = 应结转未分配利润 - 未分配利润变动值
      3. 真实分红 = min(理论分红, 分配股利利润偿付利息现金)
      4. 真实利息支出 = 分配股利利润偿付利息现金 - 真实分红
      5. 综合融资成本 = 真实利息支出 / 全部债务平均值

    返回包含各步骤中间结果的字典。
    """
    result = {}

    # 提取输入
    np_attr = fin.get("归母净利润", 0) or 0
    surplus_e = fin.get("盈余公积期末", 0) or 0
    surplus_b = fin.get("盈余公积期初", 0) or 0
    reserve_e = fin.get("一般风险准备期末", 0) or 0
    reserve_b = fin.get("一般风险准备期初", 0) or 0
    retained_e = fin.get("未分配利润期末", 0) or 0
    retained_b = fin.get("未分配利润期初", 0) or 0
    cash_flow = fin.get("分配股利利润偿付利息现金", 0) or 0

    # Step 1: 应结转未分配利润
    surplus_change = surplus_e - surplus_b
    reserve_change = reserve_e - reserve_b
    transferable = np_attr - surplus_change - reserve_change
    result["归母净利润"] = np_attr
    result["盈余公积变动"] = f"{surplus_change:.2f}"
    result["一般风险准备变动"] = f"{reserve_change:.2f}"
    result["步骤1_应结转未分配利润"] = f"{transferable:.2f}"

    # Step 2: 理论分红
    retained_change = retained_e - retained_b
    theoretical_dividend = transferable - retained_change
    if theoretical_dividend < 0:
        theoretical_dividend = 0
    result["未分配利润变动"] = f"{retained_change:.2f}"
    result["步骤2_理论分红"] = f"{theoretical_dividend:.2f}"

    # Step 3: 真实分红
    real_dividend = min(theoretical_dividend, cash_flow) if cash_flow > 0 else theoretical_dividend
    result["现金流量表支出"] = f"{cash_flow:.2f}"
    result["步骤3_真实分红"] = f"{real_dividend:.2f}"

    # Step 4: 真实利息支出
    real_interest = cash_flow - real_dividend
    if real_interest < 0:
        real_interest = 0
    result["步骤4_真实利息支出"] = f"{real_interest:.2f}"

    # Step 5: 综合融资成本（需外部输入分母）
    result["_real_interest_float"] = real_interest
    result["_cash_flow_float"] = cash_flow
    result["_theoretical_dividend_float"] = theoretical_dividend

    return result


# ── 债务底盘汇总 ────────────────────────────────────────────────────────────

def summarize_debt_structure(df: pd.DataFrame) -> dict:
    """从明细表汇总三大类债务余额和占比。"""
    if "标准分类" not in df.columns:
        df["标准分类"] = df.get("融资品种", "未知").apply(classify_debt)

    summary = df.groupby("标准分类")["借款余额"].sum()
    total = summary.sum()

    bank_bal = summary.get("银行借款", 0)
    bond_bal = summary.get("公开债券", 0)
    ns_bal = summary.get("非标及其他", 0)

    return {
        "总有息负债": total,
        "银行借款余额": bank_bal,
        "银行借款占比": bank_bal / total if total > 0 else 0,
        "公开债券余额": bond_bal,
        "公开债券占比": bond_bal / total if total > 0 else 0,
        "非标余额": ns_bal,
        "非标占比": ns_bal / total if total > 0 else 0,
    }


# ── 核心计算 ────────────────────────────────────────────────────────────────

def calculate_costs(
    fin_data: dict,
    df_liability: pd.DataFrame,
    bank_rate: float = 3.0,
    bond_rate: float = 5.0,
) -> dict:
    """执行穿透式加权融资成本核算。

    参数:
        fin_data: extract_fin() 返回的财务数据
        df_liability: 清洗后的金融负债明细表 DataFrame
        bank_rate: 银行借款预估平均利率 (%)
        bond_rate: 公开债券预估平均利率 (%)

    返回包含所有中间结果和最终结论的字典。
    """
    result = {}

    # ── 五步勾稽法 ──
    reconciliation = five_step_reconciliation(fin_data)
    result.update(reconciliation)
    real_interest = reconciliation["_real_interest_float"]

    # ── 资产负债表平均有息负债 ──
    bs_short_term = fin_data.get("短期借款", 0) or 0
    bs_long_term = fin_data.get("长期借款", 0) or 0
    bs_bond = fin_data.get("应付债券", 0) or 0
    bs_maturing = fin_data.get("一年内到期非流动负债", 0) or 0

    # 期末有息负债 = 短期+长期+应付债券+一年内到期
    bs_total_debt_e = bs_short_term + bs_long_term + bs_bond + bs_maturing
    # 期初近似用期末的90% (如无期初数据)
    bs_total_debt_b = fin_data.get("期初有息负债", bs_total_debt_e * 0.9)
    bs_avg_debt = (bs_total_debt_e + bs_total_debt_b) / 2

    result["资产负债表短期借款"] = f"{bs_short_term:.2f}"
    result["资产负债表长期借款"] = f"{bs_long_term:.2f}"
    result["资产负债表应付债券"] = f"{bs_bond:.2f}"
    result["资产负债表一年内到期"] = f"{bs_maturing:.2f}"
    result["负债表期末有息负债"] = f"{bs_total_debt_e:.2f}"
    result["负债表期初有息负债(估)"] = f"{bs_total_debt_b:.2f}"
    result["负债表平均有息负债"] = f"{bs_avg_debt:.2f}"

    # ── 邦得综合融资成本（独立核算，不用明细表） ──
    if bs_avg_debt > 0:
        bond_cost_pct = real_interest / bs_avg_debt * 100
    else:
        bond_cost_pct = 0
    result["邦得综合融资成本"] = f"{bond_cost_pct:.4f}%"

    # ── 明细表债务结构 ──
    structure = summarize_debt_structure(df_liability)
    result["明细表总有息负债"] = f"{structure['总有息负债']:.2f}"
    result["明细表银行借款余额"] = f"{structure['银行借款余额']:.2f}"
    result["明细表银行借款占比"] = f"{structure['银行借款占比']*100:.2f}%"
    result["明细表公开债券余额"] = f"{structure['公开债券余额']:.2f}"
    result["明细表公开债券占比"] = f"{structure['公开债券占比']*100:.2f}%"
    result["明细表非标余额"] = f"{structure['非标余额']:.2f}"
    result["明细表非标占比"] = f"{structure['非标占比']*100:.2f}%"

    # ── 正算综合融资成本（用明细表分母+用户利率） ──
    excel_total = structure["总有息负债"]
    bank_bal = structure["银行借款余额"]
    bond_bal = structure["公开债券余额"]
    ns_bal = structure["非标余额"]

    bank_rate_dec = bank_rate / 100
    bond_rate_dec = bond_rate / 100

    if excel_total > 0:
        forward_cost = (
            bank_bal * bank_rate_dec
            + bond_bal * bond_rate_dec
            + ns_bal * 0.08  # 非标默认8%（用户可覆盖）
        ) / excel_total * 100
    else:
        forward_cost = 0

    result["用户给定银行利率"] = f"{bank_rate:.2f}%"
    result["用户给定债券利率"] = f"{bond_rate:.2f}%"
    result["正算加权综合融资成本"] = f"{forward_cost:.4f}%"

    # ── 倒挤非标成本 ──
    # 方法：从五步法真实利息中扣减银行和债券的利息
    bank_interest = bank_bal * bank_rate_dec
    bond_interest = bond_bal * bond_rate_dec
    remaining = real_interest - bank_interest - bond_interest

    result["银行消耗利息"] = f"{bank_interest:.2f}"
    result["债券消耗利息"] = f"{bond_interest:.2f}"
    result["非标剩余利息"] = f"{remaining:.2f}"

    if ns_bal > 0 and remaining > 0:
        squeeze_ns_rate = remaining / ns_bal * 100
        result["倒挤非标成本"] = f"{squeeze_ns_rate:.4f}%"
    else:
        squeeze_ns_rate = 0
        result["倒挤非标成本"] = "N/A（非标本金为0或剩余利息为负）"

    # ── 风控评级 ──
    real_ns_rate = None
    if ns_bal > 0 and remaining > 0:
        real_ns_rate = squeeze_ns_rate

    if real_ns_rate is not None:
        if real_ns_rate > 12:
            result["风控评级"] = ("🔴 高危预警", "非标成本异常高企（>12%），"
                                   "表明表观现金流存在严重表外摩擦损耗，流动性极其脆弱！")
        elif 6 <= real_ns_rate <= 12:
            result["风控评级"] = ("🟡 结构性合理", "非标成本处于行业合理区间，"
                                   "但仍是整体融资成本的主要拉高因素。")
        else:
            result["风控评级"] = ("🟢 偏低", "非标成本偏低，需核实利息资本化金额是否完整，"
                                   "或债务分类是否准确。")
    elif real_interest > 0 and ns_bal > 0 and remaining <= 0:
        result["风控评级"] = ("⚠️ 异常", "剩余利息为负，说明高估了银行/债券利率，"
                               "或存在未体现在明细表中的账外负债。")
    else:
        result["风控评级"] = ("ℹ️ 信息不足", "缺少足够数据完成风控评级。")

    result["_real_interest_float"] = real_interest
    result["_bond_cost_pct_float"] = bond_cost_pct
    result["_forward_cost_float"] = forward_cost
    result["_squeeze_ns_float"] = squeeze_ns_rate if isinstance(squeeze_ns_rate, (int, float)) else None
    result["_bs_avg_debt_float"] = bs_avg_debt
    result["_excel_total_float"] = excel_total
    result["_ns_bal_float"] = ns_bal
    result["_bank_bal_float"] = bank_bal
    result["_bond_bal_float"] = bond_bal

    return result


# ── 图表生成 ────────────────────────────────────────────────────────────────

def setup_chinese_font():
    """尝试设置matplotlib中文字体（优先微软雅黑）。"""
    import matplotlib.pyplot as plt
    import matplotlib.font_manager as fm
    for font_name in ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"]:
        try:
            fm.findfont(font_name, fallback_to_default=False)
            plt.rcParams["font.sans-serif"] = [font_name]
            plt.rcParams["axes.unicode_minus"] = False
            return
        except Exception:
            continue


def generate_pie_chart(result: dict, output_path: str):
    """生成债务结构饼图。"""
    import matplotlib.pyplot as plt

    setup_chinese_font()

    labels = ["银行借款", "公开债券", "非标及其他"]
    try:
        sizes = [
            float(result.get("明细表银行借款占比", "0%").replace("%", "")),
            float(result.get("明细表公开债券占比", "0%").replace("%", "")),
            float(result.get("明细表非标占比", "0%").replace("%", "")),
        ]
    except (ValueError, KeyError):
        sizes = [0, 0, 0]

    total_str = result.get("明细表总有息负债", "0")
    try:
        total_val = float(total_str)
    except ValueError:
        total_val = 0

    if sum(sizes) == 0:
        print("  [跳过] 债务结构饼图：无有效数据")
        return

    colors = ["#4A90D9", "#F5A623", "#D0021B"]
    fig, ax = plt.subplots(figsize=(8, 6))

    def autopct_abs(pct):
        absolute = int(total_val * pct / 100)
        return f"{pct:.1f}%\n({absolute:,.0f}万)"

    wedges, texts, autotexts = ax.pie(
        sizes,
        labels=labels,
        autopct=autopct_abs,
        startangle=140,
        colors=colors,
        textprops={"fontsize": 11},
    )
    for t in autotexts:
        t.set_fontsize(10)

    ax.set_title("债务结构分布（按融资品种）", fontsize=14, fontweight="bold", pad=20)

    fig.savefig(output_path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"  [图表] 债务结构饼图 → {output_path}")


def generate_maturity_chart(df: pd.DataFrame, output_path: str):
    """生成季度到期余额柱状图（已剔除过期债务）。"""
    import matplotlib.pyplot as plt

    setup_chinese_font()

    if "还款日期" not in df.columns or "借款余额" not in df.columns:
        print("  [跳过] 季度到期图：缺少还款日期或借款余额列")
        return

    df = df.copy()
    df["借款余额"] = pd.to_numeric(df["借款余额"], errors="coerce").fillna(0)
    df["还款日期"] = pd.to_datetime(df["还款日期"], errors="coerce")

    # 剔除过期债务（过期的不列入图表，但可用于计算）
    df_valid = df[df["还款日期"].notna() & (df["还款日期"] >= CURRENT_DATE)].copy()
    if df_valid.empty:
        print("  [跳过] 季度到期图：无有效未到期债务")
        return

    df_valid["year"] = df_valid["还款日期"].dt.year
    df_valid["quarter"] = df_valid["还款日期"].dt.quarter
    df_valid["year_quarter"] = df_valid["year"].astype(str) + "-Q" + df_valid["quarter"].astype(str)
    # 对year_quarter排序
    df_valid["_sort_key"] = df_valid["year"] * 10 + df_valid["quarter"]
    df_valid = df_valid.sort_values("_sort_key")

    quarterly = df_valid.groupby(["year", "quarter", "year_quarter"])["借款余额"].sum().reset_index(drop=False)  # keep all keys

    # 标记风险等级：1年内到期 🟡，1年后到期 🟢
    now_year = CURRENT_DATE.year
    now_quarter = (CURRENT_DATE.month - 1) // 3 + 1
    colors = []
    for _, r in quarterly.iterrows():
        if r["year"] < now_year or (r["year"] == now_year and r["quarter"] <= now_quarter + 1):
            colors.append("#F5A623")  # 🟡 1年内
        else:
            colors.append("#7ED321")  # 🟢 1年后

    fig, ax = plt.subplots(figsize=(14, 6))
    bars = ax.bar(quarterly["year_quarter"], quarterly["借款余额"], color=colors, width=0.6)

    ax.set_xlabel("到期季度", fontsize=11)
    ax.set_ylabel("借款余额（万元）", fontsize=11)
    ax.set_title("季度到期债务余额分布", fontsize=14, fontweight="bold", pad=20)

    # 数值标签
    for bar in bars:
        h = bar.get_height()
        if h > 0:
            ax.text(
                bar.get_x() + bar.get_width() / 2, h,
                f"{h / 10000:.1f}亿",
                ha="center", va="bottom", fontsize=7, rotation=45,
            )

    plt.xticks(rotation=45, ha="right", fontsize=8)
    fig.tight_layout()

    # 图例
    from matplotlib.patches import Patch
    legend_elements = [
        Patch(facecolor="#F5A623", label="1年内到期"),
        Patch(facecolor="#7ED321", label="1年后到期"),
    ]
    ax.legend(handles=legend_elements, loc="upper right")

    fig.savefig(output_path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"  [图表] 季度到期分布图 → {output_path}")


# ── 报告生成 ────────────────────────────────────────────────────────────────

def generate_reports(result: dict, output_dir: str):
    """生成《尽调推演计算书》和《邦得综合融资成本报告》。"""
    os.makedirs(output_dir, exist_ok=True)

    # ── 计算书 ──
    calc_lines = [
        "=" * 60,
        "  穿透式尽调推演计算书",
        "  生成时间: " + CURRENT_DATE.strftime("%Y-%m-%d %H:%M:%S"),
        "=" * 60,
        "",
        "━" * 60,
        "  【第一部分】五步勾稽法 — 真实利息支出推演",
        "━" * 60,
        "",
        f"  ① 应结转未分配利润",
        f"     归母净利润: {result.get('归母净利润', 'N/A'):>14} 万元",
        f"    - 盈余公积变动: {result.get('盈余公积变动', 'N/A'):>11} 万元",
        f"    - 一般风险准备变动: {result.get('一般风险准备变动', 'N/A'):>9} 万元",
        f"    = 应结转未分配利润: {result.get('步骤1_应结转未分配利润', 'N/A'):>11} 万元",
        "",
        f"  ② 理论分红",
        f"     应结转未分配利润: {result.get('步骤1_应结转未分配利润', 'N/A'):>11} 万元",
        f"    - 未分配利润变动: {result.get('未分配利润变动', 'N/A'):>11} 万元",
        f"    = 理论分红: {result.get('步骤2_理论分红', 'N/A'):>19} 万元  (负数取0)",
        "",
        f"  ③ 真实分红",
        f"     min(理论分红, 分配股利利润偿付利息现金)",
        f"     = min({result.get('步骤2_理论分红', 'N/A')}, {result.get('现金流量表支出', 'N/A')})",
        f"     = 真实分红: {result.get('步骤3_真实分红', 'N/A'):>18} 万元",
        "",
        f"  ④ 真实利息支出",
        f"     现金流量表支出: {result.get('现金流量表支出', 'N/A'):>13} 万元",
        f"    - 真实分红: {result.get('步骤3_真实分红', 'N/A'):>18} 万元",
        f"    = 真实利息支出: {result.get('步骤4_真实利息支出', 'N/A'):>14} 万元",
        "",
        "━" * 60,
        "  【第二部分】资产负债表平均有息负债",
        "━" * 60,
        "",
        f"   短期借款: {result.get('资产负债表短期借款', 'N/A'):>20} 万元",
        f"   长期借款: {result.get('资产负债表长期借款', 'N/A'):>20} 万元",
        f"   应付债券: {result.get('资产负债表应付债券', 'N/A'):>20} 万元",
        f"   一年内到期非流动负债: {result.get('资产负债表一年内到期', 'N/A'):>10} 万元",
        f"   ──────────────────────────────────────────",
        f"   期末有息负债合计: {result.get('负债表期末有息负债', 'N/A'):>16} 万元",
        f"   期初有息负债(估): {result.get('负债表期初有息负债(估)', 'N/A'):>16} 万元",
        f"   平均有息负债: {result.get('负债表平均有息负债', 'N/A'):>19} 万元",
        "",
        "━" * 60,
        "  【第三部分】邦得综合融资成本",
        "━" * 60,
        "",
        f"   真实利息支出: {result.get('步骤4_真实利息支出', 'N/A'):>17} 万元",
        f"   ÷ 平均有息负债: {result.get('负债表平均有息负债', 'N/A'):>16} 万元",
        f"   = 邦得综合融资成本: {result.get('邦得综合融资成本', 'N/A'):>14}",
        "",
        "━" * 60,
        "  【第四部分】明细表债务结构（已聚类）",
        "━" * 60,
        "",
        f"   总有息负债: {result.get('明细表总有息负债', 'N/A'):>21} 万元",
        "",
        f"   银行借款: {result.get('明细表银行借款余额', 'N/A'):>22} 万元"
        f"  ({result.get('明细表银行借款占比', 'N/A')})",
        f"   公开债券: {result.get('明细表公开债券余额', 'N/A'):>22} 万元"
        f"  ({result.get('明细表公开债券占比', 'N/A')})",
        f"   非标及其他: {result.get('明细表非标余额', 'N/A'):>21} 万元"
        f"  ({result.get('明细表非标占比', 'N/A')})",
        "",
        "━" * 60,
        "  【第五部分】正算加权综合融资成本",
        "━" * 60,
        "",
        f"   银行利率: {result.get('用户给定银行利率', 'N/A')}",
        f"   债券利率: {result.get('用户给定债券利率', 'N/A')}",
        f"   非标利率: 8.00%（默认，可被倒挤结果覆盖）",
        "",
        f"   正算加权综合融资成本 =",
        f"   ({result.get('明细表银行借款余额', 'N/A')} × {result.get('用户给定银行利率', 'N/A')}"
        f"    + {result.get('明细表公开债券余额', 'N/A')} × {result.get('用户给定债券利率', 'N/A')}"
        f"    + {result.get('明细表非标余额', 'N/A')} × 8.00%)",
        f"    ÷ {result.get('明细表总有息负债', 'N/A')}",
        f"    = {result.get('正算加权综合融资成本', 'N/A')}",
        "",
        "━" * 60,
        "  【第六部分】极限倒挤非标成本",
        "━" * 60,
        "",
        f"   真实利息支出: {result.get('步骤4_真实利息支出', 'N/A'):>17} 万元",
        f"   银行消耗利息: {result.get('银行消耗利息', 'N/A'):>20} 万元",
        f"   债券消耗利息: {result.get('债券消耗利息', 'N/A'):>20} 万元",
        f"   ──────────────────────────────────────────",
        f"   非标剩余利息: {result.get('非标剩余利息', 'N/A'):>20} 万元",
        f"   ÷ 非标本金: {result.get('明细表非标余额', 'N/A'):>24} 万元",
        f"   = 倒挤非标成本: {result.get('倒挤非标成本', 'N/A'):>18}",
        "",
        "━" * 60,
        "  【第七部分】风控评级",
        "━" * 60,
        "",
        f"   {result.get('风控评级', ('N/A', ''))[0]}:"
        f" {result.get('风控评级', ('', 'N/A'))[1]}",
        "",
        "=" * 60,
        "  报告结束",
        "=" * 60,
    ]

    calc_path = os.path.join(output_dir, "尽调推演计算书.txt")
    with open(calc_path, "w", encoding="utf-8") as f:
        f.write("\n".join(calc_lines))
    print(f"  [报告] 尽调推演计算书 → {calc_path}")

    # ── 邦得综合融资成本报告 ──
    bond_lines = [
        "=" * 60,
        "  邦得综合融资成本报告",
        "  生成时间: " + CURRENT_DATE.strftime("%Y-%m-%d %H:%M:%S"),
        "=" * 60,
        "",
        "━" * 60,
        "  三种口径对比",
        "━" * 60,
        "",
        "  ★ 推荐：真实总利息支出法（邦得法）",
        f"     {result.get('邦得综合融资成本', 'N/A')}",
        "     公式: 真实利息支出 / 资产负债表平均有息负债",
        "",
        "  ☆ 明细表正算加权法",
        f"     {result.get('正算加权综合融资成本', 'N/A')}",
        "     公式: ∑(各类余额 × 给定利率) / 明细表总有息负债",
        "",
        "  ☆ 极限倒挤法（仅非标）",
        f"     {result.get('倒挤非标成本', 'N/A')}",
        "     公式: (真实利息 - 银行利息 - 债券利息) / 非标本金",
        "",
        "━" * 60,
        "  关键数据快照",
        "━" * 60,
        "",
        f"  真实总利息支出: {result.get('步骤4_真实利息支出', 'N/A')} 万元",
        f"  资产负债表平均有息负债: {result.get('负债表平均有息负债', 'N/A')} 万元",
        f"  明细表总有息负债: {result.get('明细表总有息负债', 'N/A')} 万元",
        f"  明细表非标余额: {result.get('明细表非标余额', 'N/A')} 万元",
        "",
        "━" * 60,
        "  风控研判",
        "━" * 60,
        "",
        f"  {result.get('风控评级', ('N/A', ''))[0]}: {result.get('风控评级', ('', 'N/A'))[1]}",
        "",
        "=" * 60,
        "  报告结束",
        "=" * 60,
    ]

    bond_path = os.path.join(output_dir, "邦得综合融资成本报告.txt")
    with open(bond_path, "w", encoding="utf-8") as f:
        f.write("\n".join(bond_lines))
    print(f"  [报告] 邦得综合融资成本报告 → {bond_path}")

    return calc_path, bond_path


# ── 主流程 (Task A 统一入口) ────────────────────────────────────────────────

def run_task_a_full(
    audit_pdf: str,
    liability_file: str,
    bank_rate: float = 3.0,
    bond_rate: float = 5.0,
    output_path: str = "./outputs",
) -> dict:
    """Task A 完整执行流程。

    参数:
        audit_pdf: 审计报告PDF路径
        liability_file: 金融负债明细表路径 (Excel/CSV)
        bank_rate: 银行借款预估基准利率(%)
        bond_rate: 公开债券预估基准利率(%)
        output_path: 输出目录

    返回包含所有计算结果的字典。
    """
    os.makedirs(output_path, exist_ok=True)

    print("=" * 60)
    print("  穿透式融资成本核算引擎 v2.0")
    print("  Task A: 债务倒挤测算与尽调推演")
    print("=" * 60)

    # Step 1: 读取审计报告
    print("\n[1/6] 读取审计报告PDF...")
    pdf_text = read_pdf_text(audit_pdf)
    fin_data = extract_fin(pdf_text)
    print(f"      归母净利润: {fin_data.get('归母净利润', '未识别')} 万元")
    print(f"      营业收入: {fin_data.get('营业收入', '未识别')} 万元")

    # Step 2: 读取明细表
    print("\n[2/6] 读取金融负债明细表...")
    df_raw = read_liability_file(liability_file)
    df = clean_liability_data(df_raw)
    print(f"      总行数: {len(df)}")
    print(f"      识别分类: {df['标准分类'].value_counts().to_dict()}"
          if "标准分类" in df.columns else "")

    # Step 3: 核心计算
    print(f"\n[3/6] 执行穿透计算（银行利率={bank_rate}%, 债券利率={bond_rate}%）...")
    result = calculate_costs(fin_data, df, bank_rate, bond_rate)

    # Step 4: 生成报告
    print("\n[4/6] 生成文本报告...")
    generate_reports(result, output_path)

    # Step 5: 生成图表
    print("\n[5/6] 生成图表...")
    generate_pie_chart(result, os.path.join(output_path, "债务结构饼图.png"))
    generate_maturity_chart(df, os.path.join(output_path, "季度到期余额柱状图.png"))

    # Step 6: 结果汇总
    print("\n[6/6] 计算完成，核心结论：")
    print(f"  ★ 邦得综合融资成本: {result.get('邦得综合融资成本', 'N/A')}")
    print(f"  ★ 正算加权综合成本: {result.get('正算加权综合融资成本', 'N/A')}")
    print(f"  ★ 倒挤非标成本: {result.get('倒挤非标成本', 'N/A')}")
    rating = result.get("风控评级", ("", ""))
    print(f"  ★ 风控评级: {rating[0]} {rating[1]}")

    print(f"\n  所有输出文件 → {os.path.abspath(output_path)}")
    print("=" * 60)

    return result


# ============================================================================
# Task B: 三年财务OCR校验与指标计算
# ============================================================================

# ── 工具函数 ────────────────────────────────────────────────────────────────

def _guess_col_name(name: str) -> str:
    """归一化科目名称（去除空格、小写化、替换全角字符）。"""
    if not isinstance(name, str):
        return ""
    name = name.strip()
    name = name.replace(" ", "").replace("　", "")
    # 全角转半角
    result = []
    for c in name:
        code = ord(c)
        if 0xFF01 <= code <= 0xFF5E:
            result.append(chr(code - 0xFEE0))
        else:
            result.append(c)
    return "".join(result)


def _match_value(df: pd.DataFrame, col_name: str, patterns: list) -> Optional[float]:
    """在DataFrame中按科目名称列表查找数值。"""
    for pattern in patterns:
        pattern_norm = _guess_col_name(pattern)
        for col in df.columns:
            if _guess_col_name(col) == pattern_norm:
                val = df[col].iloc[0]
                try:
                    return float(str(val).replace(",", "").replace(" ", ""))
                except (ValueError, TypeError):
                    pass
    return None


def _check_balance_sheet(data: dict, year: str) -> list:
    """校验资产负债表勾稽关系。返回错误信息列表（空=通过）。"""
    errors = []
    a = data.get(f"{year}_资产总计")
    l = data.get(f"{year}_负债合计")
    e = data.get(f"{year}_所有者权益合计")
    if a and l and e:
        diff = abs(a - l - e)
        if diff > 1:
            errors.append(f"资产({a:.2f}) ≠ 负债({l:.2f}) + 权益({e:.2f}), 差异={diff:.2f}")
    return errors


def _check_income_stmt(data: dict, year: str) -> list:
    """校验利润表勾稽关系。返回错误信息列表。"""
    errors = []
    rev = data.get(f"{year}_营业收入")
    cost = data.get(f"{year}_营业成本")
    op_profit = data.get(f"{year}_营业利润")
    total_profit = data.get(f"{year}_利润总额")
    net_profit = data.get(f"{year}_净利润")
    tax = data.get(f"{year}_所得税费用")

    if rev and cost and op_profit:
        gross = rev - cost
        if gross < 0:
            errors.append(f"毛利为负: 收入{rev:.2f}, 成本{cost:.2f}")

    if total_profit and tax is not None and net_profit:
        diff = abs(total_profit - tax - net_profit)
        if diff > 1:
            errors.append(f"利润总额({total_profit:.2f}) - 所得税({tax:.2f}) "
                          f"≠ 净利润({net_profit:.2f}), 差异={diff:.2f}")
    return errors


def _ocr_correction_hints(errors: list) -> list:
    """针对OCR错误提供常见的数字混淆修正建议。"""
    hints = []
    for err in errors:
        hints.append(f"  ⚠ {err}")
        hints.append("    建议排查: 8↔3, 1↔7, 0↔O混淆, 漏小数点, 多位数字等OCR常见错误")
    return hints


# ── 核心：OCR校验 → 全科目总表 → 指标计算 ─────────────────────────────────

def run_task_b(
    pdf_files: list,
    years: list,
    out_path: str = "./outputs/ocr_analysis",
) -> dict:
    """Task B 完整执行流程。

    参数:
        pdf_files: 三个年度审计报告PDF路径列表 [year1, year2, year3]
        years: 对应的年份标签列表 ["2022", "2023", "2024"]
        out_path: 输出目录
    """
    import matplotlib.pyplot as plt

    os.makedirs(out_path, exist_ok=True)

    print("=" * 60)
    print("  三年财务OCR校验与指标计算")
    print("=" * 60)

    all_data = {}
    corrections = []  # [(year, field, old_val, new_val, reason)]
    anomalies = {}  # {field: [(year, change_pct)]}

    # ── 读取各年PDF ──
    for pdf_file, year in zip(pdf_files, years):
        print(f"\n[读取] {year} 年度报告: {os.path.basename(pdf_file)}")
        text = read_pdf_text(pdf_file)
        fin = extract_fin(text)
        all_data[year] = fin

        # 打印提取的关键数据
        print(f"  资产总计: {fin.get('资产总计', '未提取')}")
        print(f"  负债合计: {fin.get('负债合计', '未提取')}")
        print(f"  营业收入: {fin.get('营业收入', '未提取')}")
        print(f"  归母净利润: {fin.get('归母净利润', '未提取')}")

        # OCR校验
        bs_errors = _check_balance_sheet({f"{year}_{k}": v for k, v in fin.items()}, year)
        is_errors = _check_income_stmt({f"{year}_{k}": v for k, v in fin.items()}, year)

        if bs_errors:
            print(f"  [校验] 资产负债表异常:")
            corrections.extend(_ocr_correction_hints(bs_errors))
        if is_errors:
            print(f"  [校验] 利润表异常:")
            corrections.extend(_ocr_correction_hints(is_errors))
        if not bs_errors and not is_errors:
            print(f"  [校验] 勾稽关系校验通过 ✓")

    # ── 全科目总表 ──
    # 合并所有科目的完整列表
    all_subjects = set()
    for year_data in all_data.values():
        all_subjects.update(year_data.keys())

    # 排除内部key（以下划线开头）
    display_subjects = sorted(
        [s for s in all_subjects if not s.startswith("_")],
        key=lambda x: (
            0 if "利润" in x or "收入" in x or "成本" in x or "费用" in x else
            1 if "资产" in x or "负债" in x or "权益" in x else
            2 if "现金" in x or "流量" in x else 3,
            x,
        ),
    )

    rows = []
    for subj in display_subjects:
        row = [subj]
        for year in years:
            val = all_data.get(year, {}).get(subj)
            if val is not None:
                row.append(f"{val:.2f}")
            else:
                row.append("—")
        rows.append(row)

    # ── 指标计算 ──
    indicators = []

    def _get(year, key):
        return all_data.get(year, {}).get(key)

    def _avg(year, key):
        """取当年值（如有期初则平均）。"""
        val = _get(year, key)
        return val if val else 0

    def _avg2(year1_val, year2_val):
        if year1_val is not None and year2_val is not None:
            return (year1_val + year2_val) / 2
        return year2_val if year2_val is not None else 0

    for i, year in enumerate(years):
        year_indicators = {"年份": year}

        rev = _get(year, "营业收入") or 0
        cost = _get(year, "营业成本") or 0
        np_val = _get(year, "归母净利润") or _get(year, "净利润") or 0
        op_profit = _get(year, "营业利润") or 0
        total_assets = _get(year, "资产总计") or 0
        total_liab = _get(year, "负债合计") or 0
        equity = _get(year, "所有者权益合计") or 0

        # 营业收入/成本 → 毛利率
        if rev > 0:
            year_indicators["毛利率"] = f"{(rev - cost) / rev * 100:.2f}%"
            year_indicators["净利率"] = f"{np_val / rev * 100:.2f}%"
            year_indicators["营业利润率"] = f"{op_profit / rev * 100:.2f}%"
        else:
            year_indicators["毛利率"] = year_indicators["净利率"] = "N/A"

        # ROE
        if i > 0 and years[i - 1] in all_data:
            prev_eq = _get(years[i - 1], "所有者权益合计") or 0
            avg_eq = (equity + prev_eq) / 2
        else:
            avg_eq = equity
        if avg_eq > 0:
            year_indicators["ROE"] = f"{np_val / avg_eq * 100:.2f}%"
        else:
            year_indicators["ROE"] = "N/A"

        # ROA
        if i > 0 and years[i - 1] in all_data:
            prev_ta = _get(years[i - 1], "资产总计") or 0
            avg_ta = (total_assets + prev_ta) / 2
        else:
            avg_ta = total_assets
        if avg_ta > 0:
            year_indicators["ROA"] = f"{np_val / avg_ta * 100:.2f}%"
        else:
            year_indicators["ROA"] = "N/A"

        # 资产负债率
        if total_assets > 0:
            year_indicators["资产负债率"] = f"{total_liab / total_assets * 100:.2f}%"
        else:
            year_indicators["资产负债率"] = "N/A"

        # 营运能力（需要前一年期初）
        if i > 0 and years[i - 1] in all_data:
            prev_ar = _get(years[i - 1], "应收账款") or 0
            prev_inv = _get(years[i - 1], "存货") or 0
            prev_ca = _get(years[i - 1], "流动资产合计") or 0
            curr_ar = _get(year, "应收账款") or 0
            curr_inv = _get(year, "存货") or 0
            curr_ca = _get(year, "流动资产合计") or 0

            if (curr_ar + prev_ar) / 2 > 0 and rev > 0:
                year_indicators["应收账款周转率"] = f"{rev / ((curr_ar + prev_ar) / 2):.2f}"
            if (curr_inv + prev_inv) / 2 > 0 and cost > 0:
                year_indicators["存货周转率"] = f"{cost / ((curr_inv + prev_inv) / 2):.2f}"
            if (curr_ca + prev_ca) / 2 > 0 and rev > 0:
                year_indicators["流动资产周转率"] = f"{rev / ((curr_ca + prev_ca) / 2):.2f}"
            if (total_assets + prev_ta) / 2 > 0 and rev > 0:
                year_indicators["总资产周转率"] = f"{rev / ((total_assets + prev_ta) / 2):.2f}"

        # 流动/速动比率
        curr_liab = _get(year, "流动负债合计") or 0
        inventory = _get(year, "存货") or 0
        prepay = _get(year, "预付款项") or 0
        if curr_liab > 0 and curr_ca > 0:
            year_indicators["流动比率"] = f"{curr_ca / curr_liab:.2f}"
            year_indicators["速动比率"] = f"{(curr_ca - inventory - prepay) / curr_liab:.2f}"

        indicators.append(year_indicators)

        # ── 异动识别（同比变化 > 30%） ──
        if i > 0 and years[i - 1] in all_data:
            prev_year = years[i - 1]
            for subj in ["营业收入", "营业成本", "归母净利润", "净利润",
                          "资产总计", "负债合计", "应收账款", "存货",
                          "短期借款", "长期借款", "应付债券"]:
                curr_val = _get(year, subj)
                prev_val = _get(prev_year, subj)
                if curr_val and prev_val and prev_val != 0:
                    change = (curr_val - prev_val) / abs(prev_val) * 100
                    if abs(change) > 30:
                        if subj not in anomalies:
                            anomalies[subj] = []
                        anomalies[subj].append((year, change))

    # ── 输出《OCR数据校验与修正报告》──
    ocr_lines = [
        "=" * 60,
        "  OCR数据校验与修正报告",
        "=" * 60,
        f"  报告生成: {CURRENT_DATE.strftime('%Y-%m-%d %H:%M:%S')}",
        f"  覆盖年度: {', '.join(years)}",
        "",
    ]
    if corrections:
        ocr_lines.append("━" * 60)
        ocr_lines.append("  发现的问题与修正建议")
        ocr_lines.append("━" * 60)
        ocr_lines.extend(corrections)
    else:
        ocr_lines.append("  校验通过，未发现明显的OCR逻辑错误 ✓")
    ocr_lines.append("")
    ocr_lines.append("=" * 60)
    ocr_lines.append("  报告结束")
    ocr_lines.append("=" * 60)

    ocr_path = os.path.join(out_path, "OCR财务分析报告.txt")
    with open(ocr_path, "w", encoding="utf-8") as f:
        f.write("\n".join(ocr_lines))
    print(f"\n[输出] OCR报告 → {ocr_path}")

    # ── 输出《全科目财务数据合并总表》── (print to console for now)
    print("\n" + "=" * 60)
    print("  全科目财务数据合并总表")
    print("=" * 60)
    header = f"{'科目名称':<30}" + "".join(f"{y:>18}" for y in years)
    print(header)
    print("-" * len(header))
    for row in rows:
        print(f"{row[0]:<30}" + "".join(f"{v:>18}" for v in row[1:]))
    print("-" * len(header))

    # ── 输出《核心财务指标计算表》──
    print("\n" + "=" * 60)
    print("  核心财务指标计算表")
    print("=" * 60)
    if indicators:
        all_keys = list(indicators[0].keys())
        header = f"{'指标':<20}" + "".join(f"{y:>18}" for y in years)
        print(header)
        print("-" * len(header))
        for key in all_keys:
            if key == "年份":
                continue
            row_str = f"{key:<20}"
            for ind in indicators:
                row_str += f"{ind.get(key, 'N/A'):>18}"
            print(row_str)

    # ── 输出《财务异动简评》──
    print("\n" + "=" * 60)
    print("  财务异动简评")
    print("=" * 60)
    if anomalies:
        for subj, changes in sorted(anomalies.items()):
            for year, change in changes:
                direction = "↑" if change > 0 else "↓"
                print(f"  {subj}: {year}年 {direction} {abs(change):.1f}%")
    else:
        print("  未发现同比变动超过30%的异常科目。")

    # ── 输出Excel ──
    try:
        excel_path = os.path.join(out_path, "财务分析结果.xlsx")
        with pd.ExcelWriter(excel_path, engine="xlsxwriter") as writer:
            # Sheet 1: OCR修正清单
            if corrections:
                corr_df = pd.DataFrame({"修正建议": corrections})
                corr_df.to_excel(writer, sheet_name="OCR修正清单", index=False)

            # Sheet 2: 全科目总表
            subjects_df = pd.DataFrame(rows, columns=["科目名称"] + years)
            subjects_df.to_excel(writer, sheet_name="全科目总表", index=False)

            # Sheet 3: 财务指标
            ind_df = pd.DataFrame(indicators)
            ind_df.to_excel(writer, sheet_name="财务指标", index=False)

            # Sheet 4: 异动简评
            anom_rows = []
            for subj, changes in sorted(anomalies.items()):
                for year, change in changes:
                    anom_rows.append({"科目": subj, "年度": year, "变动幅度": f"{change:.1f}%"})
            if anom_rows:
                anom_df = pd.DataFrame(anom_rows)
            else:
                anom_df = pd.DataFrame({"信息": ["未发现明显异动（变动>30%）"]})
            anom_df.to_excel(writer, sheet_name="异动简评", index=False)

        print(f"\n[输出] Excel报告 → {excel_path}")
    except Exception as e:
        print(f"\n[警告] Excel输出失败: {e}")

    print("\n" + "=" * 60)
    print("  Task B 执行完成")
    print("=" * 60)

    return {
        "ocr_report": ocr_path,
        "excel_report": os.path.join(out_path, "财务分析结果.xlsx"),
        "indicators": indicators,
        "anomalies": anomalies,
        "corrections": corrections,
        "data": all_data,
    }


# ============================================================================
# 命令行入口
# ============================================================================

if __name__ == "__main__":
    import sys

    print("""
╔══════════════════════════════════════════════════════════╗
║      财务尽调分析工具包 (Financial Due Diligence Toolkit)    ║
╠══════════════════════════════════════════════════════════╣
║  Task A: 债务倒挤测算与尽调推演                           ║
║  Task B: 三年财务OCR校验与指标计算                        ║
╚══════════════════════════════════════════════════════════╝
""")
    print("请使用 run_task_a_full() 或 run_task_b() 函数执行分析。")
    print("示例:")
    print("  from scripts.financial_analysis import run_task_a_full")
    print("  result = run_task_a_full(")
    print('      audit_pdf="审计报告.pdf",')
    print('      liability_file="金融负债明细表.xlsx",')
    print("      bank_rate=3.0, bond_rate=5.0,")
    print('      output_path="./outputs"')
    print("  )")
