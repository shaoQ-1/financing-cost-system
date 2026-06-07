"""
OCR 数据校验与 AI 辅助修正引擎。

针对从 Excel (OCR/手动) 导入的三年财务数据:
  1. 本地勾稽校验 — 资产负债表平衡、利润表链条、三年异动
  2. 数字合理性检测 — 小数点错位、量级异常
  3. DeepSeek AI 辅助修正 — 提示勾稽异常，返回修正建议
"""

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── 关键勾稽等式 ──

def _check_bs_balance(data: dict, year: str) -> list[dict]:
    """资产负债表三大勾稽检查。"""
    issues = []
    d = data.get(year, {})

    ta = d.get("资产总计")
    tl = d.get("负债合计")
    eq = d.get("所有者权益合计")
    ca = d.get("流动资产合计")
    nca = d.get("非流动资产合计")
    cl = d.get("流动负债合计")
    ncl = d.get("非流动负债合计")

    # 资产 = 流动资产 + 非流动资产
    if ta is not None and ca is not None and nca is not None:
        diff = abs(ta - (ca + nca))
        if diff > 0.01 and ta > 0:
            ratio = diff / ta
            if ratio > 0.01:
                issues.append({
                    "year": year, "type": "bs_subtotal",
                    "desc": f"资产总计({ta:.2f}) ≠ 流动资产合计({ca:.2f}) + 非流动资产合计({nca:.2f})，差额{diff:.2f}(占比{ratio*100:.1f}%)",
                    "severity": "high" if ratio > 0.05 else "medium",
                    "involved": ["资产总计", "流动资产合计", "非流动资产合计"],
                    "expected": ca + nca,
                })

    # 负债 + 权益 = 资产
    if ta is not None and tl is not None and eq is not None:
        diff = abs(ta - (tl + eq))
        if diff > 0.01 and ta > 0:
            ratio = diff / ta
            if ratio > 0.01:
                issues.append({
                    "year": year, "type": "bs_balance",
                    "desc": f"资产总计({ta:.2f}) ≠ 负债合计({tl:.2f}) + 所有者权益({eq:.2f})，差额{diff:.2f}(占比{ratio*100:.1f}%)",
                    "severity": "high" if ratio > 0.05 else "medium",
                    "involved": ["资产总计", "负债合计", "所有者权益合计"],
                    "expected": tl + eq,
                })

    # 负债 = 流动负债 + 非流动负债
    if tl is not None and cl is not None and ncl is not None:
        diff = abs(tl - (cl + ncl))
        if diff > 0.01 and tl > 0:
            ratio = diff / tl
            if ratio > 0.01:
                issues.append({
                    "year": year, "type": "liability_subtotal",
                    "desc": f"负债合计({tl:.2f}) ≠ 流动负债合计({cl:.2f}) + 非流动负债合计({ncl:.2f})，差额{diff:.2f}",
                    "severity": "medium",
                    "involved": ["负债合计", "流动负债合计", "非流动负债合计"],
                    "expected": cl + ncl,
                })

    return issues


