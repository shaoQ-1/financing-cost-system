# 穿透式融资成本核算系统

## 项目概述
基于审计报告PDF和金融负债明细表，自动计算企业真实融资成本的财务尽调工具。
提供 Web 界面（Docker 部署）和 CLI 两种使用方式。

## 架构

```
用户浏览器 (Bootstrap + htmx)
    ↓
Flask Web App (端口 5120)
    ↓
三层 PDF 提取引擎:
  ① text_pdf (pdfplumber)   — 文本型PDF，免费
  ② ocr_pdf  (PaddleOCR)    — 扫描件中文OCR，免费
  ③ ai_pdf   (DeepSeek API) — AI 视觉兜底
    ↓
核心计算引擎 (financial_analysis.py)
    → 五步勾稽法 → 三种口径融资成本 → 图表 → 报告
```

## 核心功能
- **Task B**: 三年财务分析（上传PDF → 选页 → AI提取 → 24项指标 → 异动识别 → Excel）
- **Task A**: 债务成本测算（上传明细表 → 三种口径成本 → 倒挤非标 → 图表报告）
  - ✅ 不依赖明细表：邦得法（五步法真实利息/平均负债）
  - ✅ 依赖明细表：正算法（加权） + 倒挤非标法

## 项目结构
```
MyProjects/
├── app/                          # Flask Web 应用
│   ├── __init__.py               # 应用工厂
│   ├── state.py                  # 会话状态机
│   ├── routes/
│   │   ├── web.py                # 页面路由（Bootstrap + htmx）
│   │   └── api.py                # JSON 端点
│   ├── services/
│   │   ├── session_store.py      # 文件型会话管理
│   │   ├── pdf_utils.py          # PyMuPDF 页面渲染
│   │   ├── pdf_extractor.py      # 三层 PDF 提取引擎
│   │   ├── deepseek.py           # DeepSeek API 客户端
│   │   ├── task_b_pipeline.py    # Task B 编排
│   │   └── task_a_pipeline.py    # Task A 编排
│   └── templates/                # Bootstrap 5 + htmx 页面
├── scripts/
│   └── financial_analysis.py     # 核心分析引擎（所有计算逻辑）
├── main.py                       # CLI 入口
├── Dockerfile                    # 容器构建（端口 5120）
├── docker-compose.yml            # FNOS 部署配置
└── requirements.txt
```

## 部署（Docker / FNOS）

```bash
# 1. 创建 .env 文件
echo "DEEPSEEK_API_KEY=sk-your-key" > .env

# 2. 构建并启动（使用 Docker 镜像源加速）
docker compose up -d --build

# 3. 访问 http://nas-ip:5120
```

## Docker 构建注意事项

- **镜像源**：Dockerfile 中使用 `docker.1ms.run` 镜像源加速拉取基础镜像
- **代理**：构建时如需代理（pip install / apt-get），已在 Dockerfile 内配置
- **强制重新构建**（不使用缓存）：`docker compose build --no-cache && docker compose up -d`

## 使用流程（Web）

```
Step B1: 上传 3 年审计报告 PDF
Step B2: 选择三种提取模式（自动/文本/OCR/AI）+ 选三表页码
Step B3: 预览 DeepSeek 提取结果，可手动修正
Step B4: 查看财务指标 + 异动，下载 Excel
         ↓
Step A1: 上传金融负债明细表
Step A2: 输入银行/债券利率
Step A3: 查看四种口径融资成本 + 图表 + 下载报告
```

## PDF 提取引擎

| 模式 | 工具 | 速度 | 费用 | 适用场景 |
|------|------|------|------|---------|
| 文本 | pdfplumber/pdfminer | 秒级 | 免费 | 财务软件生成的文本PDF |
| OCR | PaddleOCR | 数秒 | 免费 | 扫描件（需安装 paddlepaddle） |
| AI | DeepSeek Vision | 30-60秒 | ~$0.03 | 复杂排版/低质量扫描（兜底） |
| 自动 | text→OCR→AI 自动降级 | 可变 | 最小化 | 推荐，兼顾速度与质量 |

## CLI 方式（备用）

```bash
# Task A: 债务测算
python main.py task_a --audit 审计报告.pdf --liability 明细表.xlsx -b 3.0 -B 5.0

# Task B: 三年财务分析
python main.py task_b --pdfs 2022.pdf 2023.pdf 2024.pdf --years 2022 2023 2024
```

## 四种融资成本口径

| 方法 | 明细表 | 数据源 | 公式 |
|------|--------|--------|------|
| 邦得法 | ❌ 不需要 | 审计报告 | 真实利息 / 平均有息负债 |
| 费用化法 | ❌ 不需要 | 利润表利息支出 | 利息支出 / 平均有息负债 |
| 正算法 | ✅ 需要 | 明细表 | ∑(各类余额×利率) / 总有息负债 |
| 倒挤法 | ✅ 需要 | 两者结合 | (真实利息-银行利息-债券利息)/非标本金 |
