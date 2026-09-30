# -*- coding: utf-8 -*-
"""标准集构建入口：编译并（可选）执行 141 题，写出 JSON / Excel / MD / contracts.json。

在评测工具根目录执行：

    python -m golden_builder.runner                    # 基准日 = 今天
    python -m golden_builder.runner --base-date 2026-08-17
    python -m golden_builder.runner --compile-only     # 只写契约，不连库
    python -m golden_builder.runner --only JL-026      # 子集调试，只写 data/debug
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path

from evaluator.paths import CONFIG_DIR, TOOL_ROOT, configure_paths, golden_dir
from evaluator.run_context import build_run_context, params_for_resolver, preview_sql, to_pg_params
from evaluator.settings import load_settings
from golden_builder import build_excel, build_md
from golden_builder.assemble import make_detail, make_stat
from golden_builder.compile import compile_all
from golden_builder.db import get_latest_periods, query

CONTRACTS_PATH = CONFIG_DIR / "contracts.json"
DEBUG_DIR = TOOL_ROOT / "data" / "debug"


def _dump_contracts(contracts, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"contracts": contracts}, f, ensure_ascii=False, indent=2)


def data_cutoff() -> dict:
    """各表实际取数截止（meta.data_cutoff），便于业务核对数据新鲜度。"""
    checks = [
        ("fqcxfx", "SELECT MAX(businessyearmonth) FROM dwd_lsxt_fqcxfx WHERE periodtype='SzwgBusinessYear'"),
        ("fqcxfx_single", "SELECT MAX(businessyearmonth) FROM dwd_lsxt_fqcxfx WHERE periodtype='SzwgBusiness'"),
        ("wlls", "SELECT MAX(taskstart)::date FROM dwd_lsxt_wlls"),
        ("bj", "SELECT MAX(warndate)::date FROM dwd_lsxt_bj"),
        ("gsgdwxmx", "SELECT MAX(create_time)::date FROM dwd_gdgl_gd_gsgdwxmx"),
        ("jlmx", "SELECT MAX(create_time)::date FROM dwd_gdgl_gd_jlmx"),
        ("xfswxmx", "SELECT MAX(create_time)::date FROM dwd_gdgl_gd_xfswxmx"),
        ("fmwxgdmx", "SELECT MAX(create_time)::date FROM dwd_gdgl_gd_fmwxgdmx"),
        ("dm_yyzbtx", "SELECT MAX(ywrq)::date FROM dm_yyzbtx_gggsgwlsl"),
        ("dma", "SELECT COUNT(*) FROM dwd_lsxt_dma"),
    ]
    from decimal import Decimal
    out = {}
    for key, sql in checks:
        try:
            value = query(sql)[1][0][0]
            if isinstance(value, Decimal) and value == int(value):
                out[key] = str(int(value))
            elif hasattr(value, "strftime"):
                out[key] = str(value)[:10]
            else:
                out[key] = str(value)
        except Exception as exc:
            out[key] = "ERROR:%s" % exc
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-date", default=None, help="基准日 YYYY-MM-DD；缺省=今天")
    ap.add_argument("--compile-only", action="store_true")
    ap.add_argument("--only", default="",
                    help="只跑指定题号/前缀（逗号分隔），如 JL 或 JL-026,JL-027；只写 data/debug")
    ap.add_argument("--out-dir", default=None,
                    help="默认：完整跑数写 data/golden；--only 写 data/debug")
    args = ap.parse_args(argv)

    configure_paths(load_settings())

    compiled_all = compile_all()
    contracts = [item[2] for item in compiled_all]
    _dump_contracts(contracts, CONTRACTS_PATH)
    print("written %s (%d)" % (CONTRACTS_PATH, len(contracts)), flush=True)

    compiled = compiled_all
    if args.only:
        wanted = [x.strip() for x in args.only.split(",") if x.strip()]
        compiled = [item for item in compiled_all
                    if any(item[0]["case_id"] == w or item[0]["case_id"].startswith(w + "-") for w in wanted)]
        print("only=%s -> %d cases" % (args.only, len(compiled)), flush=True)

    if args.compile_only:
        print("compile-only done", flush=True)
        return 0

    B = date.fromisoformat(args.base_date) if args.base_date else date.today()
    latest = get_latest_periods()
    ctx = build_run_context(B.isoformat(), latest_periods=latest)
    cases = []
    failed = []
    empty = []
    for spec, sql, contract in compiled:
        rec = {
            "case_id": spec["case_id"],
            "category": spec["category"],
            "scene_big": spec["scene_big"],
            "roles": spec.get("roles") or [],
            "indicator_type": spec["indicator_type"],
            "question": spec["question"],
            "time_scope_raw": spec["time_scope_raw"],
            "org_scope_raw": spec["org_scope_raw"],
            "caliber": spec["caliber"],
            "business_verified": bool(spec.get("business_verified", False)),
            "result_type": spec["result_type"],
            "sql": sql,
            "params": {},
            "availability": "computable",
            "notes": spec["caliber"],
        }
        try:
            params = params_for_resolver(ctx, spec["parameter_resolver"])
            rec["params"] = params
            pg_sql, bound = to_pg_params(sql, params)
            rec["sql_source"] = pg_sql
            rec["sql_executable"] = preview_sql(sql, params)
            cols, rows = query(pg_sql, bound)
            if not rows:
                rec["availability"] = "empty"
                rec["expected"] = {"fields": []} if spec["result_type"] == "stat" else {"detail": {"rows": [], "total_rows": 0, "key_fields": spec.get("row_key") or []}}
                empty.append(spec["case_id"])
            elif spec["result_type"] == "stat":
                rec["expected"] = make_stat(cols, rows, spec["measures"])
            else:
                rec["expected"] = {"detail": make_detail(cols, rows, spec.get("row_key") or [], spec["measures"])}
        except Exception as exc:
            rec["availability"] = "error"
            rec["notes"] = "%s; ERROR:%s" % (spec["caliber"], exc)
            rec["expected"] = {}
            failed.append((spec["case_id"], str(exc)))
        cases.append(rec)
        print("[%s] %s %s" % (spec["case_id"], rec["availability"], rec["result_type"]), flush=True)

    out_dir = Path(args.out_dir) if args.out_dir else (DEBUG_DIR if args.only else golden_dir())
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = "_" + args.only.replace(",", "_") if args.only else ""
    golden = {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "time_anchor_base": B.isoformat(),
            "total_cases": len(cases),
            "only": args.only or None,
            "data_cutoff": {} if args.only else data_cutoff(),
            "latest_periods": latest,
            "empty_cases": empty,
            "failed_cases": [x[0] for x in failed],
        },
        "cases": cases,
    }
    out_json = out_dir / ("golden_dataset%s.json" % suffix)
    out_json.write_text(json.dumps(golden, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("written %s" % out_json, flush=True)

    xlsx = out_dir / ("漏损问答黄金测评集%s.xlsx" % suffix)
    md = out_dir / ("黄金测评集_问答打印%s.md" % suffix)
    try:
        build_excel.build(golden, str(xlsx), B)
        build_md.build(golden, str(md), B)
        print("written %s" % xlsx, flush=True)
        print("written %s" % md, flush=True)
    except Exception as exc:
        print("document render skipped: %s" % exc, flush=True)
    if args.only:
        print("partial run（%s）：只写 %s，未触动标准集" % (args.only, out_dir), flush=True)

    print("empty=%d failed=%d" % (len(empty), len(failed)), flush=True)
    for cid, err in failed:
        print("FAIL %s: %s" % (cid, err), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(None))
