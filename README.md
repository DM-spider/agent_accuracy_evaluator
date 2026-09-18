# 智能体数值准确性测评

本地测评页面：批量执行黄金集中的确定数值题，实时（或 Mock）调用智能体与 SQL，再由配置在 `settings.toml` 的 OpenAI-compatible 评估 LLM 对「题目契约 + 智能体原文 + SQL 基准」做结构化评估。

2026-09-18 起，结果对比由 **单一的 LLM 评估链路** 完成：正常数值题只产生一套自动结论，规则逐格比较、字段映射表和旧抽取值链路已移除。人工平反仍覆盖自动总体结论。

- 题目通过率：智能体已生成结果题数 / 计划测评题数（分母为开跑即固定的选题清单，不随完成数增长）
- 准确率：`final_verdict=QUALIFIED` 题数 / 可评估题数（部分作答不计入分子）
- 可评估率：有有效 LLM 结论或已人工平反的题数 / 已完成题数
- v3 专题通过率：`CX` / `LS` / `DMA` / `JL` / `BJ` 五类前缀的合格率（`metrics.category_pass_rate`）
- 七维一致率：期间 / 范围 / 粒度 / 字段 / 行覆盖 / 数值 / 单位口径，按题计数，UNKNOWN 与 NA 不进分母

## 评估链路

    智能体调用 ─┐
                ├─> 原始证据落盘 ─> 构建 LLM 证据包 ─> LLM 严格 JSON 评估
    SQL 查询 ───┘                                      │
                                                       v
                                      Schema 校验 + 数值容差复核
                                                       │
                                                       v
                                      llm_evaluation.json 持久化
                                          ├─> 详情页结果对比
                                          └─> 批次指标聚合

- 智能体调用失败、SQL 失败、水位变化、非数值题：不调用 LLM，直接标记无法评估 / 不评分。
- LLM 调用失败、返回非法 JSON、证据包超过上限：保存 `UNEVALUABLE` 与对应问题码，**不回退旧规则**。
- 详情页 GET、批次 GET 和指标聚合只读取持久化结果，不会触发 LLM 网络请求。
- 程序只做结构校验、数值容差复核、持久化和聚合，不产生第二套规则比较结论。

题目总体结论：`QUALIFIED`（合格）/ `PARTIAL`（部分作答）/ `UNQUALIFIED`（不合格）/ `UNEVALUABLE`（无法评估）。
固定诊断维度：`period`、`scope`、`grain`、`field_coverage`、`row_coverage`、`numeric_accuracy`、`unit_caliber`。
主要问题码优先级：`PERIOD_MISMATCH > SCOPE_MISMATCH > GRAIN_MISMATCH > CALIBER_MISMATCH > UNIT_MISMATCH > MISSING_FIELD > MISSING_ROW > WRONG_VALUE > CONTRADICTORY_TEXT`。

## 配置

所有生产配置和凭证只从 `config/settings.toml` 读取，**不支持环境变量覆盖**。`config/settings.toml` 含本机凭证，已被 Git 忽略；`runtime/` 运行产物同样不入库，启动时自动创建。

```toml
[app]
host = "127.0.0.1"
port = 8765
timezone = "Asia/Shanghai"
contract_version = "v3"   # v1 加载 config/contracts.json；v3 加载 config/contracts_v3.json
open_browser = true

[paths]
golden_dir = ""    # 留空 = 本项目 data/golden
runtime_dir = ""   # 留空 = 本项目 runtime

[database]
host = "127.0.0.1"
port = 5432
name = "example_db"
user = ""
password = ""      # 只写在本文件，不提交 Git

[agent]
url = ""
token = ""         # 普通 HTTP 智能体密钥只写在本文件

[evaluator_llm]
enabled = true
base_url = "https://api.deepseek.com"
model = "deepseek-flash"
api_key = ""       # 评估模型密钥只写在本文件
timeout_seconds = 90
retries = 2
temperature = 0
max_output_tokens = 8000
max_input_chars = 500000
min_confidence = 0.70
prompt_version = "v1"
```

