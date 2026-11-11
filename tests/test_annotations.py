import csv

import pytest

from evaluator.annotations import (
    append_annotation,
    load_annotations,
    latest_by_case,
    summarize,
    validate_reason,
    write_snapshot,
)


def _append(path, case_id="C1", code="THRESHOLD_STRICT", dimensions=("row_coverage",), note="理由"):
    return append_annotation(
        run_id="r1",
        case_id=case_id,
        auto_verdict="PARTIAL",
        manual_verdict="QUALIFIED",
        reason_code=code,
        dimensions=dimensions,
        note=note,
        evidence="原文",
        annotator="tester",
        version="abc123",
        path=path,
    )


def test_append_creates_bom_csv_and_reads_back(tmp_path):
    path = tmp_path / "annotations.csv"
    row = _append(path, dimensions=("row_coverage", "bogus_dimension"))
    assert row["dimensions"] == ["row_coverage"]
    assert row["reason_label"] == "判定太严"
    assert row["created_at"]
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    rows = load_annotations(path)
    assert len(rows) == 1
    assert rows[0]["case_id"] == "C1"
    assert rows[0]["auto_verdict"] == "PARTIAL"
    assert rows[0]["manual_verdict"] == "QUALIFIED"


def test_append_keeps_single_header(tmp_path):
    path = tmp_path / "annotations.csv"
    _append(path, case_id="C1")
    _append(path, case_id="C2")
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    text = path.read_text(encoding="utf-8-sig")
    assert text.count("run_id,case_id") == 1


def test_validate_reason_rejects_unknown():
    with pytest.raises(ValueError):
        validate_reason("NOT_A_REASON")


def test_latest_by_case_and_snapshot(tmp_path):
    path = tmp_path / "annotations.csv"
    _append(path, case_id="C1", note="第一次")
    _append(path, case_id="C1", note="第二次")
    _append(path, case_id="C2", note="另一题")
    latest = latest_by_case(load_annotations(path))
    assert len(latest) == 2
    assert latest[("r1", "C1")]["note"] == "第二次"

    out = tmp_path / "snapshot.csv"
    count = write_snapshot(out, path)
    assert count == 2
    snapshot_rows = load_annotations(out)
    assert len(snapshot_rows) == 2
    assert {row["case_id"] for row in snapshot_rows} == {"C1", "C2"}


def test_summarize_counts_by_reason(tmp_path):
    path = tmp_path / "annotations.csv"
    _append(path, case_id="C1", code="EXTRACT_MISS")
    _append(path, case_id="C2", code="EXTRACT_MISS")
    _append(path, case_id="C3", code="OTHER")
    data = summarize(load_annotations(path))
    assert data["total"] == 3
    assert data["cases"] == 3
    assert data["by_reason"]["EXTRACT_MISS"] == 2
    assert data["by_reason"]["OTHER"] == 1
    assert data["by_reason"]["THRESHOLD_STRICT"] == 0
