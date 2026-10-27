import json

import pytest

from evaluator.exporter import export_html
from evaluator.report_html import build_report, differences_html


class ReportRepo:
    def __init__(self, root, entries):
        self.runs_dir = root
        self.entries = entries
        self.run = {"status": "COMPLETED", "summary": {"numeric_accuracy": 1.0}, "agent_name": "测试智能体"}
        for entry in entries:
            case_dir = root / "r" / "cases" / entry["case_id"]
            case_dir.mkdir(parents=True)
            for name in ("llm_evaluation", "agent_answer"):
                if name in entry:
                    (case_dir / f"{name}.json").write_text(json.dumps(entry[name], ensure_ascii=False), encoding="utf-8")

    def get_run(self, run_id):
        return self.run

    def list_cases(self, run_id):
        return self.entries

    def load_case_detail(self, run_id, case_id):
        return next(entry for entry in self.entries if entry["case_id"] == case_id)


def entry(case_id="CX-1", verdict="UNQUALIFIED", **extra):
    return {
        "case_id": case_id, "status": "FAIL", "completion_status": "completed",
        "result": {"question": "2026 年的供水量？"},
        "agent_answer": {"text": "| 月份 | 供水量 |\n|---|---|\n|202601|1,730,758|\n<script>alert(1)</script>", "raw": {"cookie": "PRIVATE_COOKIE"}},
        "llm_evaluation": {"model": "test", "evaluation": {
            "overall_verdict": verdict, "confidence": .95, "summary": "数值不一致", "primary_issue_code": "WRONG_VALUE", "issue_codes": ["WRONG_VALUE"],
            "dimensions": {"numeric_accuracy": {"status": "MISMATCH", "reason": "数值不一致"}},
            "differences": [{"type": "WRONG_VALUE", "severity": "ERROR", "field": "供水量", "coordinate": {"月份": "202601"}, "agent_value": "1,730,758", "sql_value": 2515137, "normalized_agent_value": 1730758, "normalized_sql_value": 2515137, "evidence": "原始核验证据"}],
        }},
        "sql_snapshot": {"columns": ["月份", "供水量"], "rows": [{"月份": 202601, "供水量": 2515137}], "latency_ms": 100, "executed_sql": "SELECT '<script>unsafe</script>'"},
        **extra,
    }


def test_export_uses_live_verdicts_and_escapes_html(tmp_path, monkeypatch):
    repo = ReportRepo(tmp_path, [entry()])
    monkeypatch.setattr("evaluator.exporter.load_contracts", lambda: [])
    destination = tmp_path / "report.html"
    assert export_html(repo, "r", destination) == destination
    html = destination.read_text(encoding="utf-8")
    assert "合格 0 / 可评估 1 题" in html  # 不读取旧 summary 的 100% 准确率。
    assert "已返回回答 1 / 计划 1 题" in html
    assert "-784,379" in html and "-31.2%" in html
    assert "2,515,137" in html and "202601" in html
    # 差异明细只保留说明，不再把模型的原始核验证据折叠进来
    assert "查看核验证据" not in html
    assert "原始核验证据" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html
    assert "PRIVATE_COOKIE" not in html
    assert "comparison_items" not in html
    assert '<link ' not in html and '<script src=' not in html


def test_manual_override_preserves_auto_evidence_and_all_cases(tmp_path):
    repo = ReportRepo(tmp_path, [entry(manual_verdict="QUALIFIED", review={"note": "业务已复核"}), entry("LS-2", "PARTIAL"), entry("DMA-3", "UNEVALUABLE")])
    html = build_report(repo, "r", [])
    assert "合格 1 / 可评估 2 题" in html
    assert "人工调整：不合格 → 合格" in html
    assert "业务已复核" in html
    assert "以下维度和差异保留自动评估记录" in html
    assert html.count("data-case-row data-verdict") == 3
    assert html.count("data-case-detail>") == 3
    assert html.count("-784,379") == 3
    assert "产销差专题" in html and "漏损率专题" in html