实时模式走业务平台会话时，按 [真实接口接入使用说明](docs/真实接口接入使用说明.md) 粘贴 `platform.curl`。
只跑模拟智能体（不需要数据库、智能体接口与评估 LLM）可跳过以上配置，模拟模式使用确定性回放评估器，不联网。

## 启动

### 1. 首次准备

```bat
cd /d <本项目目录>
python -m pip install -r requirements.txt
if not exist config\settings.toml copy config\settings.example.toml config\settings.toml
```

**data 目录单独私发**（不入 git）：拉取代码后，把私发的 `data` 文件夹整个放回 `<本项目目录>\data`（所有黄金集文件均直接位于 `data\golden\`），再启动服务。没有 data 也能启动服务、浏览目录，但模拟 20 题与 v1 历史题不可用（健康检查显示 `golden: missing`）。

### 2. 启动服务

```bat
python start.py
```

或双击 `start_eval.bat`（自动优先用 `.venv` 里的 Python）。浏览器会自动打开 `http://127.0.0.1:8765/`；端口、契约版本、运行时目录都在 `settings.toml` 里改。

### 3. 健康检查

`GET /api/health` 除黄金集、存储、数据库、智能体外，新增：

- `evaluator_llm = configured`：`enabled=true` 且 `base_url`、`model`、`api_key` 齐全
- `evaluator_llm = disabled`：`enabled=false`
- `evaluator_llm = not_configured`：缺少必要配置

健康检查只判断配置完整性，不调用模型，不返回密钥。

### 4. 停止

终端 `Ctrl+C`（或直接关窗口）。所有数据只落在本地 `runtime\`，数据库全程只读（仅 SELECT），不会写业务库。

## 操作说明

### 1. 首页：新建测评

打开 `http://127.0.0.1:8765/`，首页是「历史测评批次」列表和健康状态。点右上角 **「新建测评」**，弹窗里选：

| 配置项 | 说明 |
|---|---|
| 模式 | `模拟 20 题`（默认，内置模拟智能体与回放评估器，10 题与金标一致、10 题植入错值或漏行）或 `实时调用智能体与数据库` |
| 题目范围 | 仅实时模式出现：顺序 10 题 / 随机 10 题 / 指定题号 / 全部 |
| 指定题号 | 范围选「指定题号」时填写，逗号分隔，如 `CX-001, LS-001, DMA-001` |
| 基准时间 | 实时模式固定用当前时间（不可传历史时钟）；模拟模式自动用黄金集时间 2026-08-17 |
| 智能体名称 | 批次标注，默认 `water-loss-agent` |

点「开始」即后台执行，页面可关闭，稍后在首页批次列表点开查看。模拟模式秒级完成；实时模式每题几十秒（视智能体、SQL 与 LLM 耗时）。

接口方式建批：`POST /api/runs`，`mode` 可取 `mock_mixed` / `mock_perfect` / `mock_errors` / `live`。模拟回答是自然语言 + Markdown 表格（与真实智能体同一形态），问答稿见 `docs/模拟智能体20题.md` 或启动后的 [查看模拟 20 题问答稿](/api/mock-demo/script)。

**实时模式专属**：同一业务平台会话内题目按顺序执行；批次结束或中断后，回首页点 **「确认网页任务已结束，解除暂停」** 才能开下一批（防止同会话并发作答）。

### 2. 概览页 `/runs/{run_id}`

- 顶部指标卡：**题目通过率 / 准确率 / 可评估率**，下方是状态分布（合格 / 部分作答 / 不合格 / 无法评估）
- **问题定位**：七维一致率紧凑条 + 前三个主要问题（`primary_issue_distribution`，按题去重），用于回答「准确率低在哪里」
- 批次运行中会显示实时进度面板（已完成题数、当前题目），刷新页面即可跟进
- 「题目结果」表支持按题号/问题关键词搜索、按状态和结果类型（统计值/明细）筛选，并展示每题的主要问题短标签；点题号进入详情页
- 右上角 **导出 JSON / 导出 Excel / 离线 HTML**

