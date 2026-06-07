#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
穿透式融资成本核算系统
============================================

用法:
  python main.py --help

示例:
  python main.py task_a --audit 审计报告.pdf --liability 金融负债明细表.xlsx --bank-rate 3.0 --bond-rate 5.0
  python main.py task_b --pdfs 2022.pdf 2023.pdf 2024.pdf --years 2022 2023 2024
"""

import argparse
import sys
import os

# 确保 scripts 目录在路径中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scripts.financial_analysis import run_task_a_full, run_task_b


def main():
    parser = argparse.ArgumentParser(
        description="穿透式融资成本核算系统 v2.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  Task A — 债务倒挤测算:
    python main.py task_a --audit 审计报告.pdf --liability 明细表.xlsx -b 3.0 -B 5.0

  Task B — 三年财务OCR校验:
    python main.py task_b --pdfs 2022.pdf 2023.pdf 2024.pdf --years 2022 2023 2024
        """,
    )
    subparsers = parser.add_subparsers(dest="task", help="选择分析任务")

    # Task A
    parser_a = subparsers.add_parser("task_a", help="债务倒挤测算与尽调推演")
    parser_a.add_argument("--audit", required=True, help="审计报告PDF路径")
    parser_a.add_argument("--liability", required=True, help="金融负债明细表路径 (Excel/CSV)")
    parser_a.add_argument("-b", "--bank-rate", type=float, default=3.0, help="银行借款预估利率% (默认 3.0)")
    parser_a.add_argument("-B", "--bond-rate", type=float, default=5.0, help="公开债券预估利率% (默认 5.0)")
    parser_a.add_argument("-o", "--output", default="./outputs", help="输出目录 (默认 ./outputs)")

    # Task B
    parser_b = subparsers.add_parser("task_b", help="三年财务OCR校验与指标计算")
    parser_b.add_argument("--pdfs", nargs=3, required=True, metavar=("PDF1", "PDF2", "PDF3"),
                          help="三个年度的审计报告PDF")
    parser_b.add_argument("--years", nargs=3, required=True, metavar=("Y1", "Y2", "Y3"),
                          help="对应的三个年份标签")
    parser_b.add_argument("-o", "--output", default="./outputs/ocr_analysis",
                          help="输出目录 (默认 ./outputs/ocr_analysis)")

    args = parser.parse_args()

    if args.task == "task_a":
        # 打印文件信息
        for path, label in [(args.audit, "审计报告"), (args.liability, "明细表")]:
            if not os.path.exists(path):
                print(f"错误: {label}文件不存在: {path}")
                sys.exit(1)
            print(f"  {label}: {path} ({os.path.getsize(path) / 1024:.1f} KB)")

        run_task_a_full(
            audit_pdf=args.audit,
            liability_file=args.liability,
            bank_rate=args.bank_rate,
            bond_rate=args.bond_rate,
            output_path=args.output,
        )

    elif args.task == "task_b":
        for pdf in args.pdfs:
            if not os.path.exists(pdf):
                print(f"错误: 文件不存在: {pdf}")
                sys.exit(1)

        run_task_b(
            pdf_files=list(args.pdfs),
            years=list(args.years),
            out_path=args.output,
        )

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
