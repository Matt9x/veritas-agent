from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from collections.abc import Iterable
from fnmatch import fnmatch
from pathlib import Path
from typing import NamedTuple

from .subprocess_util import run_bounded
from .contracts import (
    EvidenceRecord,
    GateCoverage,
    GateResult,
    Observation,
    ObservationStatus,
    ProtectedCheck,
    TaskSpec,
    VerificationCheck,
    VerificationConclusion,
    VerificationReport,
)
from .state import RunState
from .verification_debt import normalize_argv

GATE_PASS = "PASS"
GATE_FAIL = "FAIL"
GATE_NOT_APPLICABLE = "NOT_APPLICABLE"
# The check is applicable in principle but its capability is absent in this
# environment (for example no type checker is installed). Distinct from
# NOT_APPLICABLE: "there was nothing to check" vs "we could not check".
# Non-blocking by design -- a missing tool must not become friction -- but it
# must never be indistinguishable from a passing check.
GATE_UNAVAILABLE = "UNAVAILABLE"
GATE_UNKNOWN = "UNKNOWN"

# Gate order follows docs/V0.1-IMPLEMENTATION-SPEC.md section 12.
GATE_ORDER = (
    "Intent Gate",
    "Diff Gate",
    "Syntax Gate",
    "Typecheck Gate",
    "Lint Gate",
    "Tests Gate",
    "Evidence Gate",
    "Intent Assertion Gate",
    "Regression Gate",
    "Destructive Pattern Gate",
    "Final Goal Gate",
)

_DESTRUCTIVE_COMMANDS = {"rm", "rmdir", "del", "erase", "format", "mkfs", "diskpart", "shutdown", "takeown", "truncate"}
_DESTRUCTIVE_FLAGS = {"-rf", "-fr", "-r", "-f", "--force", "--hard", "/f", "/s", "/q", "-fd", "-df"}
_FORCED_GIT_SUBCOMMANDS = {"push", "reset", "clean"}
_SENSITIVE_NAME_TOKENS = ("secret", "credential", "password", "token", "apikey", "api_key")
_SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx")

_TYPECHECK_CANDIDATES = (
    ("mypy", ["mypy"], ["mypy"]),
    ("pyright", ["pyright"], ["pyright"]),
)
_LINT_CANDIDATES = (
    ("ruff", ["ruff", "check"], ["ruff"]),
    ("flake8", ["flake8"], ["flake8"]),
    ("pylint", ["pylint"], ["pylint"]),
)

# --- Semantic completion contract -------------------------------------------
# The P0 authority defect: a semantically wrong patch plus already-green checks
# reached PASS/DONE, because nothing tied a criterion to *how* its evidence was
# obtained or to any demonstration that the change caused correctness. Two
# deterministic rules close that hole; both live inside RuleVerifier, which
# stays the sole DONE authority. No model, no network, no new authority.

# Every value ``EvidenceRecord.source`` can carry in a real run is
# ``Observation.tool``, and ``Observation.tool`` is set by ToolRuntime from its
# own tool table. This is that table, transcribed -- the mapping is read off the
# product, never guessed.
CANONICAL_EVIDENCE_SOURCES: frozenset[str] = frozenset(
    {
        "list_files",
        "read_file",
        "search",
        "apply_patch",
        "run_command",
        "git_status",
        "git_diff",
        "run_tests",
    }
)
# The runtime's own alias table (ToolRuntime._aliases).
EVIDENCE_SOURCE_ALIASES: dict[str, str] = {
    "list_directory": "list_files",
    "search_files": "search",
}

# Intent assertions are not all alike. Only an assertion that parses the
# artifact and states something about its content can anchor the claim that the
# requested behaviour exists. The split below is mechanical rather than a
# judgement call: the CONSTRAINT_ONLY kinds are exactly the two whose evaluator
# consults nothing but the set of changed paths -- the mutation ledger -- so a
# passing `required_changed` says "something was edited", which is precisely the
# claim that must not be mistaken for semantic proof.
SEMANTIC_ANCHOR_ASSERTION_KINDS: frozenset[str] = frozenset(
    {
        "required_import",
        "required_module_constant",
        "forbidden_module_constant",
        "required_call_result",
        "required_return_name",
        "required_symbol",
        "forbidden_text",
    }
)
CONSTRAINT_ONLY_ASSERTION_KINDS: frozenset[str] = frozenset({"required_changed", "unchanged"})


def _declared_evidence_sources(method: str) -> frozenset[str]:
    """Normalize a declared ``Criterion.evidence_method`` to canonical sources.

    The declared vocabulary is a ``/``-separated list of source tokens -- the
    shipped benchmark specs write ``"run_tests/git_diff"`` and
    ``"read_file/search"`` -- so each token is resolved exactly, or through the
    runtime's alias table. A token that names no source the product can produce
    (``"observation"``, ``"Final Goal Verification"``) is dropped rather than
    guessed at. A method that resolves to *nothing* is unsupported, and the
    caller fails closed on it.
    """
    sources: set[str] = set()
    for token in str(method or "").split("/"):
        token = token.strip().lower()
        if not token:
            continue
        canonical = EVIDENCE_SOURCE_ALIASES.get(token, token)
        if canonical in CANONICAL_EVIDENCE_SOURCES:
            sources.add(canonical)
    return frozenset(sources)


def _evidence_record_source(record: EvidenceRecord, observation_by_id: dict) -> str:
    """The source a bound record actually evidences.

    ``Observation.tool`` is product-owned; ``EvidenceRecord.source`` is that same
    value copied at bind time. Prefer the observation, so a record cannot claim a
    method its own observation never used.
    """
    observation = observation_by_id.get(record.invocation_id)
    if observation is not None and observation.tool:
        return str(observation.tool)
    return str(record.source or "")


def _status_is(status, expected) -> bool:
    """Compare an ObservationStatus that may have been through JSON."""
    return str(status) == str(expected)


