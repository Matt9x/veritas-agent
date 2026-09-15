from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Budget(StrictModel):
    # Initial values frozen in docs/V0.1-IMPLEMENTATION-SPEC.md section 16.1.
    max_tool_calls: StrictInt = Field(default=60, gt=0)
    max_model_requests: StrictInt = Field(default=80, gt=0)
    max_elapsed_s: StrictInt = Field(default=1800, gt=0)
    max_repair_cycles: StrictInt = Field(default=3, ge=0)
    max_consecutive_format_errors: StrictInt = Field(default=5, ge=0)
    max_same_failure_without_new_evidence: StrictInt = Field(default=2, ge=0)
    max_output_chars: StrictInt = Field(default=20_000, gt=0)
    # Per-tool output and command ceilings (section 16.1).
    max_read_lines: StrictInt = Field(default=400, gt=0)
    max_read_bytes: StrictInt = Field(default=65_536, gt=0)
    max_search_matches: StrictInt = Field(default=200, gt=0)
    max_command_output_bytes: StrictInt = Field(default=262_144, gt=0)
    max_diff_output_bytes: StrictInt = Field(default=2_097_152, gt=0)
    max_command_timeout_s: StrictInt = Field(default=300, gt=0)


INTENT_ASSERTION_KINDS: frozenset[str] = frozenset(
    {
        "required_changed",       # params: {"paths": [...]}
        "unchanged",              # params: {"paths": [...]}
        "required_import",        # params: {"path", "module", "name"}
        "required_module_constant",  # params: {"path", "name", "value"}
        "required_call_result",   # params: {"path", "function", "called_function", "delta"?}
        "required_return_name",   # params: {"path", "function", "name"}
        "forbidden_module_constant",  # params: {"path", "name", "value"}
        "forbidden_text",         # params: {"path", "pattern"}
        "required_symbol",        # params: {"path", "symbol", "symbol_kind"}
    }
)


class ProtectedCheck(StrictModel):
    """A file that constitutes part of a task's verification.

    The file may grow -- adding tests is good work -- but it must not lose
    coverage. Deleting a test, or an assertion, makes the check pass for a
    reason unrelated to the task, which is the textbook way for an agent to
    improve its score without improving the work.

    `min_methods` / `min_assertions` are the surface measured on the fixture the
    task starts from. `scripts/generate_protected_checks.py` derives them from
    the fixture so that the declaration cannot drift from reality.
    """

    path: StrictStr
    min_methods: StrictInt = Field(ge=0)
    min_assertions: StrictInt = Field(ge=0)


class Criterion(StrictModel):
    criterion_id: StrictStr
    description: StrictStr
    evidence_method: StrictStr
    # Declarative hint for evidence binding: observations touching these paths are
    # eligible evidence for this criterion. Empty means "no path constraint".
    evidence_paths: list[StrictStr] = Field(default_factory=list)


class IntentAssertion(StrictModel):
    """A declarative, machine-checkable statement about *how* a task must be solved.

    Verification commands can only assert what is expressible as a command. Some
    requirements (keep a call chain, do not inline an upstream constant, keep a
    symbol's shape) are structural and would otherwise be bypassable while every
    command still exits 0. An assertion states that requirement once, in the
    TaskSpec, so any runtime's completion gate can evaluate it.

    The model is deliberately generic: it carries no task identity, and the
    evaluator that consumes it (Runtime Verifier) never receives a task id.
    """

    assertion_id: StrictStr
    criterion_ids: list[StrictStr] = Field(min_length=1)
    # One of the kinds supported by the declaration-order evaluator.
    kind: StrictStr
    # Kind-specific arguments. See the verifier's evaluator table for the schema
    # of each kind (e.g. {"path": "c.py", "function": "get_c",
    # "called_function": "get_b", "delta": 1}).
    params: dict[str, Any] = Field(default_factory=dict)
    description: StrictStr = ""

    @model_validator(mode="after")
    def validate_kind(self) -> "IntentAssertion":
        if self.kind not in INTENT_ASSERTION_KINDS:
            raise ValueError(f"unknown intent assertion kind: {self.kind}")
        return self


class VerificationCheck(StrictModel):
    check_id: StrictStr
    criterion_ids: list[StrictStr] = Field(min_length=1)
    argv: list[StrictStr] = Field(min_length=1)
    cwd: StrictStr = "."
    environment_profile: StrictStr = "default"
    timeout_s: StrictInt = Field(default=120, gt=0)
    expected_exit: StrictInt = 0
    result_match: dict[str, Any] = Field(default_factory=dict)
    applicable: bool = True