def _check_is_chain(data: dict, year: str) -> list[dict]:
    """利润表价值链检查。"""
    issues = []
    d = data.get(year, {})

    rev = d.get("营业收入")
    cost = d.get("营业成本")
    op = d.get("营业利润")
    total_p = d.get("利润总额")
    tax = d.get("所得税费用")
    np_val = d.get("净利润")
    parent_np = d.get("归母净利润")
    minority = d.get("少数股东损益")

    # 毛利润检验（不一定严格，但异常标识）
    if rev and cost and rev > 0:
        gross = rev - cost
        if gross < 0:
            issues.append({
                "year": year, "type": "gross_loss",
                "desc": f"毛利润为负: 营业收入({rev:.2f}) < 营业成本({cost:.2f})",
                "severity": "medium",
                "involved": ["营业收入", "营业成本"],
                "expected": None,
            })

    # 营业利润 → 利润总额 → 净利润 链条
    if op is not None and total_p is not None:
        diff = abs(total_p - op) if total_p != 0 else 0
        if diff > 0 and op != 0 and diff / abs(op) < 0.3:
            pass  # 有营业外收支是正常的
        elif diff > 0 and op != 0 and diff / abs(op) > 0.3:
            issues.append({
                "year": year, "type": "is_chain_nonop",
                "desc": f"营业利润({op:.2f})到利润总额({total_p:.2f})差异大({diff:.2f})，营业外收支可能异常",
                "severity": "low",
                "involved": ["营业利润", "利润总额"],
                "expected": None,
            })

    if total_p is not None and tax is not None and np_val is not None:
        expected_np = total_p - tax
        if total_p != 0:
            diff = abs(np_val - expected_np)
            ratio = diff / abs(total_p)
            if ratio > 0.02:
                issues.append({
                    "year": year, "type": "is_chain_np",
                    "desc": f"净利润({np_val:.2f}) ≠ 利润总额({total_p:.2f}) - 所得税({tax:.2f}) = {expected_np:.2f}，差额{diff:.2f}(占比{ratio*100:.1f}%)",
                    "severity": "high" if ratio > 0.05 else "medium",
                    "involved": ["利润总额", "所得税费用", "净利润"],
                    "expected": expected_np,
                })

    # 归母净利润 + 少数股东损益 = 净利润
    if parent_np is not None and minority is not None and np_val is not None:
        diff = abs(np_val - (parent_np + minority))
        ratio = diff / abs(np_val) if np_val != 0 else diff
        if ratio > 0.02:
            issues.append({
                "year": year, "type": "is_chain_minority",
                "desc": f"净利润({np_val:.2f}) ≠ 归母净利润({parent_np:.2f}) + 少数股东损益({minority:.2f})，差额{diff:.2f}",
                "severity": "medium",
                "involved": ["净利润", "归母净利润", "少数股东损益"],
                "expected": parent_np + minority if parent_np is not None and minority is not None else None,
            })

    return issues


def _check_yoy_anomalies(data: dict, years: list[str]) -> list[dict]:
    """三年同比异常跳动检测。"""
    issues = []
    watch_keys = [
        "营业收入", "营业成本", "净利润", "归母净利润",
        "资产总计", "负债合计", "所有者权益合计",
        "应收账款", "存货", "短期借款", "长期借款", "应付债券",
        "货币资金", "固定资产", "财务费用",
    ]

    for i, year in enumerate(years):
        if i == 0:
            continue
        prev_year = years[i - 1]
        for key in watch_keys:
            cv = data.get(year, {}).get(key)
            pv = data.get(prev_year, {}).get(key)
            if cv is None or pv is None or pv == 0:
                continue
            ratio = abs((cv - pv) / pv)
            if ratio > 0.5:
                chg = (cv - pv) / pv * 100
                issues.append({
                    "year": year, "type": "yoy_jump",
                    "desc": f"{key}: {prev_year}年{pv:.2f} → {year}年{cv:.2f}，变动{chg:+.1f}%",
                    "severity": "high" if ratio > 1.0 else "medium",
                    "involved": [key],
                    "expected": None,
                    "prev_year": prev_year,
                    "prev_val": pv,
                })

    return issues


