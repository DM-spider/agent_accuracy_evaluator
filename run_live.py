"""Run up to ten sequential live questions using the same API as the page."""
import argparse
import json
import time

from fastapi import HTTPException

from evaluator.api import CreateRunBody, bootstrap, create_run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default="", help="Comma-separated case IDs; default first 10 numeric cases")
    args = parser.parse_args()
    st = bootstrap()
    try:
        created = create_run(CreateRunBody(mode="live", case_ids=args.cases.split(",") if args.cases else None))
    except HTTPException as exc:
        print(json.dumps(exc.detail, ensure_ascii=False))
        return 2
    run_id = created["run_id"]
    print(json.dumps(created, ensure_ascii=False), flush=True)
    last = None
    while True:
        run = st["repo"].get_run(run_id)
        progress = st["orchestrators"][run_id].get_progress(run_id)
        marker = (progress.get("done"), progress.get("current"), progress.get("status"))
        if marker != last:
            print(json.dumps(progress, ensure_ascii=False), flush=True)
            last = marker
        if run and run["status"] not in {"RUNNING", "PENDING"}:
            job = st.get("jobs", {}).get(run_id)
            if job:
                job.join()
            print(json.dumps(run["summary"], ensure_ascii=False), flush=True)
            return 0 if run["status"] == "COMPLETED" else 1
        time.sleep(0.5)


if __name__ == "__main__":
    raise SystemExit(main())
