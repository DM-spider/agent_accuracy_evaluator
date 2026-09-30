# golden_builder · 黄金测评集构建

评测工具内置的标准集构建模块（唯一一份）。生成 141 道单 Query 标准集，产物直接写到
`config/contracts.json` 与 `data/golden/`。

## 用法（在评测工具根目录执行）

```bash
python -m golden_builder.runner                         # 基准日 = 今天，跑全量并覆盖标准集
python -m golden_builder.runner --base-date 2026-08-17  # 指定基准日（如业务核对快照）
python -m golden_builder.runner --compile-only          # 只编译契约，不连库
python -m golden_builder.runner --only JL-026,JL-027    # 子集调试，只写 data/debug
```

- 数据库凭据读 `config/settings.toml` 的 `[database]`（与评测服务同源，不硬编码）。
- `--only` 子集调试不会触碰标准集。

## 目录说明

| 文件 | 作用 |
|---|---|
| `runner.py` | 入口：编译 + 跑数 + 写 JSON/Excel/MD + 契约 |
| `compile.py` | catalog + builders → 契约 |
| `catalog.py` | 141 题元数据（题面、指标、解析器） |
| `fragments.py` / `builders/` | SQL 片段与 CX / LS / DMA / JL / BJ 五类参数化 builder |
| `assemble.py` | 结果组装（明细列名中文） |
| `registry.py` | 口径常量（组织白名单、红字规则、字典、锚点） |
| `build_excel.py` / `build_md.py` | JSON → Excel / Markdown |
| `db.py` | 只读数据库连接（凭据读 settings.toml） |

## 铁律

- SQL 比率输出小数比值，禁止 `*100`；度量列 `ROUND(..., 4)`，别名统一简体中文
- 集团合计剔除布吉 `F1014`；13 组织明细不含 `F1`/`F100`
- 产销差单月 `SzwgBusiness`，年累计 `SzwgBusinessYear`
- 报警用 `bz_bm`，禁止 `zonename`
- 维修漏水量只用 `water_leakage`
- **工单期间一律 `create_time` 半开区间**（GD-001~GD-011；完成判定才用 `finish_time IS NOT NULL`）
- 不引用看板爬取表

## 测试

```bash
pytest tests/golden_builder -q      # 静态守卫，不连库
```