def _actual_artifact_mutation(observations: list[Observation]) -> bool:
    """True when the run actually changed the artifact.

    `expects_artifact_change` is a declaration; this is the fact. A spec that
    omits the flag (`None`, the legacy value) is not evidence that the run stayed
    read-only, so the completion contract is keyed on the fact as well.
    """
    return any(
        observation.tool == "apply_patch" and observation.status == ObservationStatus.SUCCEEDED
        for observation in observations
    )


def _mutation_paths(observation: Observation) -> list[str]:
    """The paths a patch observation itself reports touching.

    Product-owned facts only: the runtime's `affected_files`, and the change
    entries the patch was applied from. Never model prose.
    """
    paths = [str(item) for item in (observation.affected_files or []) if str(item)]
    changes = observation.data.get("changes")
    if isinstance(changes, list):
        for change in changes:
            if isinstance(change, dict) and change.get("path"):
                paths.append(str(change["path"]))
    return [path.replace("\\", "/") for path in paths]


def _patch_touches(observation: Observation, declared_paths) -> bool:
    """Whether this mutation touched any declared path.

    With no declared path contract there is nothing narrower to enforce, so the
    mutation counts. When a contract exists, only a matching path does.
    """
    declared = [str(item).replace("\\", "/").strip() for item in (declared_paths or [])]
    declared = [item for item in declared if item]
    if not declared:
        return True
    for path in _mutation_paths(observation):
        for pattern in declared:
            if path == pattern or path.startswith(pattern.rstrip("/") + "/") or fnmatch(path, pattern):
                return True
    return False


class DeclaredCheckExecution(NamedTuple):
    """One applicable declared check, resolved against the trailing plan run.

    There is exactly ONE declared-check execution truth. The Tests Gate, the
    Final Goal Gate's criterion coverage, and the evidence auto-binder all read
    this, so they cannot drift apart: a check that is not *satisfied* here is
    unsatisfied everywhere.
    """

    check: VerificationCheck
    observation: Observation | None
    command_identity_match: bool
    status: ObservationStatus | None

    @property
    def satisfied(self) -> bool:
        """The check ran, its own argv ran, and it passed.

        A missing observation, a command that is not this check's argv, or any
        non-SUCCEEDED status (FAILED, DENIED, OUTCOME_UNKNOWN) all mean the
        declared check was not satisfied.
        """
        return (
            self.observation is not None
            and self.command_identity_match
            and self.observation.status == ObservationStatus.SUCCEEDED
        )


def _command_events(observations: list[Observation]) -> list[tuple[int, tuple[str, ...], bool, bool]]:
    """Flatten executing observations to (index, normalized argv, failed, ok).

    One ``run_tests`` observation aggregates several commands, each with its own
    exit; the per-command result is the honest unit, so it is the unit used here.
    `failed` and `ok` are each true only for the status they name: a DENIED or
    OUTCOME_UNKNOWN command is neither a failure nor a pass.
    """
    events: list[tuple[int, tuple[str, ...], bool, bool]] = []
    for index, observation in enumerate(observations):
        if observation.tool == "run_command":
            argv = observation.data.get("argv")
            if isinstance(argv, list) and argv:
                events.append(
                    (
                        index,
                        normalize_argv(argv),
                        _status_is(observation.status, ObservationStatus.FAILED),
                        _status_is(observation.status, ObservationStatus.SUCCEEDED),
                    )
                )
            continue
        if observation.tool != "run_tests":
            continue
        results = observation.data.get("results")
        if not isinstance(results, list):
            continue
        for result in results:
            if not isinstance(result, dict):
                continue
            argv = result.get("argv")
            if not isinstance(argv, list) or not argv:
                continue
            status = result.get("status", observation.status)
            events.append(
                (
                    index,
                    normalize_argv(argv),
                    _status_is(status, ObservationStatus.FAILED),
                    _status_is(status, ObservationStatus.SUCCEEDED),
                )
            )
    return events


def _intent_load_tree(root: Path, relative: str):
    if not relative:
        return None
    try:
        return ast.parse((root / relative).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeError):
        return None


def _intent_has_import(tree, module: str, name: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module:
            if any(alias.name == name for alias in node.names):
                return True
    return False


def _intent_module_constant(tree, name: str):
    if not name:
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name:
            if isinstance(node.value, ast.Constant) and type(node.value.value) is int:
                return node.value.value
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            if isinstance(node.value, ast.Constant) and type(node.value.value) is int:
                return node.value.value
    return None


def _intent_any_constant(tree, name: str):
    """Any integer assignment to `name` anywhere in the module (detects inlining)."""
    if not name:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name:
            if isinstance(node.value, ast.Constant) and type(node.value.value) is int:
                return node.value.value
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            if isinstance(node.value, ast.Constant) and type(node.value.value) is int:
                return node.value.value
    return None


def _intent_returns_called_with_delta(tree, function: str, called_function: str, delta: int, expected_args: list[str] | None = None) -> bool:
    """`expected_args` is the declared positional argument shape. An empty list
    (the default) means the delegated call must take no arguments."""
    if not function or not called_function:
        return False
    for node in ast.walk(tree):
        if not (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function):
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Return) or child.value is None:
                continue
            value = child.value
            call = None
            if isinstance(value, ast.Call):
                call = value if delta == 0 else None
            elif isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add):
                for left, right in ((value.left, value.right), (value.right, value.left)):
                    if isinstance(left, ast.Call) and isinstance(right, ast.Constant) and right.value == delta:
                        call = left
                        break
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
                continue
            if call.func.id != called_function or call.keywords:
                continue
            declared = list(expected_args or [])
            actual = [arg.id for arg in call.args if isinstance(arg, ast.Name)]
            if len(actual) != len(call.args) or actual != declared:
                continue
            return True
    return False


def _intent_returns_name(tree, function: str, name: str) -> bool:
    if not function or not name:
        return False
    for node in ast.walk(tree):
        if not (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Return) and isinstance(child.value, ast.Name) and child.value.id == name:
                return True
    return False


