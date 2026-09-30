# -*- coding: utf-8 -*-
"""评测内核的数据模型与状态枚举。"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CaseStatus(str, Enum):
    PASS = "PASS"
    PARTIAL = "PARTIAL"
    FAIL = "FAIL"
    REVIEW = "REVIEW"
    NOT_SCORED = "NOT_SCORED"


class EvaluationVerdict(str, Enum):
    QUALIFIED = "QUALIFIED"
    PARTIAL = "PARTIAL"
    UNQUALIFIED = "UNQUALIFIED"
    UNEVALUABLE = "UNEVALUABLE"


class DimensionStatus(str, Enum):
    MATCH = "MATCH"
    PARTIAL = "PARTIAL"
    MISMATCH = "MISMATCH"
    UNKNOWN = "UNKNOWN"
    NA = "NA"


REQUIRED_DIMENSIONS: tuple = (
    "period",
    "scope",
    "grain",
    "field_coverage",
    "row_coverage",
    "numeric_accuracy",
    "unit_caliber",
)

ISSUE_CODES: tuple = (
    "PERIOD_MISMATCH",
    "SCOPE_MISMATCH",
    "GRAIN_MISMATCH",
    "MISSING_FIELD",
    "MISSING_ROW",
    "WRONG_VALUE",
    "UNIT_MISMATCH",
    "CALIBER_MISMATCH",
    "CONTRADICTORY_TEXT",
    "INSUFFICIENT_EVIDENCE",
    "LLM_CALL_FAILED",
    "LLM_RESPONSE_INVALID",
    "LLM_INPUT_TOO_LARGE",
)

# 主要问题优先级：越靠前越优先归因，避免日期筛选错误被统计成大量数值错误。
PRIMARY_ISSUE_PRIORITY: tuple = (
    "PERIOD_MISMATCH",
    "SCOPE_MISMATCH",
    "GRAIN_MISMATCH",
    "CALIBER_MISMATCH",
    "UNIT_MISMATCH",
    "MISSING_FIELD",
    "MISSING_ROW",
    "WRONG_VALUE",
    "CONTRADICTORY_TEXT",
)

PRIMARY_ISSUE_ORDER = {code: index for index, code in enumerate(PRIMARY_ISSUE_PRIORITY)}


class RunStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    INTERRUPTED = "INTERRUPTED"
    FAILED = "FAILED"


class ErrorType(str, Enum):
    SQL_FAIL = "SQL_FAIL"
    AGENT_FAIL = "AGENT_FAIL"
    SQL_NOT_REALTIME_READY = "SQL_NOT_REALTIME_READY"
    WATERMARK_CHANGED = "WATERMARK_CHANGED"
    NON_NUMERIC = "NON_NUMERIC"


class Tolerance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = "abs"
    eps: float = 0.01
    abs_floor: float = 0.0

    @field_validator("kind")
    @classmethod
    def _kind(cls, value: str) -> str:
        allowed = {"abs", "rel", "exact"}
        if value not in allowed:
            raise ValueError(f"tolerance.kind must be one of {sorted(allowed)}")
        return value


class MeasureSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    aliases: List[str] = Field(default_factory=list)
    unit: str = ""
    tolerance: Tolerance = Field(default_factory=Tolerance)
    sql_column: str = ""
    value_scale: str = "volume"
    required: bool = True
    aggregation: str = ""
    period_role: str = ""

    @field_validator("value_scale")
    @classmethod
    def _scale(cls, value: str) -> str:
        allowed = {"percent", "ratio01", "volume", "count", "pp", "text"}
        if value not in allowed:
            raise ValueError(f"value_scale must be one of {sorted(allowed)}")
        return value


class CoveragePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = "all_fields"
    minimum: float = 1.0
    n: Optional[int] = None
    score_order: bool = False

    @field_validator("mode")
    @classmethod
    def _mode(cls, value: str) -> str:
        allowed = {"all_rows", "all_fields", "top_n"}
        if value not in allowed:
            raise ValueError(f"coverage_policy.mode must be one of {sorted(allowed)}")
        return value

    @field_validator("minimum")
    @classmethod
    def _minimum(cls, value: float) -> float:
        if not 0 <= value <= 1:
            raise ValueError("coverage_policy.minimum must be between 0 and 1")
        return value


class CaseContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    question: str
    result_type: str
    numeric_evaluable: bool
    realtime_ready: bool = False
    sql_template: str = ""
    sql_source: str = ""
    parameter_resolver: str = "none"
    dimensions: List[str] = Field(default_factory=list)
    row_key: List[str] = Field(default_factory=list)
    dimension_columns: Dict[str, List[str]] = Field(default_factory=dict)
    measures: Dict[str, MeasureSpec] = Field(default_factory=dict)
    coverage_policy: CoveragePolicy = Field(default_factory=CoveragePolicy)
    scene_big: str = ""
    category: str = ""
    indicator_type: str = ""
    caliber: str = ""
    notes: str = ""
    roles: List[str] = Field(default_factory=list)
    review_required: List[str] = Field(default_factory=list)
    not_scored_reason: Optional[str] = None

    @model_validator(mode="after")
    def _numeric_contract(self) -> "CaseContract":
        if not self.numeric_evaluable:
            return self
        if not self.sql_template and not self.sql_source:
            self.review_required = list(dict.fromkeys(self.review_required + ["sql"]))
        if not self.measures:
            self.review_required = list(dict.fromkeys(self.review_required + ["measures"]))
        if self.result_type == "detail" and not self.row_key:
            self.review_required = list(dict.fromkeys(self.review_required + ["row_key"]))
        return self


class RunContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    anchor_time: datetime
    timezone: str = "Asia/Shanghai"
    current_month: str
    previous_month: str
    current_month_start: str
    next_month_start: str
    current_month_end: str
    previous_month_start: str
    previous_month_end: str
    year_start: str
    today: str
    yesterday: str
    yoy_month: str
    last_6_months: List[str] = Field(default_factory=list)
    effective_single_month: str = ""
    effective_year_month: str = ""
    agent_name: str = "water-loss-agent"
    consistency_threshold: float = 0.6
    latest_periods: Dict[str, int] = Field(default_factory=dict)


class CaseMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 仅保留题目级 LLM 结论需要的字段。
    accuracy: Optional[float] = None
    auto_resolved: bool = True


class DimensionEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: DimensionStatus
    reason: str = ""


class EvaluationDifference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    severity: str = "ERROR"
    coordinate: Dict[str, str] = Field(default_factory=dict)
    field: str = ""
    agent_value: Any = None
    sql_value: Any = None
    normalized_agent_value: Optional[float] = None
    normalized_sql_value: Optional[float] = None
    delta: Optional[float] = None
    evidence: str = ""
    explanation: str = ""

    @field_validator("type")
    @classmethod
    def _type(cls, value: str) -> str:
        if value not in ISSUE_CODES:
            raise ValueError(f"unknown issue code: {value}")
        return value

    @field_validator("severity")
    @classmethod
    def _severity(cls, value: str) -> str:
        if value not in {"ERROR", "WARNING"}:
            raise ValueError("severity 只能是 ERROR 或 WARNING")
        return value

    @field_validator("explanation")
    @classmethod
    def _explanation(cls, value: str) -> str:
        if len(value or "") > 120:
            raise ValueError("explanation 不能超过 120 个字符")
        return value


class LlmEvaluationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "v1"
    overall_verdict: EvaluationVerdict
    confidence: float
    summary: str = ""
    primary_issue_code: Optional[str] = None
    issue_codes: List[str] = Field(default_factory=list)
    dimensions: Dict[str, DimensionEvaluation]
    differences: List[EvaluationDifference] = Field(default_factory=list)
    needs_human_review: bool = False

    @field_validator("confidence")
    @classmethod
    def _confidence(cls, value: float) -> float:
        if not 0 <= value <= 1:
            raise ValueError("confidence 必须在 0 到 1 之间")
        return value

    @field_validator("issue_codes")
    @classmethod
    def _issue_codes(cls, value: List[str]) -> List[str]:
        unknown = [code for code in value if code not in ISSUE_CODES]
        if unknown:
            raise ValueError(f"unknown issue codes: {unknown}")
        return list(dict.fromkeys(value))

    @field_validator("primary_issue_code")
    @classmethod
    def _primary(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in ISSUE_CODES:
            raise ValueError(f"unknown primary issue code: {value}")
        return value

    @model_validator(mode="after")
    def _dimensions_exact(self) -> "LlmEvaluationResult":
        keys = tuple(self.dimensions)
        if set(keys) != set(REQUIRED_DIMENSIONS):
            missing = [key for key in REQUIRED_DIMENSIONS if key not in self.dimensions]
            extra = [key for key in keys if key not in REQUIRED_DIMENSIONS]
            raise ValueError(f"dimensions 必须恰好包含七个固定维度 missing={missing} extra={extra}")
        return self


class LlmEvaluationArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluation: LlmEvaluationResult
    provider: str = "openai-compatible"
    model: str = ""
    prompt_version: str = "v1"
    input_hash: str = ""
    created_at: str
    latency_ms: int = 0
    token_usage: Dict[str, int] = Field(default_factory=dict)
    validation_notes: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    raw_response: Optional[str] = None


class AgentAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    question: str
    text: str = ""
    latency_ms: int = 0
    retries: int = 0
    error: Optional[str] = None
    model: str = ""
    http_url: str = ""
    http_status: int = 200
    request_body: Dict[str, Any] = Field(default_factory=dict)
    response_body: Dict[str, Any] = Field(default_factory=dict)
    completion_status: str = "completed"
    timings: Dict[str, Any] = Field(default_factory=dict)


class WatermarkSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tables: Dict[str, Optional[str]] = Field(default_factory=dict)
    captured_at: str = ""


class SqlSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sql_template: str = ""
    params: Dict[str, Any] = Field(default_factory=dict)
    executed_sql: str = ""
    columns: List[str] = Field(default_factory=list)
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    latency_ms: int = 0
    error: Optional[str] = None
    result_path: str = ""
    watermark_before: Optional[WatermarkSnapshot] = None
    watermark_after: Optional[WatermarkSnapshot] = None
    watermark_changed: bool = False
    source: str = "live_sql"


class CaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    question: str = ""
    scene_big: str = ""
    result_type: str = ""
    numeric_evaluable: bool = True
    status: CaseStatus
    not_scored_reason: Optional[str] = None
    primary_failure: Optional[str] = None
    error_types: List[str] = Field(default_factory=list)
    metrics: CaseMetrics = Field(default_factory=CaseMetrics)
    agent_latency_ms: int = 0
    sql_latency_ms: int = 0
    retries: int = 0
    reviewed: bool = False
    review_note: str = ""
    turn_index: int = 0
    completion_status: str = "not_sent"
    agent_timings: Dict[str, Any] = Field(default_factory=dict)
    alignment: Dict[str, Any] = Field(default_factory=dict)


class SceneStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene: str
    scored: int = 0
    passed: int = 0
    pass_rate: Optional[float] = None
    accuracy: Optional[float] = None


class RunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    status: RunStatus = RunStatus.PENDING
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    anchor_time: str = ""
    timezone: str = "Asia/Shanghai"
    agent_name: str = ""
    total_cases: int = 0
    scored_cases: int = 0
    not_scored_cases: int = 0
    pass_cases: int = 0
    partial_cases: int = 0
    fail_cases: int = 0
    review_cases: int = 0
    case_pass_rate: Optional[float] = None
    numeric_accuracy: Optional[float] = None
    numeric_coverage: Optional[float] = None
    row_coverage: Optional[float] = None
    unexpected_rate: Optional[float] = None
    auto_parse_rate: Optional[float] = None
    watermark_stable: bool = True
    by_scene: List[SceneStats] = Field(default_factory=list)
    by_status: Dict[str, int] = Field(default_factory=dict)
    by_error_type: Dict[str, int] = Field(default_factory=dict)
    progress_done: int = 0
    progress_total: int = 0
    consistency_threshold: float = 0.6
    timing_summary: Dict[str, Any] = Field(default_factory=dict)
