import unittest

from veritas.contracts import VerificationConclusion
from veritas.state import Phase, RunState, StateTransitionError


class StateTests(unittest.TestCase):
    def test_done_requires_pass_verification(self):
        state = RunState(run_id="run-1", task_id="task-1")
        state.phase = Phase.VERIFYING
        state.verification_status = VerificationConclusion.REPAIR_REQUIRED
        with self.assertRaises(StateTransitionError):
            state.finish()

    def test_pass_verification_transitions_to_done(self):
        state = RunState(run_id="run-2", task_id="task-2")
        state.phase = Phase.VERIFYING
        state.verification_status = VerificationConclusion.PASS
        state.finish()
        self.assertEqual(state.phase, Phase.DONE)

    def test_paused_run_is_terminal_for_this_record(self):
        state = RunState(run_id="run-3", task_id="task-3")
        state.start()
        state.pause()

        for transition in (state.block, state.finish, state.start, state.begin_verification):
            with self.subTest(transition=transition.__name__):
                with self.assertRaises(StateTransitionError):
                    transition()
                self.assertEqual(state.phase, Phase.PAUSED)


if __name__ == "__main__":
    unittest.main()