def _intent_has_symbol(tree, symbol: str, symbol_kind: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol and symbol_kind in ("any", "function"):
            return True
        if isinstance(node, ast.ClassDef) and node.name == symbol and symbol_kind in ("any", "class"):
            return True
    return False


def _intent_observation_touches(paths: list[str], observation) -> bool:
    """True when the observation payload references one of the declared paths."""
    if observation is None:
        return False
    try:
        payload = json.dumps(observation.data, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        payload = str(observation.data)
    return any(path and path in payload for path in paths)

def _check_surface(path: Path) -> dict[str, int] | None:
    """Test surface of one file, or None when it is gone or does not parse.

    Implemented here independently of the oracle's own counter: the Verifier and
    the Oracle may share the declaration, never the evaluator.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    methods = 0
    assertions = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            methods += 1
        if isinstance(node, ast.Assert):
            assertions += 1
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr.startswith("assert"):
            assertions += 1
    return {"methods": methods, "assertions": assertions}


class RuleVerifier:
    """Independent deterministic completion gate; it never calls a model."""

    def __init__(self, *, workspace_root: Path | None = None, checker_timeout_s: int = 120):
        self.workspace_root = Path(workspace_root).resolve() if workspace_root is not None else None
        self.checker_timeout_s = checker_timeout_s

    def verify(
        self,
        spec: TaskSpec,
        state: RunState,
        observations: Iterable[Observation],
        criterion_evidence: dict[str, list[EvidenceRecord]],
        *,
        artifact_ref: str = "workspace",
    ) -> VerificationReport:
        observations = list(observations)
        observed_ids = {observation.invocation_id for observation in observations}
        successful_ids = {
            observation.invocation_id
            for observation in observations
            if observation.status == ObservationStatus.SUCCEEDED
        }
        gates: list[GateResult] = []
        blocking: list[str] = []
        warnings: list[str] = []
        changed_files = sorted(state.veritas_files or state.files_touched)

        def add(gate: str, status: str, reason: str, evidence_refs: list[str] | None = None) -> None:
            gates.append(GateResult(gate=gate, status=status, reason=reason, evidence_refs=evidence_refs or []))

        # The runner executes the applicable declared checks at propose_finish --
        # AFTER the model proposed completion, so the model cannot name their
        # invocation ids. Bind that product-owned evidence to the criteria those
        # checks declare before anything is judged, so a criterion declaring
        # `run_tests` is actually supported by a test that ran.
        criterion_evidence = self._with_declared_check_evidence(spec, state, observations, criterion_evidence, artifact_ref)

        # --- Intent Gate: every success criterion needs fresh, bound evidence. ---
        missing = [criterion.criterion_id for criterion in spec.success_conditions if not criterion_evidence.get(criterion.criterion_id)]
        invalid_refs = []
        evidence_records = []
        for criterion_id, records in criterion_evidence.items():
            for record in records:
                evidence_records.append(record)
                if (
                    record.run_id != state.run_id
                    or record.criterion_id != criterion_id
                    or record.artifact_ref != artifact_ref
                    or not record.fresh
                    or record.invocation_id not in observed_ids
                ):
                    invalid_refs.append(f"{criterion_id}:{record.evidence_id}")
        # A criterion that declares evidence_paths must be supported by an
        # observation that actually touched one of those paths. Without this,
        # any successful observation can be bound to any criterion and the gate
        # degenerates into "something happened".
        observation_by_id = {observation.invocation_id: observation for observation in observations}
        off_path = []
        for criterion in spec.success_conditions:
            if not criterion.evidence_paths:
                continue
            bound = criterion_evidence.get(criterion.criterion_id) or []
            if not any(_intent_observation_touches(criterion.evidence_paths, observation_by_id.get(record.invocation_id)) for record in bound):
                off_path.append(criterion.criterion_id)
        # A declared evidence_method must constrain the bound evidence: a
        # criterion asking for `run_tests` is not satisfied by a patch. A method
        # that names no source the product can produce is unsupported, and an
        # unsupported declaration fails closed rather than being ignored.
        method_mismatch = []
        for criterion in spec.success_conditions:
            bound = criterion_evidence.get(criterion.criterion_id) or []
            if not bound:
                continue  # already reported as `missing`
            declared = _declared_evidence_sources(criterion.evidence_method)
            if not declared or not any(_evidence_record_source(record, observation_by_id) in declared for record in bound):
                method_mismatch.append(criterion.criterion_id)
        if missing or invalid_refs or method_mismatch or off_path:
            if missing:
                reason = "missing criterion evidence"
            elif invalid_refs:
                reason = "criterion evidence does not reference an observation"
            elif method_mismatch:
                reason = "criterion evidence does not use the declared evidence method"
            else:
                reason = "criterion evidence does not touch the declared evidence paths"
            add("Intent Gate", GATE_FAIL, reason)
            blocking.extend(missing)
            blocking.extend(invalid_refs)
            blocking.extend(f"evidence-method:{item}" for item in method_mismatch)
            blocking.extend(f"off-path:{item}" for item in off_path)
        else:
            add("Intent Gate", GATE_PASS, "all criteria have bound evidence", [record.evidence_id for records in criterion_evidence.values() for record in records])

        # --- Diff Gate: no forbidden or pre-existing file was rewritten. ---
        forbidden = sorted(
            {
                path
                for path in state.files_touched
                if any(path == pattern or fnmatch(path, pattern) for pattern in spec.must_not_modify)
            }
        )
        if state.overlap_conflicts:
            add("Diff Gate", GATE_FAIL, "pre-existing files changed during the run", list(state.overlap_conflicts))
            blocking.extend(state.overlap_conflicts)
        elif forbidden:
            add("Diff Gate", GATE_FAIL, "forbidden files were touched", forbidden)
            blocking.extend(forbidden)
        else:
            add("Diff Gate", GATE_PASS, "no forbidden file was recorded", list(state.files_touched))

        python_files = [path for path in changed_files if path.endswith(".py")]

        # --- Syntax Gate ---
        self._syntax_gate(add, blocking, python_files)

        # --- Typecheck Gate ---
        self._external_checker_gate(add, blocking, "Typecheck Gate", _TYPECHECK_CANDIDATES, python_files, "type checker")

        # --- Lint Gate ---
        self._external_checker_gate(add, blocking, "Lint Gate", _LINT_CANDIDATES, python_files, "linter")

        # --- Tests Gate ---
        test_observations = [observation for observation in observations if observation.tool == "run_tests"]
        if spec.verification_plan:
            # EVERY applicable declared check must have run and passed. "The last
            # run_tests succeeded" is a weaker statement: an earlier declared
            # check may have failed, or the trailing observation may not be this
            # check's command at all.
            executions = self._declared_check_executions(spec, observations)
            unsatisfied = [execution for execution in executions if not execution.satisfied]
            if not executions or unsatisfied:
                add(
                    "Tests Gate",
                    GATE_FAIL,
                    "not every applicable declared check ran and passed",
                    [execution.check.check_id for execution in unsatisfied] or ["no-applicable-check"],
                )
                blocking.append("verification_plan")
            else:
                add(
                    "Tests Gate",
                    GATE_PASS,
                    "every applicable declared check ran and passed",
                    [execution.check.check_id for execution in executions],
                )
        else:
            # A gate that simply disappears is indistinguishable from a gate that
            # passed. Always account for it.
            add("Tests Gate", GATE_NOT_APPLICABLE, "the spec declares no verification plan")

        # --- Evidence Gate ---
        if not successful_ids:
            add("Evidence Gate", GATE_FAIL, "no successful observation supports the proposal")
            blocking.append("no-successful-observation")
        else:
            add("Evidence Gate", GATE_PASS, "at least one successful observation supports the proposal", sorted(successful_ids)[:20])

        # --- Intent Assertion Gate: declarative structural requirements. ---
        # The gate returns the criteria its *passing* assertions establish: that
        # is one of the two deterministic semantic-proof classes the Final Goal
        # Gate accepts for an artifact-changing task.
        structurally_proven = self._intent_assertion_gate(add, blocking, warnings, spec, changed_files)

        # --- Regression Gate ---
        self._regression_gate(add, blocking, spec, test_observations, changed_files)

        # --- Destructive Pattern Gate ---
        self._security_gate(add, blocking, observations, changed_files, spec)

        # --- Final Goal Gate ---
        self._final_goal_gate(add, blocking, warnings, spec, criterion_evidence, observations, structurally_proven)

        # Verifier-internal command executions are real work; report them.
        verifier_command_executions = getattr(self, "last_checker_executions", 0)

        conclusion = VerificationConclusion.PASS
        if blocking:
            conclusion = VerificationConclusion.REPAIR_REQUIRED
        elif any(gate.status == GATE_UNKNOWN for gate in gates):
            conclusion = VerificationConclusion.MORE_EVIDENCE_REQUIRED
        elif warnings:
            conclusion = VerificationConclusion.PASS_WITH_WARNINGS

        # No gate may be silently absent: a missing entry is indistinguishable
        # from a check that passed. Any known gate that did not report a status
        # is recorded explicitly.
        reported = {gate.gate for gate in gates}
        for name in GATE_ORDER:
            if name not in reported:
                add(name, GATE_NOT_APPLICABLE, "this gate did not report a status in this run")

        ordered = sorted(gates, key=lambda gate: GATE_ORDER.index(gate.gate) if gate.gate in GATE_ORDER else len(GATE_ORDER))
        coverage = GateCoverage(
            gates_evaluated=len(ordered),
            gates_passed=sum(1 for gate in ordered if gate.status == GATE_PASS),
            gates_failed=sum(1 for gate in ordered if gate.status == GATE_FAIL),
            gates_not_applicable=sum(1 for gate in ordered if gate.status == GATE_NOT_APPLICABLE),
            gates_unavailable=sum(1 for gate in ordered if gate.status == GATE_UNAVAILABLE),
            gates_unknown=sum(1 for gate in ordered if gate.status == GATE_UNKNOWN),
            unavailable_checks=sorted(gate.gate for gate in ordered if gate.status == GATE_UNAVAILABLE),
            blocking_checks=sorted({item.split(":", 1)[0] for item in blocking}) if blocking else [],
        )
        # NOTE: coverage is disclosed through the structured `coverage` field and
        # must NOT append a warning. `warnings` feeds the conclusion (PASS ->
        # PASS_WITH_WARNINGS), and the two runtimes disagree on that value:
        # runner.py treats only PASS as completion while run_phase2.py accepts
        # both. Emitting a warning here would therefore turn every native DONE
        # into BLOCKED on any machine without a type checker. Disclosure is
        # informational; changing the verdict is not.
        return VerificationReport(
            run_id=state.run_id,
            spec_version=spec.spec_version,
            artifact_ref=artifact_ref,
            policy_version="v0.1",
            gates=ordered,
            criterion_evidence={criterion_id: [record.evidence_id for record in records] for criterion_id, records in criterion_evidence.items()},
            evidence_records=evidence_records,
            warnings=warnings,
            blocking_items=blocking,
            conclusion=conclusion,
            verifier_command_executions=verifier_command_executions,
            coverage=coverage,
        )

    # ------------------------------------------------- declared check binding

    def _with_declared_check_evidence(
        self,
        spec: TaskSpec,
        state: RunState,
        observations: list[Observation],
        criterion_evidence: dict[str, list[EvidenceRecord]],
        artifact_ref: str,
    ) -> dict[str, list[EvidenceRecord]]:
        """Attach actually-executed declared checks to the criteria they declare.

        The execution order is the problem this solves: the actor proposes
        completion *before* the runner runs the trailing declared verification
        plan, so the actor cannot know the future invocation ids and must not be
        asked to predict them. The mapping already exists in the spec --
        ``VerificationCheck.criterion_ids`` -- and the runner executes the
        applicable checks in declaration order immediately before verification,
        appending one ``run_tests`` observation per check. So the trailing
        executing observations map onto the applicable checks one by one, and the
        binding is decided entirely by product-owned facts.

        Only a check that actually executed and succeeded contributes; the
        observation must also be shown to have run that check's own command, so
        an unrelated trailing ``run_tests`` cannot be mistaken for it. The result
        is a fresh, provenance-preserving record per declared criterion -- and
        for those criteria only.
        """
        augmented = {criterion_id: list(records) for criterion_id, records in criterion_evidence.items()}
        for execution in self._declared_check_executions(spec, observations):
            if not execution.satisfied:
                continue
            check, observation = execution.check, execution.observation
            for criterion_id in check.criterion_ids:
                records = augmented.setdefault(criterion_id, [])
                if any(record.invocation_id == observation.invocation_id for record in records):
                    continue
                records.append(
                    EvidenceRecord(
                        evidence_id=f"ev-check-{check.check_id}-{observation.invocation_id}",
                        run_id=state.run_id,
                        source=observation.tool,
                        locator=f"invocation:{observation.invocation_id}",
                        artifact_ref=artifact_ref,
                        criterion_id=criterion_id,
                        invocation_id=observation.invocation_id,
                        content_hash=observation.data.get("content_hash"),
                        fresh=True,
                    )
                )
        return augmented

    @staticmethod
    def _declared_check_executions(spec: TaskSpec, observations: list[Observation]) -> list[DeclaredCheckExecution]:
        """Resolve the trailing applicable verification plan into exact pairs.

        THE declared-check execution truth. The Tests Gate, the Final Goal Gate's
        criterion coverage and the evidence auto-binder all read this one
        resolution, so "did this declared check pass?" cannot be answered three
        different ways.

        Boundary: the runner executes the applicable checks in declaration order
        immediately before verification, so the trailing ``run_tests``
        observations pair with them positionally -- but a pair is only real once
        the observation is proven to have run that check's own argv. Nothing
        searches earlier observations for a substitute, and nothing reorders the
        plan to turn a failure into a pass: a command mismatch stays a mismatch.
        """
        checks = [check for check in RuleVerifier._normalize_checks(spec.verification_plan) if check.applicable]
        if not checks:
            return []
        executed = [observation for observation in observations if observation.tool == "run_tests"]
        trailing = executed[-len(checks):] if len(executed) >= len(checks) else []
        resolved: list[DeclaredCheckExecution] = []
        for index, check in enumerate(checks):
            observation = trailing[index] if index < len(trailing) else None
            resolved.append(
                DeclaredCheckExecution(
                    check=check,
                    observation=observation,
                    command_identity_match=observation is not None and RuleVerifier._observation_ran_check(check, observation),
                    status=observation.status if observation is not None else None,
                )
            )
        return resolved

    @staticmethod
    def _observation_ran_check(check: VerificationCheck, observation: Observation) -> bool:
        """True when this observation's own argv IS the check's declared argv."""
        expected = normalize_argv(check.argv)
        if not expected:
            return False
        return any(normalize_argv(signature) == expected for signature in RuleVerifier._command_signature(observation))

    # ------------------------------------------------------------------ gates

    def _syntax_gate(self, add, blocking: list[str], python_files: list[str]) -> None:
        if self.workspace_root is None:
            add("Syntax Gate", GATE_NOT_APPLICABLE, "verifier was given no workspace root to inspect")
            return
        if not python_files:
            add("Syntax Gate", GATE_NOT_APPLICABLE, "no Python source file in the changed set")
            return
        failures: list[str] = []
        checked: list[str] = []
        for relative in python_files:
            path = self.workspace_root / relative
            if not path.is_file():
                continue
            checked.append(relative)
            try:
                ast.parse(path.read_text(encoding="utf-8"), filename=relative)
            except (SyntaxError, ValueError, UnicodeDecodeError) as exc:
                failures.append(f"{relative}: {type(exc).__name__}")
        if not checked:
            add("Syntax Gate", GATE_NOT_APPLICABLE, "changed Python files are not present on disk")
            return
        if failures:
            add("Syntax Gate", GATE_FAIL, "changed Python files do not parse", failures)
            blocking.extend(failures)
        else:
            add("Syntax Gate", GATE_PASS, "all changed Python files parse", checked)

    def _external_checker_gate(self, add, blocking: list[str], gate: str, candidates, python_files: list[str], noun: str) -> None:
        self.last_checker_executions = getattr(self, "last_checker_executions", 0)
        if self.workspace_root is None:
            add(gate, GATE_NOT_APPLICABLE, f"verifier was given no workspace root to inspect")
            return
        if not python_files:
            add(gate, GATE_NOT_APPLICABLE, "no Python source file in the changed set")
            return
        available = self._resolve_checker(candidates)
        if available is None:
            names = ", ".join(candidate[0] for candidate in candidates)
            add(
                gate,
                GATE_UNAVAILABLE,
                f"no {noun} ({names}) is installed in the verification environment, so this check did not run",
            )
            return
        argv = list(available)
        completed = run_bounded(
            [*argv, *python_files],
            cwd=str(self.workspace_root),
            timeout_s=self.checker_timeout_s,
            stdin_devnull=True,
        )
        if completed.timed_out:
            self.last_checker_executions += 1
            add(gate, GATE_UNKNOWN, f"{noun} did not finish within {self.checker_timeout_s}s")
            return
        self.last_checker_executions += 1
        if completed.returncode == 0:
            add(gate, GATE_PASS, f"{noun} reported no finding on the changed files", python_files)
            return
        detail = (completed.stdout or completed.stderr or "").strip().splitlines()
        summary = detail[-1][:400] if detail else f"exit {completed.returncode}"
        add(gate, GATE_FAIL, f"{noun} reported findings: {summary}", python_files)
        blocking.append(f"{gate}:{summary}")

    @staticmethod
    def _resolve_checker(candidates) -> list[str] | None:
        for _name, argv, module in candidates:
            if shutil.which(argv[0]):
                return list(argv)
            if importlib.util.find_spec(module[0]) is not None:
                return [sys.executable, "-m", *argv]
        return None

    def _regression_gate(
        self,
        add,
        blocking: list[str],
        spec: TaskSpec,
        test_observations: list[Observation],
        changed_files: list[str],
    ) -> None:
        # Coverage non-regression comes first: if a task's own check lost
        # coverage, any "passing" result it produced afterwards is not about the
        # task. Checked before the command-repetition logic because it applies
        # even when a command ran only once.
        coverage_loss = self._protected_check_regressions(spec, changed_files)
        if coverage_loss:
            add("Regression Gate", GATE_FAIL, "a protected check lost coverage", coverage_loss)
            blocking.extend(coverage_loss)
            return
        if not test_observations:
            add("Regression Gate", GATE_NOT_APPLICABLE, "no test observation in this run")
            return
        groups: dict[str, list[Observation]] = {}
        for observation in test_observations:
            signature = json.dumps(RuleVerifier._command_signature(observation), sort_keys=True)
            groups.setdefault(signature, []).append(observation)
        repeated = [items for items in groups.values() if len(items) > 1]
        if not repeated:
            add("Regression Gate", GATE_NOT_APPLICABLE, "no command was executed more than once, so no baseline comparison exists")
            return
        regressions: list[str] = []
        unknowns: list[str] = []
        for items in repeated:
            latest = items[-1]
            earlier_passed = any(item.status == ObservationStatus.SUCCEEDED for item in items[:-1])
            if latest.status == ObservationStatus.OUTCOME_UNKNOWN:
                unknowns.append(latest.invocation_id)
            elif latest.status != ObservationStatus.SUCCEEDED and earlier_passed:
                regressions.append(latest.invocation_id)
        if regressions:
            add("Regression Gate", GATE_FAIL, "a previously passing check regressed in the latest run", regressions)
            blocking.extend(f"regression:{item}" for item in regressions)
            return
        if unknowns:
            add("Regression Gate", GATE_FAIL, "latest repeated check has an unknown outcome", unknowns)
            blocking.extend(f"unknown-regression:{item}" for item in unknowns)
            return
        add("Regression Gate", GATE_PASS, "no repeated check regressed", [items[-1].invocation_id for items in repeated])

    @staticmethod
    def _normalize_checks(plan) -> list[VerificationCheck]:
        return [item if isinstance(item, VerificationCheck) else VerificationCheck.model_validate(item) for item in plan]

    @staticmethod
    def _command_signature(observation: Observation) -> list[list[str]]:
        results = observation.data.get("results")
        if isinstance(results, list) and results:
            return [list(result.get("argv", [])) for result in results if isinstance(result, dict)]
        argv = observation.data.get("argv")
        return [list(argv)] if isinstance(argv, list) else []

    def _security_gate(self, add, blocking: list[str], observations: list[Observation], changed_files: list[str], spec: TaskSpec) -> None:
        commands: list[list[str]] = []
        for observation in observations:
            if observation.tool == "run_command":
                argv = observation.data.get("argv")
                if isinstance(argv, list):
                    commands.append([str(item) for item in argv])
            elif observation.tool == "run_tests":
                commands.extend(RuleVerifier._command_signature(observation))
        violations: list[str] = []
        for argv in commands:
            reason = RuleVerifier._destructive_reason(argv)
            if reason:
                violations.append(f"argv:{' '.join(argv)} ({reason})")
        for path in changed_files:
            reason = RuleVerifier._unsafe_path_reason(path, spec.allowed_paths)
            if reason:
                violations.append(f"file:{path} ({reason})")
        if not commands and not changed_files:
            add("Destructive Pattern Gate", GATE_NOT_APPLICABLE, "no command or file change to inspect")
            return
        if violations:
            add("Destructive Pattern Gate", GATE_FAIL, "destructive pattern or unauthorized path detected", violations)
            blocking.extend(violations)
        else:
            add("Destructive Pattern Gate", GATE_PASS, f"inspected {len(commands)} command(s) and {len(changed_files)} changed file(s), no destructive pattern", changed_files)

    @staticmethod
    def _destructive_reason(argv: list[str]) -> str | None:
        if not argv:
            return None
        program = Path(argv[0]).name.lower()
        program = program.removesuffix(".exe")
        flags = {item.lower() for item in argv[1:]}
        if program in _DESTRUCTIVE_COMMANDS:
            return f"destructive command '{program}'"
        if program == "git" and len(argv) > 1 and argv[1] in _FORCED_GIT_SUBCOMMANDS and (flags & _DESTRUCTIVE_FLAGS):
            return f"forced git operation '{argv[1]}'"
        if program == "chmod" and any(item.startswith("777") for item in argv[1:]):
            return "world-writable permission change"
        return None

    @staticmethod
    def _unsafe_path_reason(path: str, allowed_paths: list[str]) -> str | None:
        normalized = path.replace("\\", "/")
        if ".." in normalized.split("/"):
            return "path contains parent traversal segment"
        if normalized.startswith("/") or (len(normalized) > 1 and normalized[1] == ":"):
            return "path escapes the repository root"
        if any(part == ".git" for part in normalized.split("/")[:-1]) or normalized.startswith(".git/"):
            return "change touches git internals"
        name = normalized.rsplit("/", 1)[-1].lower()
        if name.startswith(".env") or name.endswith(_SENSITIVE_SUFFIXES) or any(token in name for token in _SENSITIVE_NAME_TOKENS):
            return "change touches credential material"
        if RuleVerifier._path_allowed(normalized, allowed_paths):
            return None
        return "change is outside allowed_paths"

    @staticmethod
    def _path_allowed(path: str, allowed_paths: list[str]) -> bool:
        if not allowed_paths or "." in allowed_paths or "**" in allowed_paths or "*" in allowed_paths:
            return True
        for pattern in allowed_paths:
            if path == pattern or fnmatch(path, pattern) or fnmatch(path, f"{pattern.rstrip('/')}/**"):
                return True
        return False


    # --- Intent assertions -------------------------------------------------
    # The evaluator never receives a task id: it judges the declared structural
    # requirement against the workspace only, so a spec cannot smuggle in
    # per-task special cases. Implemented independently of the benchmark
    # oracle's own intent checks (shared declarations, separate evaluators).

    def _intent_assertion_gate(self, add, blocking: list[str], warnings: list[str], spec: TaskSpec, changed_files: list[str]) -> set[str]:
        """Evaluate the declared structural assertions.

        Returns the criteria that *passing* assertions establish as a semantic
        anchor. Only the content-reading kinds qualify: a passing
        `required_changed` / `unchanged` proves the mutation ledger moved, which
        is exactly the claim that must not be mistaken for semantic proof. Those
        two still block the run when violated, exactly as before.
        """
        assertions = list(spec.intent_assertions)
        if not assertions:
            add("Intent Assertion Gate", GATE_NOT_APPLICABLE, "no structural intent assertion is declared for this spec")
            return set()
        changed = set(changed_files)
        failures: list[str] = []
        passed: list[str] = []
        established: set[str] = set()
        root = Path(self.workspace_root) if self.workspace_root is not None else Path(spec.repo_root)
        for assertion in assertions:
            ok, detail = self._evaluate_intent_assertion(assertion, root, changed)
            if ok:
                passed.append(assertion.assertion_id)
                if assertion.kind in SEMANTIC_ANCHOR_ASSERTION_KINDS:
                    established.update(assertion.criterion_ids)
            else:
                failures.append(assertion.assertion_id)
                blocking.append(f"intent:{assertion.assertion_id}")
                warnings.append(f"intent assertion failed: {assertion.assertion_id} ({assertion.kind}) - {detail}")
        if failures:
            add("Intent Assertion Gate", GATE_FAIL, f"structural intent violated: {', '.join(sorted(failures))}", sorted(failures))
            return established
        add("Intent Assertion Gate", GATE_PASS, "every declared structural intent assertion holds", sorted(passed))
        return established

    def _evaluate_intent_assertion(self, assertion, root: Path, changed: set[str]) -> tuple[bool, str]:
        params = dict(assertion.params or {})
        kind = assertion.kind
        if kind == "required_changed":
            required = list(params.get("paths", []))
            missing = [item for item in required if item not in changed]
            return (not missing, f"missing changed paths: {missing}" if missing else "all required paths changed")
        if kind == "unchanged":
            forbidden_changes = [item for item in params.get("paths", []) if item in changed]
            return (not forbidden_changes, f"paths must not change: {forbidden_changes}" if forbidden_changes else "paths unchanged")
        tree = _intent_load_tree(root, str(params.get("path", "")))
        if tree is None:
            return (False, f"could not parse {params.get('path')!r}")
        if kind == "required_import":
            found = _intent_has_import(tree, str(params.get("module", "")), str(params.get("name", "")))
            return (found, "import present" if found else f"missing 'from {params.get('module')} import {params.get('name')}'")
        if kind == "required_module_constant":
            value = _intent_module_constant(tree, str(params.get("name", "")))
            expected = params.get("value")
            return (value == expected, f"{params.get('name')} == {value!r}, expected {expected!r}")
        if kind == "forbidden_module_constant":
            value = _intent_any_constant(tree, str(params.get("name", "")))
            expected = params.get("value")
            return (value != expected, f"{params.get('name')} is assigned {expected!r} (inlined upstream value)" if value == expected else "no inlined upstream constant")
        if kind == "required_call_result":
            expected_args = list(params.get("args", []))
            found = _intent_returns_called_with_delta(tree, str(params.get("function", "")), str(params.get("called_function", "")), int(params.get("delta", 0)), expected_args)
            return (found, "call chain preserved" if found else f"{params.get('function')} must return {params.get('called_function')}() (+{params.get('delta', 0)})")
        if kind == "required_return_name":
            found = _intent_returns_name(tree, str(params.get("function", "")), str(params.get("name", "")))
            return (found, "returns the required name" if found else f"{params.get('function')} must return {params.get('name')}")
        if kind == "required_symbol":
            found = _intent_has_symbol(tree, str(params.get("symbol", "")), str(params.get("symbol_kind", "any")))
            return (found, "symbol present" if found else f"missing symbol {params.get('symbol')}")
        if kind == "forbidden_text":
            try:
                text = (root / str(params.get("path", ""))).read_text(encoding="utf-8")
            except OSError as exc:
                return (False, f"could not read {params.get('path')!r}: {exc}")
            pattern = str(params.get("pattern", ""))
            return (pattern not in text, f"forbidden text present: {pattern!r}" if pattern in text else "no forbidden text")
        return (False, f"unsupported assertion kind: {kind}")


    def _protected_check_regressions(self, spec: TaskSpec, changed_files: list[str]) -> list[str]:
        """Which protected checks lost test surface relative to their baseline."""
        if self.workspace_root is None:
            return []
        changed = set(changed_files)
        failures: list[str] = []
        for check in spec.protected_checks:
            if check.path not in changed:
                continue
            surface = _check_surface(self.workspace_root / check.path)
            if surface is None:
                failures.append(f"{check.path}:removed-or-unparseable")
                continue
            if surface["methods"] < check.min_methods or surface["assertions"] < check.min_assertions:
                failures.append(
                    f"{check.path}:coverage-regressed "
                    f"(methods {surface['methods']} < {check.min_methods} or "
                    f"assertions {surface['assertions']} < {check.min_assertions})"
                )
        return failures

    def _discriminating_transitions(self, spec: TaskSpec, observations: list[Observation]) -> set[str]:
        """Criteria a declared check demonstrably distinguishes.

        Deterministic behavioural evidence: a command equivalent to one of a
        criterion's declared verification checks FAILED, the artifact was then
        mutated, and the same check PASSED afterwards. That is the smallest
        sequence that shows the check is sensitive to the change; a post-edit
        PASS with no prior discriminating failure shows only that the check is
        green, which is what a comment-only patch also produces.

        The mutation must also be *relevant* to the criterion. When the criterion
        declares `evidence_paths`, a patch inside the failure->pass window has to
        touch one of them; a patch to some other file explains nothing about this
        criterion. With no declared path contract there is nothing narrower to
        enforce, so any successful patch in the window counts -- a deliberate
        limitation, recorded rather than papered over.
        """
        events = _command_events(observations)
        if not events:
            return set()
        patches = [
            (index, observation)
            for index, observation in enumerate(observations)
            if observation.tool == "apply_patch" and observation.status == ObservationStatus.SUCCEEDED
        ]
        if not patches:
            return set()
        proven: set[str] = set()
        for check in self._normalize_checks(spec.verification_plan):
            if not check.applicable:
                continue
            expected = normalize_argv(check.argv)
            if not expected:
                continue
            matched = [event for event in events if event[1] == expected]
            failures = [event[0] for event in matched if event[2]]
            successes = [event[0] for event in matched if event[3]]
            if not failures or not successes:
                continue
            for criterion in spec.success_conditions:
                if criterion.criterion_id not in check.criterion_ids:
                    continue
                if RuleVerifier._check_distinguishes(criterion, failures, successes, patches):
                    proven.add(criterion.criterion_id)
        return proven

    @staticmethod
    def _check_distinguishes(criterion, failures: list[int], successes: list[int], patches) -> bool:
        """Is there a failure->relevant-mutation->pass window for this criterion?"""
        for failure in failures:
            for success in successes:
                if success <= failure:
                    continue
                if any(
                    failure < index < success and _patch_touches(observation, criterion.evidence_paths)
                    for index, observation in patches
                ):
                    return True
        return False

    def _final_goal_gate(
        self,
        add,
        blocking: list[str],
        warnings: list[str],
        spec: TaskSpec,
        criterion_evidence: dict[str, list[EvidenceRecord]],
        observations: list[Observation],
        structurally_proven: set[str],
    ) -> None:
        drift = self._evidence_drift(criterion_evidence, warnings)
        applicable_checks = [check for check in RuleVerifier._normalize_checks(spec.verification_plan) if check.applicable]
        mutated = _actual_artifact_mutation(observations)
        if spec.expects_artifact_change and not mutated:
            # Mechanized no-op guard (B20-class): existing tests can pass while
            # the requested behavior change was never made.
            add("Final Goal Gate", GATE_FAIL, "task expects an artifact change but no successful patch was made", ["no-op:expects_artifact_change"])
            blocking.append("no-op:expects_artifact_change")
            return
        if spec.expects_artifact_change or mutated:
            # Semantic completion contract. Green checks are not semantic proof
            # when nothing shows they distinguish the requested behaviour: an
            # irrelevant edit to an allowed path, followed by checks that were
            # already green, is indistinguishable from a correct implementation
            # by coverage alone. A criterion therefore needs one of the two
            # deterministic anchors -- a passing content-reading intent
            # assertion, or a declared check that failed before a relevant
            # mutation and passed after.
            #
            # The rule is keyed on the FACT of mutation as well as the
            # declaration. `expects_artifact_change` is a claim a spec makes
            # about itself; an omitted flag (`None`, the legacy value) is not
            # evidence that the run stayed read-only, so it cannot be a licence
            # to mutate without proof. A read-only run mutates nothing and is
            # untouched by this branch.
            anchored = set(structurally_proven) | self._discriminating_transitions(spec, observations)
            unanchored = sorted({criterion.criterion_id for criterion in spec.success_conditions} - anchored)
            if unanchored:
                add(
                    "Final Goal Gate",
                    GATE_FAIL,
                    "no deterministic semantic proof that the requested criterion was established",
                    [f"semantic-proof:{item}" for item in unanchored],
                )
                blocking.extend(f"semantic-proof:{item}" for item in unanchored)
                return
        if not applicable_checks:
            if drift:
                add("Final Goal Gate", GATE_FAIL, "artifact changed after the evidence was captured", drift)
                blocking.extend(drift)
            else:
                add(
                    "Final Goal Gate",
                    GATE_NOT_APPLICABLE,
                    "no applicable verification check is declared; final goal rests on bound evidence only",
                )
            return
        # Criterion coverage comes from the SAME declared-check resolution the
        # Tests Gate and the auto-binder consume: only an exact matched declared
        # check whose observation SUCCEEDED covers its criteria. The previous
        # positional zip granted coverage on status alone, which is how an
        # unrelated trailing run_tests could stand in for a declared check.
        covered: set[str] = set()
        for execution in self._declared_check_executions(spec, observations):
            if execution.satisfied:
                covered.update(execution.check.criterion_ids)
        uncovered = sorted({criterion.criterion_id for criterion in spec.success_conditions} - covered)
        if uncovered:
            add("Final Goal Gate", GATE_FAIL, "criteria are not covered by a passing check", uncovered)
            blocking.extend(f"uncovered:{item}" for item in uncovered)
            return
        if drift:
            add("Final Goal Gate", GATE_FAIL, "artifact changed after the evidence was captured", drift)
            blocking.extend(drift)
            return
        add("Final Goal Gate", GATE_PASS, "every criterion is covered by a passing check and the artifact still matches", sorted(covered))

    def _evidence_drift(self, criterion_evidence: dict[str, list[EvidenceRecord]], warnings: list[str]) -> list[str]:
        if self.workspace_root is None:
            return []
        drift: list[str] = []
        for criterion_id, records in criterion_evidence.items():
            for record in records:
                relative, expected_hash = RuleVerifier._file_locator(record)
                if relative is None or expected_hash is None:
                    continue
                path = self.workspace_root / relative
                if not path.is_file():
                    warnings.append(f"{criterion_id}: evidence file {relative} is no longer present")
                    continue
                actual = hashlib.sha256(path.read_bytes()).hexdigest()
                if actual != expected_hash:
                    drift.append(f"{criterion_id}:{relative}")
        return sorted(set(drift))

    @staticmethod
    def _file_locator(record: EvidenceRecord) -> tuple[str | None, str | None]:
        locator = record.locator
        if not locator.startswith("file:"):
            return None, None
        body = locator[5:]
        relative, _, remainder = body.partition("#")
        if not relative:
            return None, None
        expected = record.content_hash
        if expected is None and remainder:
            expected = remainder.split(":", 1)[0] or None
        return relative, expected
