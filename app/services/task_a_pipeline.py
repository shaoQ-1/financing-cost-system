"""
Task A 编排：计算→图表→报告。
复用 financial_analysis.py 全部计算逻辑。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from scripts.financial_analysis import (
    read_liability_file,
    clean_liability_data,
    calculate_costs,
    generate_reports,
    generate_pie_chart,
    generate_maturity_chart,
)


def run_task_a_web(fin_data: dict,
                   liability_path: str,
                   bank_rate: float,
                   bond_rate: float,
                   output_dir: str) -> dict:
    """Task A Web 版封装。

    fin_data: 兼容 extract_fin() 输出格式的 dict（来自 DeepSeek mapped_keys）
    liability_path: 金融负债明细表路径
    返回: calculate_costs() 的结果 dict
    """
    os.makedirs(output_dir, exist_ok=True)

    # 读取并清洗明细表
    df = read_liability_file(liability_path)
    df = clean_liability_data(df)

    # 核心计算
    result = calculate_costs(fin_data, df, bank_rate, bond_rate)

    # 生成报告
    generate_reports(result, output_dir)

    # 生成图表
    generate_pie_chart(result, os.path.join(output_dir, "债务结构饼图.png"))
    generate_maturity_chart(df, os.path.join(output_dir, "季度到期余额柱状图.png"))

    # === 新增：第6步 不依赖明细表的综合融资成本 ===
    # 邦得综合融资成本已经在 result 中（使用五步法/资产负债表平均有息负债）
    # 但还要计算一个"纯报表倒推综合成本"（只用审计报告，完全不依赖明细表）
    # 这个实际上就是邦得法本身：真实利息/平均有息负债
    # 同时加入一个"利息费用法"（直接用利润表利息费用/平均有息负债）

    fin = fin_data
    bs_short = fin.get("短期借款", 0) or 0
    bs_long = fin.get("长期借款", 0) or 0
    bs_bond = fin.get("应付债券", 0) or 0
    bs_maturing = fin.get("一年内到期非流动负债", 0) or 0
    bs_debt_e = bs_short + bs_long + bs_bond + bs_maturing
    bs_debt_b = fin.get("期初有息负债", bs_debt_e * 0.9)
    avg_debt = (bs_debt_e + bs_debt_b) / 2

    # 方法1：邦得法（五步法真实利息）
    real_interest = float(result.get("步骤4_真实利息支出", "0").replace(",", ""))
    bond_cost = real_interest / avg_debt * 100 if avg_debt > 0 else 0

    # 方法2：费用化利息法（只用利润表利息支出）
    fee_interest = fin.get("利息支出_费用化", 0) or 0
    fee_cost = fee_interest / avg_debt * 100 if avg_debt > 0 else 0

    # 方法3：明细表正算法（已有）
    forward_cost = float(result.get("正算加权综合融资成本", "0%").replace("%", ""))

    # 方法4：倒挤非标法（已有）
    squeeze_cost_str = result.get("倒挤非标成本", "N/A")

    no_liability_results = {
        "邦得法（五步法真实利息）": f"{bond_cost:.4f}%",
        "费用化利息法（利润表利息支出）": f"{fee_cost:.4f}%",
        "明细表正算法（银行+债券+非标）": result.get("正算加权综合融资成本", "N/A"),
        "极限倒挤非标法": squeeze_cost_str,
        "真实利息支出": f"{real_interest:.2f}",
        "费用化利息支出": f"{fee_interest:.2f}",
        "资产负债表平均有息负债": f"{avg_debt:.2f}",
    }

    result["不依赖明细表综合成本"] = no_liability_results
    result["_no_liab_avg_debt"] = avg_debt
    result["_no_liab_real_interest"] = real_interest
    result["_no_liab_bond_cost"] = bond_cost
    result["_no_liab_fee_cost"] = fee_cost

    return result