历史批次若没有 `llm_evaluation.json`，对应题目显示「无法评估 / 尚无 LLM 评估」，不会回退旧规则重新计算。

### 3. 题目详情页 `/runs/{run_id}/cases/{case_id}`

上半部分左右对照：**智能体返回结果** vs **SQL 真实结果**（原文直出，Markdown 表格照常渲染）。下方五个页签：

| 页签 | 内容 |
|---|---|
| 结果对比 | 唯一的 LLM 结论：一行总体结论（结论标签、置信度、summary）+ 七个固定维度（状态与短原因）+ 差异表（问题类型、位置/字段、智能体值、SQL 值、说明）+ 底部元信息（模型 · prompt_version · 评估时间 · 耗时）。无评估时只显示「本题尚无 LLM 评估结果」 |
| 智能体请求与返回 | 原始请求/返回报文 |
| SQL 查询 | 查询摘要与完整 SQL；「只读补查」可修改绑定参数重新查询（模板只读，补查结果仅作参考、不改原评分） |
| 数据口径对齐 | 契约声明的维度、单位与归一/容差规则（不含比较结果） |
| 执行记录 | 智能体耗时、重试、事件与错误；SQL 查询耗时、补查次数、时间回退记录 |

### 4. 人工平反

详情页右上角有四个平反按钮：**合格 / 部分作答 / 不合格 / 无法评估**。点选即覆盖自动判定（按钮显示已覆盖样式），再点回自动判定值即撤销。平反后概览页的准确率、可评估率与专题通过率随之更新，`manual_verdict` 落库保留；七维一致率和问题分布保留 LLM 原始诊断，不因总体平反被擦除。

### 5. 失败处理

- LLM 调用失败：单题 `UNEVALUABLE`，问题码 `LLM_CALL_FAILED`，保存脱敏错误类型与重试次数，不影响后续题目。
- LLM 返回非法结构：按配置有限重试，最终保存 `LLM_RESPONSE_INVALID`；原始响应只保存在受控的 `llm_evaluation.json` 中，页面不渲染原始响应。
- 输入过大：证据包不静默截断 SQL 行，超过 `max_input_chars` 时保存 `LLM_INPUT_TOO_LARGE`。
- 低置信度：`confidence < min_confidence` 转为 `UNEVALUABLE`，`needs_human_review=true`，页面提示需要人工复核。

### 6. 安全

- `settings.toml` 必须保持在 `.gitignore`；日志、API、健康检查均不输出密码、api_key、token、Cookie、Authorization。
- 智能体回答和 SQL 文本均视为不可信输入；发送给外部 LLM 的只有评估必要字段，不发送数据库连接信息、执行 SQL 文本和请求头。
- 前端渲染 summary / reason / evidence / explanation 前统一 `escapeHtml`。

## 黄金集数据

黄金集与模拟回答全部放在本工具 `data/golden/`，评测只读该目录：v3 使用 `golden_dataset.json` 与 `漏损问答黄金测评集.xlsx`，v1 历史 73 题使用 `golden_dataset_v1.json` 与 `漏损问答黄金测评集_v1.xlsx`。两套版本共用根目录下的模拟回答文件。

## 测试

```bat
python -m pytest tests -v
node tests/ui_smoke.cjs
node tests/ui_detail_layout.cjs
```

UI 冒烟测试需要本机 Chrome 与已安装的 Node Playwright，并要求服务运行在 `http://127.0.0.1:8765`。

## Windows 分发

```powershell
.\package.ps1
```

将 `dist/agent-accuracy-evaluator` 拷到同事电脑，填写本地 `settings.toml` 后运行 `start_eval.bat`。

分发包不应包含 `.env`、数据库密码、智能体/评估模型密钥和 `runtime/` 历史运行数据。
