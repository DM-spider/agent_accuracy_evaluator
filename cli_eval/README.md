# CLI 接口对账（cli_eval）

对 `leakage-agent-cli` 做“指定传参 → 取数 → 和业务核对标准值比对 → 出报告”的最小工具。
不修改 `evaluator/`，不调用 LLM，不写业务库。

## 范围

`data/golden/golden_dataset.json` 中 `business_verified=true` 的 16 道题。

## 运行

```bash
cd agent_accuracy_evaluator

# 0) 先确认 CLI 的 .env_local 指向可用网关（不在本仓库）
#    位置：water-loss-agent/zs-water-loss-agent/tools/leakage-agent-cli/.env_local

# 1) 用 16 题的标准 SQL 在当前库跑出本次测试的标准值
python cli_eval/check_golden_db.py

# 2) 跑 16 题 CLI 对账
python -m cli_eval.run

# 3) 生成 HTML 报告
python -m cli_eval.report
```

三条命令都可以加 `--date YYYYMMDD` 指定版本日期，默认是今天（也接受 `2026-10-27` 写法，会归一成 8 位）。

## 版本管理

结果按日期存放，**一天一个版本**：

```
cli_eval/out/
  report_20261027/
    results.json                       16 题结构化结果
    cli_eval_report_20261027.html      自包含报告（文件名带日期，方便分享）
    golden_db/                         当天的标准值快照
  report_20261026/
  raw/                                  接口响应缓存，跨版本共用
```

- 同一天重复运行会**覆盖当天版本**，不会生成第二份。
- `python -m cli_eval.report --list` 列出已有版本。
- `python -m cli_eval.report --date 20261026` 用某一版的数据重出报告。
- 版本目录按日期排序，最后一个是最近一次；`out/` 下只有版本目录和 `raw/`，不放散文件。

## 结论口径

本次测试只有一套标准值：**用题目自带的标准 SQL 在当前库跑出的结果**。字段和容差沿用题目的 `value_fields` / `tolerance`。

- `PASS`：CLI 与标准值在容差内一致。
- `VALUE_DIFF`：CLI 与标准值有数值差异。
- `CALIBER_DIFF`：差异来自口径（如当月水量用累计差、标准值用单月快照），不是数据错。
- `PARTIAL`：部分列可对，部分列缺字段/缺命令/缺维度。
- `BLOCKED`：命令确实调不通（保留状态码，不再出现"长命令被截断"这类旧说法）。

## 加一道题

1. 在 `cli_eval/cases.py` 写一个 `case_xxx()`，返回 `result(...)`。
2. 在 `HANDLERS` 里注册。
3. 在 `cli_eval/run.py` 的 `ORDER` 里加题号。

分页约定（实测）：

- 指标：`pageNo` 从 0 起，`pageSize` ≤100，路径 `data.data.list`，字段 `total/pageCount`。
- 明细：`pageNumber` 从 1 起，`pageSize` ≤50，路径 `data.data.result`，字段 `totalRows/totalPage`。

## 注意

- `startDate/endDate` 实测会触发 `respCode=1002`，当前只能拉全量后按 `periodPoint` 客户端过滤。
- `invoke` 不校验 `respCode`，退出码仍为 0；必须以返回体里的 `respCode` 为准。
- 只调用读取命令；不要调用 `ai_advice_save`、`alert_water_balance`。
- 原始响应缓存会命中，重跑不会重复请求；需要重新实调时删掉 `out/raw/` 对应文件。
- `golden_db/` 属于当天版本。新的一天如果没先跑 `check_golden_db.py`，标准值会退回黄金集里的静态结果，`run.py` 会打印提醒。
