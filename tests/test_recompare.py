import json

from evaluator.annotations import append_annotation
from evaluator.models import (
    REQUIRED_DIMENSIONS,
    CaseContract,
    DimensionEvaluation,
    DimensionStatus,
    LlmEvaluationArtifact,
    LlmEvaluationResult,
    MeasureSpec,
    Tolerance,
)
from evaluator.recompare import main, replay_runtime


def _contract() -> CaseContract:
    return CaseContract(
        case_id="C1",
        question="A公司漏损量是多少",
        result_type="stat",
        numeric_evaluable=True,
        realtime_ready=True,
        sql_template="select 1",
        row_key=["组织"],
        measures={
            "漏损量": MeasureSpec(
                label="漏损量",
                unit="m³",
                sql_column="漏损量",
                aliases=["漏损量"],
                tolerance=Tolerance(kind="abs", eps=0.01),
            )
        },
    )


def _artifact(verdict: str) -> LlmEvaluationArtifact:
    dimensions = {
        key: DimensionEvaluation(status=DimensionStatus.UNKNOWN, reason="") for key in REQUIRED_DIMENSIONS
    }
    return LlmEvaluationArtifact(
        evaluation=LlmEvaluationResult(
            overall_verdict=verdict,
            confidence=0.9,
            summary="",
            dimensions=dimensions,
        ),
        model="test-model",
        prompt_version="v1",
        created_at="2026-11-06T00:00:00+08:00",
    )


def _make_runtime(tmp_path, *, verdict="QUALIFIED", answer=None, sql_rows=None):
    runtime = tmp_path / "runtime"
    case_dir = runtime / "runs" / "r1" / "cases" / "C1"
    case_dir.mkdir(parents=True)
    (runtime / "runs" / "r1" / "contracts_snapshot.json").write_text(
        json.dumps([_contract().model_dump(mode="json")], ensure_ascii=False), encoding="utf-8"
    )
    (case_dir / "agent_answer.json").write_text(
        json.dumps(
            {"text": answer or "| 组织 | 漏损量 |\n|---|---|\n| A公司 | 100 |"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (case_dir / "sql_snapshot.json").write_text(
        json.dumps({"rows": sql_rows or [{"组织": "A公司", "漏损量": 100}], "params": {}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (case_dir / "llm_evaluation.json").write_text(
        json.dumps(_artifact(verdict).model_dump(mode="json"), ensure_ascii=False), encoding="utf-8"
    )
    return runtime


def _annotate(path, manual_verdict, reason_code="THRESHOLD_STRICT"):
    append_annotation(
        run_id="r1",
        case_id="C1",
        auto_verdict="PARTIAL",
        manual_verdict=manual_verdict,
        reason_code=reason_code,
        dimensions=["row_coverage"],
        note="测试",
        annotator="tester",
        version="abc",
        path=path,
    )


def test_replay_reproduces_stored_verdict(tmp_path):
    runtime = _make_runtime(tmp_path, verdict="QUALIFIED")
    payload = replay_runtime(runtime)
    assert payload["summary"]["cases"] == 1
    assert payload["summary"]["matched"] == 1
    row = payload["rows"][0]
    assert row["stored_verdict"] == "QUALIFIED"
    assert row["replay_verdict"] == "QUALIFIED"
    assert row["available"] is True


def test_replay_without_table_keeps_stored_verdict(tmp_path):
    runtime = _make_runtime(tmp_path, verdict="UNEVALUABLE", answer="本期没有可回答的数据。")
    payload = replay_runtime(runtime)
    row = payload["rows"][0]
    assert row["available"] is False
    assert row["replay_verdict"] == "UNEVALUABLE"
    assert row["match"] is True


def test_check_passes_when_annotation_matches(tmp_path):
    runtime = _make_runtime(tmp_path, verdict="QUALIFIED")
    annotations = tmp_path / "annotations.csv"
    _annotate(annotations, "QUALIFIED")
    code = main(
        [
            "--runtime", str(runtime),
            "--annotations", str(annotations),
            "--out", str(tmp_path / "out"),
            "--check",
        ]
    )
    assert code == 0
    payload = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert payload["check"]["checked"] == 1
    assert payload["check"]["failures"] == []


def test_check_fails_when_annotation_disagrees(tmp_path):
    runtime = _make_runtime(tmp_path, verdict="QUALIFIED")
    annotations = tmp_path / "annotations.csv"
    _annotate(annotations, "PARTIAL")
    code = main(
        [
            "--runtime", str(runtime),
            "--annotations", str(annotations),
            "--out", str(tmp_path / "out"),
            "--check",
        ]
    )
    assert code == 1
    payload = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert payload["check"]["failures"][0]["manual_verdict"] == "PARTIAL"


def test_reports_written(tmp_path):
    runtime = _make_runtime(tmp_path, verdict="UNQUALIFIED")
    out = tmp_path / "report"
    code = main(["--runtime", str(runtime), "--annotations", str(tmp_path / "none.csv"), "--out", str(out)])
    assert code == 0
    assert (out / "report.json").exists()
    assert (out / "report.md").exists()
    csv_text = (out / "cases.csv").read_text(encoding="utf-8-sig")
    assert csv_text.splitlines()[0].startswith("run_id,case_id,stored_verdict,replay_verdict")
    assert "与存档一致" in (out / "report.md").read_text(encoding="utf-8")
