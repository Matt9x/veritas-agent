# Provenance and public boundary

- Source project: Veritas
- Source repository identifier: `project-0010-Veritas`
- Source branch: `veritas-product`
- Source commit: `cbed7158c8ecd42749026076213b29817a6c54c1`
- Source parent: `f8f7c309f82a9741ddf1096519d0267068911d20`
- Snapshot date: 2026-09-15
- Source state: clean, matching the approved PC-01 baseline at extraction.
- Public history: new root history; the original Git directory is not included.

The source project had a separately closed desktop repair milestone. Its desktop
test results are not public-snapshot results. This artifact does not include
desktop code.

## Included source and transformations

| Path | Source SHA-256 | Transformation |
| --- | --- | --- |
| `src/veritas/contracts.py` | `a3262fc6a48de780369ceb1e6f29ed271c1cbe00b0a662ffa9d74244afd894be` | Whole declarations required by TaskSpec, observations, evidence, reports and RunState retained; provider/actor/termination/result declarations and unused imports excluded. No retained method body rewritten. |
| `src/veritas/state.py` | `7ff99481decb0d5a6305cf58b10620b658f0eeda1a16f853cb1d79d2165edb39` | Unchanged source, LF line endings. |
| `src/veritas/verifier.py` | `ccb2bf2d5ea9690463bf83f2121e15ede733a6986019a44f668ddca0ee8dc451` | Unchanged source, LF line endings. |
| `src/veritas/subprocess_util.py` | `1c467efd3a4f0eeca9ea6e9b2b615c5ce4ae4392b0a70874b993591bc0b8fdf9` | Unchanged source, LF line endings. |
| `src/veritas/verification_debt.py` | `90f217d86728033627cb0d70621b97d7b81b9a7a8b38d97d1846a936bb86bf47` | Only normalize_argv and its pathlib import retained; no convergence prompts, telemetry or controller. |
| `tests/test_state.py` | `009d7c579552c5beabcffa633355a5bd1c45f54fc82f8d650e39b1d285fac8e7` | Unchanged source, LF line endings. |

The hashes describe original source bytes, before extraction. Retained class and
function definitions were checked for AST equality with the source. The complete
verifier is retained: removing individual gates would change the research
mechanism. Only dependency declarations were reduced in `contracts.py`, and only
`normalize_argv` remains from the convergence module.

## Dependency map

```text
demo -> contracts, state, verifier + Python standard library
state -> contracts + pydantic
verifier -> contracts, state, normalize_argv, subprocess_util + standard library
contracts -> pydantic + standard library
normalize_argv -> pathlib
subprocess_util -> subprocess, typing
tests -> installed public package + standard library unittest
```

Seven installed modules form the public package. No AgentRunner, provider,
memory extension, GUI, original state store, private sibling or system prompt
is needed.

## Example wiring and execution scope

`src/veritas/demo.py` is new example wiring, not an extraction of the complete
AgentRunner. It mirrors the source runner's report/conclusion/PASS-only-finish
sequence, delegating the decision to the original verifier and state class.

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

The source's frozen `VERITAS_PHILOSOPHY.md` informed the owner's requested public
principles: Restraint, Minimal Change, Evidence Before Confidence, Independent
Verification and Provable Completion.

The README maps each principle to the actual module boundary, fixture behavior
or tests. The full philosophy document and eight-metric quality dashboard are
not distributed or presented as implemented evaluation. The core public statement
is: “Completion must be demonstrated by evidence, not declared by the agent itself.”

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

- Two state tests are retained unchanged.
- Sixteen focused public tests were newly authored for this extraction.
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