def test_empty_batch_does_not_invent_zero_accuracy(tmp_path):
    repo = ReportRepo(tmp_path, [])
    repo.run["status"] = "RUNNING"
    html = build_report(repo, "r", [])
    assert "暂无可评估样本" in html
    assert "本批次尚无题目结果" in html
    assert "阶段性结果" in html


def test_missing_evaluation_and_sql_rechecks_are_explicit(tmp_path):
    item = entry()
    item.pop("llm_evaluation")
    item["sql_snapshot"] = {"error": "查询失败", "truncated": True, "watermark_changed": True}
    item["sql_rechecks"] = [{"finished_at": "2026-10-12T00:00:00Z", "note": "事后验证", "rows": [{"供水量": 999}]}]
    html = build_report(ReportRepo(tmp_path, [item]), "r", [])
    assert "无法评估 1 题" in html
    assert "尚无模型评估结果" in html
    assert "SQL 查询错误" in html and "基准结果已截断" in html
    assert "数据水位发生变化" in html
    assert "仅供复核，不覆盖原评分基准" in html
    assert "2026-10-12 08:00:00" in html


@pytest.mark.parametrize("agent,sql,ratio", [(10, -10, "+200.0%"), (1, 0, "—"), (None, 10, "—"), (float("inf"), 10, "—")])
def test_delta_requires_normalized_finite_values(agent, sql, ratio):
    difference = {"type": "WRONG_VALUE", "severity": "ERROR", "field": "水量", "agent_value": "1 万吨", "sql_value": 10000, "normalized_agent_value": agent, "normalized_sql_value": sql}
    html = differences_html([difference], {})
    assert ratio in html
    assert "1 万吨" in html


def test_existing_export_endpoint_returns_new_report(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import evaluator.api as api_mod
    from evaluator.app import app

    repo = ReportRepo(tmp_path, [entry()])
    monkeypatch.setattr(api_mod, "state", lambda: {"repo": repo})
    monkeypatch.setattr(api_mod, "runs_dir", lambda: tmp_path)
    monkeypatch.setattr("evaluator.exporter.load_contracts", lambda: [])
    response = TestClient(app).get("/api/runs/r/export?format=html")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert 'filename="report.html"' in response.headers["content-disposition"]
    assert "回答合格率" in response.text
    assert "标准化差值" in response.text


def _ratio_contract():
    from evaluator.models import CaseContract, MeasureSpec, Tolerance

    return CaseContract(
        case_id="CX-T",
        question="各单位产销差率？",
        result_type="detail",
        numeric_evaluable=True,
        row_key=["组织"],
        measures={
            "产销差率": MeasureSpec(
                label="产销差率", unit="%", value_scale="ratio01", sql_column="产销差率",
                tolerance=Tolerance(kind="abs", eps=0.01),
            )
        },
    )


def test_comparison_puts_both_sides_in_the_same_unit():
    from evaluator.report_html import comparison_html

    answer = "| 单位 | 产销差率 |\n|---|---|\n| 南山分公司 | 12.02 |\n| 福田分公司 | 6.70 |\n"
    snapshot = {"rows": [{"组织": "南山分公司", "产销差率": 0.1202}, {"组织": "福田分公司", "产销差率": 0.0665}]}
    html = comparison_html(_ratio_contract().model_dump(mode="json"), answer, snapshot)
    # 左（回答）与右（基准）都换算成百分数，同一行同一量纲
    assert html.count("12.02") == 2
    assert html.count("6.65") == 1
    assert "cell-miss" in html  # 6.70 vs 6.65 超出 0.01 容差
    assert "智能体回答" in html and "标准答案（SQL 基准）" in html


def test_comparison_falls_back_to_raw_answer_without_a_table():
    from evaluator.report_html import comparison_html

    snapshot = {"rows": [{"组织": "南山分公司", "产销差率": 0.1202}]}
    html = comparison_html(_ratio_contract().model_dump(mode="json"), "南山分公司产销差率 12.02%。", snapshot)
    assert "report-answer" in html
    assert "12.02" in html