class TaskSpec(StrictModel):
    task_id: StrictStr
    spec_version: StrictInt = Field(default=1, ge=1)
    repo_root: StrictStr
    request: StrictStr
    success_conditions: list[Criterion] = Field(min_length=1)
    allowed_paths: list[StrictStr] = Field(default_factory=lambda: ["."])
    must_not_modify: list[StrictStr] = Field(default_factory=list)
    # Files that carry this task's verification. They may gain coverage but must
    # never lose it.
    protected_checks: list[ProtectedCheck] = Field(default_factory=list)
    verification_plan: list[VerificationCheck] = Field(default_factory=list)
    execution_profile: StrictStr = "default"
    # When True, a DONE proposal without any successful apply_patch observation
    # fails the Final Goal Gate ("no-op completion"). None keeps legacy behavior.
    expects_artifact_change: StrictBool | None = None
    # Structural requirements that must hold for the goal to be considered met.
    # Declarative and task-agnostic: evaluated by the Runtime Verifier without
    # any knowledge of which task the spec belongs to.
    intent_assertions: list[IntentAssertion] = Field(default_factory=list)
    budget: Budget = Field(default_factory=Budget)

    @model_validator(mode="after")
    def validate_verification_refs(self) -> "TaskSpec":
        criterion_ids = {criterion.criterion_id for criterion in self.success_conditions}
        unknown = {
            criterion_id
            for check in self.verification_plan
            for criterion_id in check.criterion_ids
            if criterion_id not in criterion_ids
        }
        if unknown:
            raise ValueError(f"verification plan references unknown criteria: {sorted(unknown)}")
        assertion_unknown = {
            criterion_id
            for assertion in self.intent_assertions
            for criterion_id in assertion.criterion_ids
            if criterion_id not in criterion_ids
        }
        if assertion_unknown:
            raise ValueError(f"intent assertions reference unknown criteria: {sorted(assertion_unknown)}")
        return self


class ObservationStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    DENIED = "DENIED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"


class SideEffectOutcome(StrEnum):
    NONE = "NONE"
    APPLIED = "APPLIED"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"


class VerificationConclusion(StrEnum):
    PASS = "PASS"
    PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"
    REPAIR_REQUIRED = "REPAIR_REQUIRED"
    MORE_EVIDENCE_REQUIRED = "MORE_EVIDENCE_REQUIRED"
    BLOCKED = "BLOCKED"


class EvidenceRecord(StrictModel):
    evidence_id: StrictStr
    run_id: StrictStr
    source: StrictStr
    locator: StrictStr
    artifact_ref: StrictStr
    criterion_id: StrictStr | None = None
    invocation_id: StrictStr | None = None
    content_hash: StrictStr | None = None
    fresh: bool = True


class Observation(StrictModel):
    invocation_id: StrictStr
    tool: StrictStr
    status: ObservationStatus
    error_class: StrictStr | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    exit_code: StrictInt | None = None
    duration_ms: StrictInt = Field(default=0, ge=0)
    evidence_refs: list[StrictStr] = Field(default_factory=list)
    truncated: bool = False
    side_effect_outcome: SideEffectOutcome = SideEffectOutcome.NONE
    affected_files: list[StrictStr] = Field(default_factory=list)


class GateResult(StrictModel):
    gate: StrictStr
    status: StrictStr
    reason: StrictStr
    evidence_refs: list[StrictStr] = Field(default_factory=list)


class GateCoverage(StrictModel):
    """What the verifier actually did, as opposed to what it concluded.

    A PASS conclusion means "no check objected". It must never be read as "every
    check ran". This records the difference, so that a missing capability (for
    example, no type checker installed) stays visible instead of being silently
    folded into NOT_APPLICABLE.
    """

    gates_evaluated: StrictInt = 0
    gates_passed: StrictInt = 0
    gates_failed: StrictInt = 0
    gates_not_applicable: StrictInt = 0
    gates_unavailable: StrictInt = 0
    gates_unknown: StrictInt = 0
    unavailable_checks: list[StrictStr] = Field(default_factory=list)
    blocking_checks: list[StrictStr] = Field(default_factory=list)


class VerificationReport(StrictModel):
    run_id: StrictStr
    spec_version: StrictInt
    artifact_ref: StrictStr
    policy_version: StrictStr
    gates: list[GateResult]
    criterion_evidence: dict[str, list[str]] = Field(default_factory=dict)
    evidence_records: list[EvidenceRecord] = Field(default_factory=list)
    warnings: list[StrictStr] = Field(default_factory=list)
    blocking_items: list[StrictStr] = Field(default_factory=list)
    conclusion: VerificationConclusion
    verifier_command_executions: StrictInt = Field(default=0, ge=0)
    # Discloses which checks executed. None only for reports written before this
    # field existed; new reports always populate it.
    coverage: GateCoverage | None = None
