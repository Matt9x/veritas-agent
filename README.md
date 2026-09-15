# Veritas

Verification-First Agent Harness

## At a Glance

- **Problem:** an executor can claim success without completing the task.
- **Idea:** separate task execution, observed evidence and the completion decision.
- **Artifact:** a small headless research subset of the original Veritas verifier
  and state model, with a scripted offline executor.
- **Evidence:** 18 focused tests pass; five synthetic demo scenarios show one
  verified completion and four refusals to mark complete.

## Design Philosophy

**Completion must be demonstrated by evidence, not declared by the agent itself.**

| Principle | Observable behavior in this snapshot |
| --- | --- |
| **Restraint** | Seven installed Python modules; no agent loop, Memory/RAG, GUI or provider integration. A test checks the module boundary. |
| **Minimal Change** | The positive example changes one declared artifact and preserves an unrelated fixture; a reported out-of-scope change is rejected. This is not a general minimal-diff optimizer. |
| **Evidence Before Confidence** | A correct artifact with missing criterion evidence still cannot complete. Stale, foreign-run and missing-observation references are rejected. |
| **Independent Verification** | The deterministic verifier reads artifact content and evaluates declared assertions separately from the scripted executor's success claim. Independence is logical, not a process/security boundary. |
| **Provable Completion** | The state model permits `DONE` only from `VERIFYING` with a `PASS` verdict. Proof here means evidence satisfying declared checks, not mathematical proof of arbitrary correctness. |

The full Veritas design also considers verification coverage, unrelated diff,
architecture compliance and other quality measures. This snapshot tests only
mechanisms directly related to completion reliability; it does not implement or
report the complete eight-metric quality benchmark.

## Overview

An agent saying “done” is not completion evidence. It may have changed the wrong
value, completed only one requirement, or supplied no traceable observation.
Veritas makes these distinctions inspectable through typed task/state contracts,
criterion-bound evidence and a deterministic verification report.

This artifact extracts the existing `RuleVerifier` and `RunState` rather than
inventing a smaller replacement verifier. A new synthetic demo wires them to
simple file operations. It is not the full Veritas product or an autonomous
developer replacement, and it does not connect to an LLM.

## Research Question

**How can an autonomous agent determine that a task is actually complete,
rather than merely claiming completion?**

- **RQ1:** How should task state make execution and completion explicit?
- **RQ2:** How can independent verification reject false-completion claims?
- **RQ3:** How should evidence link actions, verification results and final status?
- **RQ4:** What happens when execution is partial or verification does not support completion?

The examples demonstrate specific mechanisms for these questions, not general
empirical answers across autonomous agents.

## Why This Matters

False completion hides unfinished work. Partial execution may produce convincing
output while leaving a requirement unsatisfied. Explicit state and evidence make
that distinction reviewable, providing a small basis for studying long-horizon
reliability without claiming that long-horizon execution has been evaluated here.

## Design Principles

The implementation keeps the task's success criteria explicit, binds evidence to
criteria and observations, verifies content, and accepts only `PASS` for `DONE`.
Missing required criterion evidence fails closed. Executor prose is not supplied
to `RuleVerifier` as proof.

These guarantees assume a trusted Python caller and honest observation capture.
They do not prevent a malicious caller from forging state or evidence objects.

## Architecture

```text
TaskSpec: criteria, allowed paths, structural assertions
  -> RunState: CREATED -> RUNNING
  -> scripted executor: attempted file mutation + success claim
  -> observer: actual file digest + observation / evidence IDs
  -> RunState: VERIFYING
  -> original RuleVerifier: evidence bindings + artifact assertions + gate report
  -> PASS -> RunState.finish() -> DONE
     other verdict -> demo wiring blocks this attempt -> BLOCKED
```

No database or trace service is required. The returned report retains evidence
links in memory, and the demo prints a public-safe summary to the terminal.

## Task Lifecycle

The demo exercises `CREATED`, `RUNNING`, `VERIFYING`, `DONE` and `BLOCKED`.
`finish()` rejects both an incorrect phase and every non-`PASS` verdict, including
`PASS_WITH_WARNINGS`. The retained source enum also includes waiting, stopped and
paused states; this snapshot does not implement their orchestration or resume
persistence. There is no automatic repair loop in the demo.

## Verification Model

`RuleVerifier` is the original deterministic module. It has no model call. It
checks criterion/evidence linkage and can inspect artifact syntax, structural
intent, changed-path reports, declared command observations and evidence digests.
The synthetic task requires exact integer constants in a small text artifact;
the verifier parses and reads those constants itself.

It is **logically independent**, but shares a process and trusted inputs with the
harness. It is not a sandbox, an independent organization, a formal proof system,
or a defence against arbitrary malicious Python code.

Gate statuses distinguish `PASS`, `FAIL`, `UNKNOWN`, `NOT_APPLICABLE` and
`UNAVAILABLE`. The original policy allows missing optional lint/type-check tools
to be `UNAVAILABLE` without blocking the overall verdict. A `PASS` therefore does
not mean every possible check ran. The demo changes text artifacts, so Python
lint/typecheck gates are not applicable and no external checker is launched.
When using the retained verifier on changed Python files, installed checkers may
be discovered and invoked; that broader behavior is not part of the demo claim.

