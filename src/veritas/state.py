from __future__ import annotations

from enum import StrEnum

from pydantic import Field, StrictStr

from .contracts import StrictModel, VerificationConclusion


class Phase(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    WAITING_FOR_INPUT = "WAITING_FOR_INPUT"
    VERIFYING = "VERIFYING"
    DONE = "DONE"
    BLOCKED = "BLOCKED"
    STOPPED = "STOPPED"
    # long-task pause: the run stopped at a SAFE boundary and its state was
    # persisted; a fresh process may resume it. PAUSED is terminal for THIS run
    # record -- the resumed run is a new run seeded from the persisted state.
    PAUSED = "PAUSED"


class StateTransitionError(ValueError):
    pass


class RunState(StrictModel):
    run_id: StrictStr
    task_id: StrictStr
    phase: Phase = Phase.CREATED
    state_version: int = Field(default=1, ge=1)
    current_goal: StrictStr | None = None
    observations: list[str] = Field(default_factory=list)
    files_touched: list[str] = Field(default_factory=list)
    pre_existing_files: list[str] = Field(default_factory=list)
    veritas_files: list[str] = Field(default_factory=list)
    overlap_conflicts: list[str] = Field(default_factory=list)
    commands_executed: list[str] = Field(default_factory=list)
    test_results: list[str] = Field(default_factory=list)
    known_risks: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    verification_status: VerificationConclusion | None = None
    tool_calls: int = Field(default=0, ge=0)
    model_requests: int = Field(default=0, ge=0)

    def start(self) -> None:
        if self.phase != Phase.CREATED:
            raise StateTransitionError(f"cannot start from {self.phase}")
        self.phase = Phase.RUNNING
        self.state_version += 1

    def wait_for_input(self) -> None:
        if self.phase not in (Phase.RUNNING, Phase.VERIFYING):
            raise StateTransitionError(f"cannot wait from {self.phase}")
        self.phase = Phase.WAITING_FOR_INPUT
        self.state_version += 1

    def begin_verification(self) -> None:
        if self.phase != Phase.RUNNING:
            raise StateTransitionError(f"cannot verify from {self.phase}")
        self.phase = Phase.VERIFYING
        self.state_version += 1

    def finish(self) -> None:
        if self.phase != Phase.VERIFYING:
            raise StateTransitionError(f"cannot finish from {self.phase}")
        if self.verification_status != VerificationConclusion.PASS:
            raise StateTransitionError("only PASS can transition to DONE")
        self.phase = Phase.DONE
        self.state_version += 1

    def repair(self) -> None:
        if self.phase != Phase.VERIFYING:
            raise StateTransitionError(f"cannot repair from {self.phase}")
        if self.verification_status != VerificationConclusion.REPAIR_REQUIRED:
            raise StateTransitionError("repair requires REPAIR_REQUIRED")
        self.phase = Phase.RUNNING
        self.state_version += 1

    def block(self) -> None:
        if self.phase in (Phase.DONE, Phase.BLOCKED, Phase.STOPPED, Phase.PAUSED):
            raise StateTransitionError(f"cannot block from {self.phase}")
        self.phase = Phase.BLOCKED
        self.state_version += 1

    def pause(self) -> None:
        """Long-task pause at a safe boundary. Terminal for this run record."""
        if self.phase not in (Phase.RUNNING, Phase.VERIFYING):
            raise StateTransitionError(f"cannot pause from {self.phase}")
        self.phase = Phase.PAUSED
        self.state_version += 1
