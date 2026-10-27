# -*- coding: utf-8 -*-
"""在当前库重跑 16 题标准 SQL，把标准值写进当天版本目录。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evaluator.settings import db_password, load_settings  # noqa: E402
from evaluator.sql_executor import SqlExecutor, make_pg_connector  # noqa: E402

from cli_eval.outdir import ensure_version_dir, use_date  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="版本日期 YYYYMMDD（如 20261027），默认今天")
    args = parser.parse_args()
    version = use_date(args.date)
    out = ensure_version_dir() / "golden_db"
    out.mkdir(parents=True, exist_ok=True)

    golden = json.loads((ROOT / "data" / "golden" / "golden_dataset.json").read_text(encoding="utf-8"))
    settings = load_settings()
    executor = SqlExecutor(make_pg_connector(settings, db_password(settings)), timeout_seconds=60)
    summary = []
    for case in golden["cases"]:
        if not case.get("business_verified"):
            continue
        case_id = case["case_id"]
        snapshot = executor.query(case.get("sql_executable") or "", {})
        payload = {
            "case_id": case_id,
            "version": version,
            "error": snapshot.error,
            "row_count": snapshot.row_count,
            "rows": snapshot.rows,
        }
        (out / f"{case_id}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
        )
        summary.append({"case_id": case_id, "error": snapshot.error, "rows": snapshot.row_count})
        print(f"{case_id}: error={snapshot.error!r} rows={snapshot.row_count}")
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n版本 {version} 的标准值已写入 {out}")


if __name__ == "__main__":
    main()