## Evidence Model

Evidence records carry an evidence ID, run ID, criterion ID, observation invocation
ID, artifact reference, freshness flag and optional content digest. The demo
observer hashes the actual artifact after execution; the verifier checks the
record's linkage and rereads artifact content. A changed digest after capture can
block completion.

These records are caller-owned data, not signed attestations. File inventories,
freshness and mutation observations must be captured correctly by a real
integration. This snapshot does not provide durable trace storage or an audit
service. All examples and tests use synthetic/generated data.

## Offline Example

Every scenario's executor claims `success`. Only one receives verified completion:

| Scenario | What actually happened | Verdict | Phase |
| --- | --- | --- | --- |
| `pass` | Requested constant changed; linked evidence and content assertion agree | `PASS` | `DONE` |
| `false_completion` | No requested mutation, despite the success claim | `REPAIR_REQUIRED` | `BLOCKED` |
| `wrong_artifact` | File write succeeded but the value is wrong | `REPAIR_REQUIRED` | `BLOCKED` |
| `missing_evidence` | Value is correct but criterion evidence is absent | `REPAIR_REQUIRED` | `BLOCKED` |
| `partial_execution` | One of two required constants remains wrong | `REPAIR_REQUIRED` | `BLOCKED` |

The demo creates only synthetic files in an operating-system temporary directory,
asserts expected outcomes and preservation of an unrelated fixture, and removes
its temporary directory afterwards. Results are repeatable; no local path or
provider response is printed. It uses scripted execution, not the production
ToolRuntime or an LLM. Permission-system behavior is not claimed.

## Quick Start

Requires **Python 3.12+**. Verified on **Python 3.14.0 / Windows**; other supported
Python versions and operating systems have not been exercised in this snapshot.

From the repository root, create and activate a fresh environment:

```sh
python -m venv .venv
```

Windows PowerShell: `.venv\Scripts\Activate.ps1`

macOS/Linux: `source .venv/bin/activate`

Then:

```sh
python -m pip install -r requirements.txt
python -m pip install --no-build-isolation --no-deps .
python -I -B -m veritas.demo
python -I -B -m unittest discover -s tests -v
```

Installation needs the pinned Python packages from a package index or a prepared
wheelhouse. After installation, demo and tests need no network, API key, GitHub
account, Git executable, GUI, Docker or database service. No source-repository
`PYTHONPATH`, editable install or private sibling repository is needed.

## Tests

On 2026-09-15, a fresh environment installed this snapshot from a separate copy:
**18 passed, 0 failed, 0 skipped**. These are 2 retained state tests and 16 focused
snapshot tests, including the deterministic demo regression.

Tests cover phase/verification guards, positive completion, false claims, wrong
and partial execution, missing/stale/foreign evidence, observation linkage,
post-capture drift, out-of-scope changes, missing semantic anchors, all non-PASS
verdicts, repeatable demo output and the installed module boundary.

The original project's desktop tests, broad suites and manifest/schema checks are
not results for this artifact. No agent task success rate or SOTA result is claimed.
Python syntax/import checks also pass. Lint/typecheck tools are not dependencies
of this minimal snapshot.

## Current Limitations

- Research subset, not the complete Veritas product or a production coding agent.
- Scripted executor; no model-driven agent loop, provider evaluation or broad benchmark.
- Verification is only as strong as the declared criteria and trusted observations.
- Logical verifier independence does not protect against forged state/evidence,
  arbitrary Python execution, race conditions or incomplete observation capture.
- Optional unavailable checkers are disclosed, not automatically blocking.
- No complete proof of tool confinement, destructive-action prevention, arbitrary
  semantic correctness, protected-file integrity or all original verifier branches.
- No production ToolRuntime, permission-system demo, durable trace store, repair
  controller or long-horizon recovery validation.
- No MemoryGraph, RetrievalPolicy, memory writer, RAG or GUI.
- No robot experiments, VLM/VLA, simulator results or embodied evaluation.
- The complete eight-metric quality benchmark is outside this snapshot.

## Future Research

Verifier robustness, uncertainty-aware verification, adversarial executor/verifier
disagreement, stronger evidence authenticity, multimodal evidence, embodied task
verification and long-horizon recovery remain future research directions.

## Toward Embodied Agents

In embodied settings, an agent may act in the physical world, where an incorrect
completion claim can have consequences beyond software state. A future direction
is to study how explicit task state, independent verification and evidence
tracking can be adapted to those settings.

How should an embodied agent verify that a physical task has actually been
completed rather than relying on its action history? This snapshot does not
implement or evaluate an embodied agent.

## Provenance

Source project: **Veritas**. Source branch: `veritas-product`. Source commit:
`cbed7158c8ecd42749026076213b29817a6c54c1`. Snapshot date: **2026-09-15**.

See [PROVENANCE.md](PROVENANCE.md) for extraction changes, source hashes, dependency
mapping, ownership and data classification. Original Git history and operational
artifacts are not included.

## License

[MIT](LICENSE), as authorized by the project owner. Third-party dependencies keep
their own licenses; see [NOTICE.md](NOTICE.md). No dependency source or wheels are
vendored in this repository.
