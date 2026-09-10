# -*- coding: utf-8 -*-
"""评测内核的数据模型与状态枚举。"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ClaimStatus(str, Enum):
    MATCH = "MATCH"
    MATCH_WITH_TOLERANCE = "MATCH_WITH_TOLERANCE"
    WRONG_VALUE = "WRONG_VALUE"
    MISSING = "MISSING"
    UNEXPECTED = "UNEXPECTED"
    UNPARSEABLE = "UNPARSEABLE"
    CALIBER_MISMATCH = "CALIBER_MISMATCH"


class CaseStatus(str, Enum):
    PASS = "PASS"
    PARTIAL = "PARTIAL"
    FAIL = "FAIL"
    REVIEW = "REVIEW"
    NOT_SCORED = "NOT_SCORED"


class RunStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    INTERRUPTED = "INTERRUPTED"
    FAILED = "FAILED"


class ErrorType(str, Enum):
    WRONG_VALUE = "WRONG_VALUE"
    MISSING = "MISSING"
    UNEXPECTED = "UNEXPECTED"
    UNIT_ERROR = "UNIT_ERROR"
    ROW_KEY_UNMATCHED = "ROW_KEY_UNMATCHED"
    EXTRACT_FAIL = "EXTRACT_FAIL"
    SQL_FAIL = "SQL_FAIL"
    AGENT_FAIL = "AGENT_FAIL"
    SQL_NOT_REALTIME_READY = "SQL_NOT_REALTIME_READY"
    WATERMARK_CHANGED = "WATERMARK_CHANGED"
    NON_NUMERIC = "NON_NUMERIC"
    GRAIN_MISMATCH = "GRAIN_MISMATCH"
    CALIBER_MISMATCH = "CALIBER_MISMATCH"


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
    last_7_start: str
    last_7_end: str
    last_30_start: str
    last_30_end: str
    year_start: str
    today: str
    yesterday: str
    week_start: str
    jan_may_period: str
    yoy_month: str
    last_6_months: List[str] = Field(default_factory=list)
    last_3_months: List[str] = Field(default_factory=list)
    last_12_months: List[str] = Field(default_factory=list)
    quarter_start: str = ""
    prev_quarter_start: str = ""
    prev_quarter_end: str = ""
    effective_single_month: str = ""
    effective_year_month: str = ""
    agent_name: str = "water-loss-agent"
    contract_version: str = "v3"
    consistency_threshold: float = 0.6
    latest_periods: Dict[str, int] = Field(default_factory=dict)
    bind_params: Dict[str, Any] = Field(default_factory=dict)


class NumericClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str = ""
    case_id: str
    source: str
    coordinates: Dict[str, str] = Field(default_factory=dict)
    metric: str
    raw_value: Optional[str] = None
    value: Optional[float] = None
    unit: str = ""
    evidence: str = ""
    extractor: str = ""
    aggregation: str = ""
    period: str = ""
    period_role: str = ""
    scale_token: str = ""
    conversion_factor: Optional[float] = None
    confidence: str = "deterministic"
    evidence_start: Optional[int] = None
    evidence_end: Optional[int] = None

    @field_validator("source")
    @classmethod
    def _source(cls, value: str) -> str:
        if value not in {"agent", "sql"}:
            raise ValueError("source must be 'agent' or 'sql'")
        return value

    @field_validator("confidence")
    @classmethod
    def _confidence(cls, value: str) -> str:
        if value not in {"deterministic", "heuristic", "llm", "unparseable"}:
            raise ValueError("invalid confidence")
        return value


class ComparisonItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    coordinates: Dict[str, str] = Field(default_factory=dict)
    metric: str
    expected_value: Optional[float] = None
    actual_value: Optional[float] = None
    expected_raw: Optional[str] = None
    actual_raw: Optional[str] = None
    delta: Optional[float] = None
    tolerance: Optional[float] = None
    status: ClaimStatus
    evidence: str = ""
    evidence_claim_id: str = ""
    unit: str = ""
    note: str = ""
    aggregation: str = ""
    normalization_rule: str = ""


class CaseMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    match_count: int = 0
    wrong_count: int = 0
    missing_count: int = 0
    unexpected_count: int = 0
    unparseable_count: int = 0
    accuracy: Optional[float] = None
    coverage: Optional[float] = None
    row_coverage: Optional[float] = None
    required_claims: int = 0
    returned_required: int = 0
    auto_resolved: bool = True


class AgentAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    question: str
    text: str = ""
    raw_response_path: str = ""
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
    comparison_items: List[ComparisonItem] = Field(default_factory=list)
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
    contract_version: str = "v3"
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
    mode: str = "live"
    consistency_threshold: float = 0.6
    timing_summary: Dict[str, Any] = Field(default_factory=dict)
