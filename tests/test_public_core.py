"""Focused synthetic regressions for the extracted completion boundary."""

import ast
import hashlib
import tempfile
import unittest
from pathlib import Path

import veritas
from veritas.contracts import (
    Criterion, EvidenceRecord, IntentAssertion, Observation, ObservationStatus,
    TaskSpec, VerificationCheck, VerificationConclusion,
)
from veritas.demo import SCENARIOS, completion_attempt, run_scenario
from veritas.state import Phase, RunState, StateTransitionError
from veritas.verification_debt import normalize_argv
from veritas.verifier import RuleVerifier


class PublicCompletionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="veritas-test-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / "answer.txt"
        self.path.write_text("ANSWER = 42\n", encoding="utf-8")
        self.spec = TaskSpec(
            task_id="synthetic-task", repo_root=str(self.root), request="Set ANSWER to 42.",
            success_conditions=[Criterion(criterion_id="C1", description="ANSWER is 42", evidence_method="read_file", evidence_paths=["answer.txt"])],
            allowed_paths=["answer.txt"], expects_artifact_change=True,
            intent_assertions=[IntentAssertion(assertion_id="answer", criterion_ids=["C1"], kind="required_module_constant", params={"path": "answer.txt", "name": "ANSWER", "value": 42})],
        )
        self.state = RunState(run_id="run-test", task_id=self.spec.task_id, files_touched=["answer.txt"], veritas_files=["answer.txt"])
        self.state.start()
        self.observations = [
            Observation(invocation_id="write-1", tool="apply_patch", status=ObservationStatus.SUCCEEDED, affected_files=["answer.txt"], data={"changes": [{"path": "answer.txt"}]}),
            Observation(invocation_id="read-1", tool="read_file", status=ObservationStatus.SUCCEEDED, data={"path": "answer.txt"}),
        ]
        self.record = EvidenceRecord(
            evidence_id="e1", run_id=self.state.run_id, source="read_file", locator="file:answer.txt",
            artifact_ref="workspace", criterion_id="C1", invocation_id="read-1",
            content_hash=hashlib.sha256(self.path.read_bytes()).hexdigest(),
        )
        self.evidence = {"C1": [self.record]}

    def verify(self):
        return completion_attempt(self.spec, self.state, self.observations, self.evidence, executor_claim="success")

    def test_executor_claim_cannot_finish_running_state(self):
        with self.assertRaises(StateTransitionError):
            self.state.finish()
        self.assertEqual(self.state.phase, Phase.RUNNING)

    def test_positive_completion_uses_verifier(self):
        report = self.verify()
        self.assertEqual(report.conclusion, VerificationConclusion.PASS)
        self.assertEqual(self.state.phase, Phase.DONE)

    def test_success_claim_without_mutation_is_not_completion(self):
        result = run_scenario("false_completion")
        self.assertEqual(result["executor_claim"], "success")
        self.assertEqual(result["changed_files"], [])
        self.assertEqual(result["phase"], "BLOCKED")
        self.assertIn("Final Goal Gate", result["failed_gates"])

    def test_successful_wrong_mutation_is_rejected(self):
        result = run_scenario("wrong_artifact")
        self.assertEqual(result["changed_files"], ["answer.txt"])
        self.assertEqual(result["phase"], "BLOCKED")
        self.assertIn("Intent Assertion Gate", result["failed_gates"])

    def test_missing_evidence_fails_closed(self):
        self.evidence = {}
        report = self.verify()
        self.assertEqual(self.state.phase, Phase.BLOCKED)
        self.assertTrue(any(g.gate == "Intent Gate" and g.status == "FAIL" for g in report.gates))

    def test_partial_execution_does_not_complete_all_criteria(self):
        result = run_scenario("partial_execution")
        self.assertEqual(set(result["criterion_evidence"]), {"C1", "C2"})
        self.assertEqual(result["phase"], "BLOCKED")
        self.assertIn("Intent Assertion Gate", result["failed_gates"])

    def test_evidence_links_criterion_to_run_and_observation(self):
        report = self.verify()
        self.assertEqual(report.criterion_evidence, {"C1": ["e1"]})
        self.assertEqual(report.evidence_records[0].invocation_id, "read-1")
        self.assertEqual(report.evidence_records[0].run_id, self.state.run_id)
        self.assertEqual(report.evidence_records[0].content_hash, hashlib.sha256(self.path.read_bytes()).hexdigest())

    def test_foreign_run_evidence_is_rejected(self):
        self.record.run_id = "another-run"
        self.assertNotEqual(self.verify().conclusion, VerificationConclusion.PASS)
        self.assertEqual(self.state.phase, Phase.BLOCKED)

    def test_missing_observation_is_rejected(self):
        self.record.invocation_id = "never-observed"
        self.assertNotEqual(self.verify().conclusion, VerificationConclusion.PASS)

    def test_stale_evidence_is_rejected(self):
        self.record.fresh = False
        self.assertNotEqual(self.verify().conclusion, VerificationConclusion.PASS)

    def test_artifact_drift_after_capture_is_rejected(self):
        self.path.write_text("ANSWER = 42\n# changed after evidence\n", encoding="utf-8")
        report = self.verify()
        self.assertNotEqual(report.conclusion, VerificationConclusion.PASS)
        self.assertIn("C1:answer.txt", report.blocking_items)

    def test_undeclared_change_is_not_verified(self):
        self.state.files_touched.append("unrelated.txt")
        self.state.veritas_files.append("unrelated.txt")
        self.assertNotEqual(self.verify().conclusion, VerificationConclusion.PASS)
        self.assertEqual(self.state.phase, Phase.BLOCKED)

    def test_no_semantic_anchor_cannot_pass_on_observations_alone(self):
        self.spec.intent_assertions = []
        report = self.verify()
        self.assertIn("semantic-proof:C1", report.blocking_items)
        self.assertEqual(self.state.phase, Phase.BLOCKED)

    def test_every_nonpass_verdict_blocks_finish(self):
        for verdict in VerificationConclusion:
            if verdict == VerificationConclusion.PASS:
                continue
            with self.subTest(verdict=verdict):
                state = RunState(run_id="r", task_id="t")
                state.start()
                state.begin_verification()
                state.verification_status = verdict
                with self.assertRaises(StateTransitionError):
                    state.finish()
                self.assertNotEqual(state.phase, Phase.DONE)

    def test_demo_is_deterministic_and_preserves_unrelated_fixture(self):
        first = [run_scenario(mode) for mode in SCENARIOS]
        self.assertEqual(first, [run_scenario(mode) for mode in SCENARIOS])
        self.assertEqual([row["phase"] for row in first], ["DONE", "BLOCKED", "BLOCKED", "BLOCKED", "BLOCKED"])
        self.assertTrue(all(row["unrelated_file_unchanged"] for row in first))

    def test_installed_public_scope_has_no_unrelated_modules(self):
        package = Path(veritas.__file__).parent
        expected = {"__init__.py", "contracts.py", "demo.py", "state.py", "subprocess_util.py", "verification_debt.py", "verifier.py"}
        self.assertEqual({p.name for p in package.glob("*.py")}, expected)
        forbidden = {"provider", "memory", "memory_graph_adapter", "retrieval_policy_adapter", "desktop", "tui", "runner", "store"}
        for path in package.glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom):
                    self.assertFalse(set((node.module or "").split(".")) & forbidden)


