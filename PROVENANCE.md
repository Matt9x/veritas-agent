# Provenance and public boundary

- Derived from the author's private Veritas project.
- Snapshot version: V1.1 public snapshot.
- Snapshot date: 2026-09-15.
- Included scope: the selected deterministic verifier, explicit run state,
  evidence contracts, bounded subprocess helper, syntactic command normalization,
  synthetic demo wiring and public regression tests.
- Excluded scope: original private Git history, operational artifacts, runtime or
  bakeoff data, provider responses, databases, traces, private prompts and
  unrelated product modules.
- Public history: a new public root; no original private Git history is included.

Desktop code and associated tests are outside this public scope.

## Included source and transformations

| Public path | Public snapshot treatment |
| --- | --- |
| `src/veritas/contracts.py` | Retains the declarations required by TaskSpec, observations, evidence, reports and RunState; unrelated product declarations are excluded. |
| `src/veritas/state.py` | Retains explicit RunState transitions; V1.1 makes PAUSED terminal for the current run record. |
| `src/veritas/verifier.py` | Retains the deterministic verification gates; V1.1 rejects any parent-traversal path segment and labels the destructive check as a pattern gate. |
| `src/veritas/subprocess_util.py` | Retains the bounded subprocess helper used by applicable checks. |
| `src/veritas/verification_debt.py` | Retains only command identity normalization; V1.1 uses a controlled Python interpreter basename pattern, not substring matching. |
| `tests/test_state.py` | Retains the two extracted state tests and adds the V1.1 PAUSED terminal regression. |
| `tests/test_public_core.py` | Synthetic public completion tests plus V1.1 command-identity and path-integrity regressions. |

The public modules retain the selected verifier mechanism and contract vocabulary;
the snapshot intentionally omits private history and operational metadata. The
V1.1 changes are limited to verifier/state boundary hardening and dependency
hygiene.

## Dependency map

```text
demo -> contracts, state, verifier + Python standard library
state -> contracts + pydantic
verifier -> contracts, state, normalize_argv, subprocess_util + standard library
contracts -> pydantic + standard library
normalize_argv -> re + standard library
subprocess_util -> subprocess, typing
tests -> installed public package + standard library unittest
```

Seven installed modules form the public package. No AgentRunner, provider,
memory extension, GUI, original state store, private sibling or system prompt
is needed.

## Example wiring and execution scope

`src/veritas/demo.py` is example wiring, not a complete agent runner. It mirrors
the intended report/conclusion/PASS-only-finish sequence, delegating the decision
to the deterministic verifier and state class.

The scripted executor uses standard-library writes on two known synthetic files
in its own temporary directory. Observations use the existing contract vocabulary;
this does not mean the production ToolRuntime is included. A small observer
captures real artifact bytes and digests, and the verifier independently parses
the requested constants. There is no model loop, permission-system replacement,
database, recovery system or persistent trace store.

All non-PASS conclusions block the demo attempt; the full product's repair budget
and orchestration are deliberately not copied. The Python caller is trusted.
The artifact does not prevent an adversarial caller from forging observations,
criteria or state.

## Design philosophy

The public principles are Restraint, Minimal Change, Evidence Before Confidence,
Independent Verification and Provable Completion.

The README maps each principle to the actual module boundary, fixture behavior
or tests. Broader quality benchmarks are not distributed or presented as
implemented evaluation. The core public statement is: “Completion must be
demonstrated by evidence, not declared by the agent itself.”

## Data classification

| Public data location | Classification | Basis |
| --- | --- | --- |
| `src/veritas/demo.py` | SAFE_SYNTHETIC / SAFE_GENERATED | Artificial constant-setting task, fixed IDs, generated file digests and two locally generated text fixtures. No imported task or conversation data. |
| `tests/test_public_core.py` | SAFE_SYNTHETIC | Newly authored constant/evidence fixtures and deliberate corruptions of those fixtures. No external data. |
| `tests/test_state.py` | SAFE_SYNTHETIC | Two original tests construct artificial run/task IDs and verification enums only. |
| Configuration and documentation | SAFE_GENERATED | New snapshot metadata and reviewed non-sensitive source identity. |
| All original task datasets, traces, reports and sessions | EXCLUDED | Not part of the approved source allowlist; no filename-based assumption of public safety. |

All public examples and tests use synthetic/generated data. Demo JSON is printed
to stdout and not stored in the repository. Temporary files are owned by the
example/test and removed afterwards.

## Test provenance

- Two state tests are retained from the public extraction baseline; one state
  regression and five verifier-boundary regressions were added for V1.1.
- Twenty-one focused public tests are now included in the snapshot.
- No original broad suite, desktop E2E suite, benchmark corpus or schema-evaluation
  result is used as the snapshot's test count.
- Snapshot tests were run against a non-editable installed package in a fresh
  virtual environment, from a separate public-file copy.
- The installed package's seven module contents match the public source.
- Syntax/import checks cover the public Python files.
- The tests do not validate every branch of the retained verifier.

## Ownership and third-party classification

The project owner explicitly confirmed authority to publish the selected
self-owned core code and tests, including AI-assisted development, and approved
MIT. This does not authorize redistribution of unknown third-party code.

The inspected allowlist contains no vendor files, generated bundles, external
reference implementations or copied dependency sources. Package dependencies
retain their own licenses; see [NOTICE.md](NOTICE.md). This is a scoped source
review and owner authorization, not an independent legal opinion.

## Explicitly excluded

Original Git history, runtime/bakeoff artifacts, raw benchmarks, databases, JSONL
traces, provider responses, local state, desktop code, memory/retrieval extensions,
research archives, screenshots, operational documents, downloaded datasets,
models, caches, build outputs, broad agent orchestration and private prompts.

## Capability boundary

Verified behavior is limited to explicit state transitions, the demonstrated
verification checks, evidence linkage, and refusal to complete the synthetic
negative cases. No production reliability, general semantic correctness,
full memory/RAG, real-provider success rate or embodied experiment is claimed.
Logical module independence is not process isolation or evidence authenticity.
Command identity normalization is syntactic, not executable attestation. Path
checks validate observed strings against declared boundaries, not symlink-aware
filesystem confinement. The Destructive Pattern Gate is heuristic and
pattern-based, not complete tool confinement; actual permission and confinement
belong to the ToolRuntime or caller boundary.
