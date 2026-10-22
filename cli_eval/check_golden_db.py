# -*- coding: utf-8 -*-
"""在当前库重跑 16 题标准 SQL，检查标准值是否仍可复现。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evaluator.settings import db_password, load_settings  # noqa: E402
from evaluator.sql_executor import SqlExecutor, make_pg_connector  # noqa: E402

OUT = Path(__file__).resolve().parent / "out" / "golden_db"
OUT.mkdir(parents=True, exist_ok=True)


def main() -> None:
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
            "error": snapshot.error,
            "row_count": snapshot.row_count,
            "rows": snapshot.rows,
        }
        (OUT / f"{case_id}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
        )
        summary.append({"case_id": case_id, "error": snapshot.error, "rows": snapshot.row_count})
        print(f"{case_id}: error={snapshot.error!r} rows={snapshot.row_count}")
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