def _check_magnitude_errors(data: dict, years: list[str]) -> list[dict]:
    """数字量级异常检测（典型OCR小数点错位）。"""
    issues = []

    # 典型量级对照: 各指标期望的量级范围（万元）
    magnitude_ranges = {
        "营业收入": (1000, 10000000),
        "营业成本": (1000, 10000000),
        "资产总计": (10000, 100000000),
        "负债合计": (10000, 100000000),
        "所有者权益合计": (1000, 100000000),
        "净利润": (-1000000, 1000000),
        "货币资金": (100, 10000000),
        "应收账款": (100, 10000000),
        "存货": (100, 10000000),
        "短期借款": (100, 10000000),
        "长期借款": (100, 10000000),
        "应付债券": (100, 10000000),
        "固定资产": (100, 10000000),
        "实收资本": (1000, 10000000),
    }

    for year in years:
        d = data.get(year, {})
        for key, (lo, hi) in magnitude_ranges.items():
            val = d.get(key)
            if val is None or val == 0:
                continue

            # 明显超出范围 → 可能是单位错误（元 vs 万元）或小数点错位
            if abs(val) > hi * 100:
                issues.append({
                    "year": year, "type": "magnitude_too_large",
                    "desc": f"{key}={val:.2f}，可能多录入了一位数字或单位为元而非万元",
                    "severity": "high",
                    "involved": [key],
                    "expected": val / 10 if abs(val/10) <= hi else (val / 100 if abs(val/100) <= hi else val / 10000),
                })
            elif abs(val) < lo / 10 and val != 0:
                issues.append({
                    "year": year, "type": "magnitude_too_small",
                    "desc": f"{key}={val:.2f}，数值偏小，可能少录入了一位数字",
                    "severity": "medium",
                    "involved": [key],
                    "expected": val * 10 if abs(val*10) <= hi else (val * 100 if abs(val*100) <= hi else None),
                })

    return issues


def _check_bs_continuity(data: dict, years: list[str]) -> list[dict]:
    """资产负债表科目跨年延续性检查：次年期初应等于上年期末。"""
    issues = []
    # 资产负债表科目（存量科目）
    bs_keys = [
        "短期借款", "长期借款", "应付债券", "应收账款", "存货",
        "货币资金", "固定资产", "资产总计", "负债合计", "所有者权益合计",
        "流动资产合计", "流动负债合计", "非流动资产合计", "非流动负债合计",
        "实收资本", "资本公积", "盈余公积", "未分配利润", "应付账款",
        "预付款项", "其他应收款", "其他应付款", "一年内到期的非流动负债",
    ]

    for i, year in enumerate(years):
        if i == 0:
            continue
        prev_year = years[i - 1]
        for key in bs_keys:
            cv = data.get(year, {}).get(key)
            pv = data.get(prev_year, {}).get(key)
            if cv is None or pv is None or pv == 0 or cv == 0:
                continue
            ratio = cv / pv
            if ratio > 3 or ratio < 0.33:
                chg = (cv - pv) / abs(pv) * 100
                issues.append({
                    "year": year, "type": "bs_continuity",
                    "desc": f"{key}: {prev_year}年末({pv:.2f}) → {year}年({cv:.2f})，变动{chg:+.1f}%，"
                            f"次年期初应等于上年期末，超3倍变动可能为数据错误",
                    "severity": "high" if ratio > 5 or ratio < 0.2 else "medium",
                    "involved": [key],
                    "expected": pv,
                    "prev_year": prev_year,
                    "prev_val": pv,
                })

    return issues


def _detect_common_ocr_errors(original: dict, corrected: dict) -> list[dict]:
    """对比原始值与修正后，标记被 AI 改动的项。"""
    changes = []
    for year in corrected:
        for key, new_val in corrected[year].items():
            orig_val = original.get(year, {}).get(key)
            if orig_val is not None and abs(orig_val - new_val) / max(abs(orig_val), 0.01) > 0.001:
                changes.append({
                    "year": year,
                    "key": key,
                    "original": orig_val,
                    "corrected": new_val,
                    "diff": new_val - orig_val,
                })
    return changes


# ── 主入口 ──

def validate_and_report(data: dict, years: list[str]) -> dict:
    """纯本地校验，返回所有问题。"""
    all_issues = []

    for year in years:
        all_issues.extend(_check_bs_balance(data, year))
        all_issues.extend(_check_is_chain(data, year))

    all_issues.extend(_check_yoy_anomalies(data, years))
    all_issues.extend(_check_magnitude_errors(data, years))
    all_issues.extend(_check_bs_continuity(data, years))

    # 按 severity 分组
    high = [i for i in all_issues if i["severity"] == "high"]
    medium = [i for i in all_issues if i["severity"] == "medium"]
    low = [i for i in all_issues if i["severity"] == "low"]

    return {
        "issues": all_issues,
        "summary": {
            "total": len(all_issues),
            "high": len(high),
            "medium": len(medium),
            "low": len(low),
        },
        "has_issues": len(all_issues) > 0,
    }


