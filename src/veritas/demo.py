"""Offline synthetic wiring around the original state and verifier mechanisms.

The executor is scripted example code, not AgentRunner or an LLM provider.
Only the two known files inside a newly created temporary directory are used.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

from .contracts import (
    Criterion, EvidenceRecord, IntentAssertion, Observation, ObservationStatus,
    SideEffectOutcome, TaskSpec, VerificationConclusion,
)
from .state import Phase, RunState
from .verifier import RuleVerifier

SCENARIOS = ("pass", "false_completion", "wrong_artifact", "missing_evidence", "partial_execution")


def completion_attempt(spec, state, observations, evidence, executor_claim):
    """Example wiring: an executor's prose has no completion authority.

    Mirrors the product's report -> verification_status -> PASS-only finish
    sequence. No model loop, repair orchestration or persistence is included.
    The Python caller remains trusted; this is not a security boundary.
    """
    state.begin_verification()
    report = RuleVerifier(workspace_root=Path(spec.repo_root)).verify(
        spec, state, observations, evidence,
    )
    state.verification_status = report.conclusion
    if report.conclusion == VerificationConclusion.PASS:
        state.finish()
    else:
        state.block()
    return report


def run_scenario(mode="pass"):
    if mode not in SCENARIOS:
        raise ValueError("unknown synthetic scenario")
    with tempfile.TemporaryDirectory(prefix="veritas-demo-") as directory:
        root = Path(directory)
        artifact = root / "answer.txt"
        unrelated = root / "untouched.txt"
        artifact.write_text("ANSWER = 0\nSECOND = 0\n", encoding="utf-8")
        unrelated.write_text("synthetic protected fixture\n", encoding="utf-8")
        protected_before = unrelated.read_bytes()
        criteria = [Criterion(
            criterion_id="C1", description="ANSWER is 42", evidence_method="read_file",
            evidence_paths=["answer.txt"],
        )]
        assertions = [IntentAssertion(
            assertion_id="answer-value", criterion_ids=["C1"],
            kind="required_module_constant", params={"path": "answer.txt", "name": "ANSWER", "value": 42},
        )]
        if mode == "partial_execution":
            criteria.append(Criterion(criterion_id="C2", description="SECOND is 7", evidence_method="read_file", evidence_paths=["answer.txt"]))
            assertions.append(IntentAssertion(
                assertion_id="second-value", criterion_ids=["C2"],
                kind="required_module_constant", params={"path": "answer.txt", "name": "SECOND", "value": 7},
            ))
        spec = TaskSpec(
            task_id="synthetic-task", repo_root=str(root), request="Set the declared constants.",
            success_conditions=criteria, intent_assertions=assertions,
            allowed_paths=["answer.txt"], must_not_modify=["untouched.txt"],
            expects_artifact_change=True,
        )
        state = RunState(run_id="synthetic-run", task_id=spec.task_id)
        state.start()
        observations = []
        if mode != "false_completion":
            value = 41 if mode == "wrong_artifact" else 42
            artifact.write_text(f"ANSWER = {value}\nSECOND = 0\n", encoding="utf-8")
            state.files_touched = ["answer.txt"]
            state.veritas_files = ["answer.txt"]
            observations.append(Observation(
                invocation_id="write-1", tool="apply_patch", status=ObservationStatus.SUCCEEDED,
                data={"changes": [{"path": "answer.txt"}]}, affected_files=["answer.txt"],
                side_effect_outcome=SideEffectOutcome.APPLIED,
            ))
        # Example observer captures actual bytes after execution; it does not
        # claim that those bytes satisfy the task. RuleVerifier reads them again.
        content_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
        observations.append(Observation(
            invocation_id="read-1", tool="read_file", status=ObservationStatus.SUCCEEDED,
            data={"path": "answer.txt", "content_hash": content_hash}, evidence_refs=["file:answer.txt"],
        ))
        state.observations = [ob.invocation_id for ob in observations]
        evidence = {} if mode == "missing_evidence" else {
            criterion.criterion_id: [EvidenceRecord(
                evidence_id=f"evidence-{criterion.criterion_id}", run_id=state.run_id,
                source="read_file", locator="file:answer.txt", artifact_ref="workspace",
                criterion_id=criterion.criterion_id, invocation_id="read-1", content_hash=content_hash,
            )] for criterion in criteria
        }
        report = completion_attempt(spec, state, observations, evidence, executor_claim="success")
        expected = Phase.DONE if mode == "pass" else Phase.BLOCKED
        if state.phase != expected or unrelated.read_bytes() != protected_before:
            raise AssertionError("synthetic completion or minimal-change contract violated")
        return {
            "scenario": mode, "executor_claim": "success", "phase": state.phase.value,
            "verifier": report.conclusion.value, "changed_files": state.files_touched,
            "unrelated_file_unchanged": True,
            "failed_gates": [gate.gate for gate in report.gates if gate.status == "FAIL"],
            "criterion_evidence": report.criterion_evidence,
            "observation_ids": [ob.invocation_id for ob in observations],
            "evidence_invocations": [record.invocation_id for record in report.evidence_records],
        }


def main():
    for mode in SCENARIOS:
        print(json.dumps(run_scenario(mode), sort_keys=True))


if __name__ == "__main__":
    main()