class VerifierBoundaryTests(unittest.TestCase):
    def test_normalize_argv_strips_supported_python_interpreter_names(self):
        command = ("-m", "pytest", "-q")
        for executable in ("python", "python.exe", "python3", "python3.14", r"D:\Python\python3.14.exe"):
            with self.subTest(executable=executable):
                self.assertEqual(normalize_argv([executable, *command]), command)

    def test_normalize_argv_keeps_substring_python_names(self):
        command = ("-m", "pytest", "-q")
        for executable in ("evilpython.exe", "notpython", "python-wrapper.exe"):
            with self.subTest(executable=executable):
                self.assertEqual(normalize_argv([executable, *command]), (executable, *command))

    def test_command_identity_does_not_accept_fake_python_interpreter(self):
        check = VerificationCheck(check_id="tests", criterion_ids=["C1"], argv=["python", "-m", "pytest"])
        observation = Observation(
            invocation_id="run-1",
            tool="run_tests",
            status=ObservationStatus.SUCCEEDED,
            data={"argv": ["evilpython.exe", "-m", "pytest"]},
        )
        self.assertFalse(RuleVerifier._observation_ran_check(check, observation))

    def test_parent_traversal_segments_are_rejected_even_when_pattern_allows(self):
        for path in ("../x", "allowed/../../outside.txt", "safe/../outside.txt", r"safe\..\outside.txt"):
            with self.subTest(path=path):
                self.assertIsNotNone(RuleVerifier._unsafe_path_reason(path, ["*"]))

    def test_valid_allowed_path_remains_allowed(self):
        self.assertIsNone(RuleVerifier._unsafe_path_reason("allowed/file.txt", ["allowed/**"]))


if __name__ == "__main__":
    unittest.main()