def ai_correct(data: dict, years: list[str],
               validation: dict,
               api_key: str, base_url: str = "https://api.deepseek.com/v1") -> Optional[dict]:
    """调用 DeepSeek 对异常数据进行 AI 修正。

    data: {year: {科目名: 数值}}
    validation: validate_and_report() 的输出

    返回: {
        "corrections": {year: {科目名: 修正值}},
        "changes": [每项改动的描述],
        "confidence": "high"/"medium"/"low",
    }
    """
    if not api_key:
        return None
    if not validation.get("has_issues"):
        return {"corrections": {}, "changes": [], "confidence": "high"}

    try:
        from openai import OpenAI
    except ImportError:
        return None

    issues_text = "\n".join(
        f"[{i['severity'].upper()}] {i['year']}: {i['desc']}"
        for i in validation["issues"]
    )

    # 构建三年数据摘要
    data_lines = []
    all_keys = sorted({k for y in years for k in data.get(y, {})})
    data_lines.append("科目\t" + "\t".join(years))
    for key in all_keys:
        vals = [str(data.get(y, {}).get(key, "")) for y in years]
        data_lines.append(f"{key}\t" + "\t".join(vals))
    data_table = "\n".join(data_lines)

    prompt = f"""你是一位中国注册会计师(CPA)，专注于财务数据质量审核。

用户通过OCR扫描/手工录入了一份企业的三年财务数据。系统自动检测到以下问题需要你判断和修正：

检测到的问题：
{issues_text}

原始三年数据（单位：元）：
{data_table}

请根据以下原则判断并修正数据：

1. **勾稽关系优先**：资产负债表必须平衡（资产=负债+权益），利润表链条必须吻合
2. **跨年延续性**：次年期初余额必须等于上年期末余额（资产负债表存量科目）。如果某科目第N+1年数值与第N年差异超过3倍，可能为数据错误，应优先参考第N年期末值修正
3. **OCR小数点错位**：如果一项数据比其他年份同科目或同一年份关联科目大/小了约10/100/1000倍，极可能是小数点错位
4. **单位强制为元**：所有数值单位为元，如果发现数据实际为万元（偏小约10000倍），应放大10000倍
5. **不要无依据修改**：如数据看起来合理但仅有轻微差异(<2%)，保持原值

输出JSON格式（只返回JSON，不要其他文字）：
{{
  "corrections": {{
    "2022": {{"科目名": 修正值}},
    "2023": {{"科目名": 修正值}},
    "2024": {{"科目名": 修正值}}
  }},
  "changes": [
    {{"year": "2022", "key": "科目名", "from": 原值, "to": 修正值, "reason": "修正原因说明"}}
  ],
  "confidence": "high/medium/low",
  "notes": "其他说明"
}}

只输出修正值有变动的科目。未修正的科目不要包含在corrections中。
"""

    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "你是一位专业的中国注册会计师，擅长财务报表数据质量审核和修正。只返回JSON。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
            timeout=60,
        )
        text = resp.choices[0].message.content
        # 清理 markdown 代码块
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]

        result = json.loads(text.strip())

        # 补充统计变更明细
        raw_changes = result.get("changes", [])
        result["changes_count"] = len(raw_changes)
        return result

    except Exception as e:
        logger.warning(f"AI correction failed: {e}")
        return None


def apply_corrections(data: dict, corrections: dict) -> dict:
    """将修正结果应用到原数据，返回新数据。"""
    result = {year: dict(vals) for year, vals in data.items()}
    for year, corrs in corrections.items():
        if year not in result:
            continue
        for key, val in corrs.items():
            if val is not None:
                result[year][key] = val
    return result
