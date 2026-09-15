# Veritas Public Specification

## 1. Purpose

Veritas is a research prototype for **evidence-constrained task completion** in autonomous agents.

Its central question is:

> **When should an agent be allowed to claim that a task is actually complete?**

Veritas treats executor success claims as non-authoritative. Completion is a separate decision that depends on explicit task state, declared criteria, observed evidence and an independent verification result.

This document specifies the public V1.1 artifact. It describes implemented invariants and boundaries; it is not a specification for the complete private Veritas product.

## 2. Core Principle

An executor MUST NOT authorize its own completion.

A run may enter `DONE` only after:

1. the run has entered verification;
2. evidence has been collected and bound to the declared task criteria;
3. the deterministic verifier produces `PASS`;
4. the state machine accepts that verdict from the correct phase.

The core invariant is:

```text
executor success claim != verified completion
```

or, operationally:

```text
DONE requires VERIFYING + PASS
```

## 3. Terms

### 3.1 Task specification

A task specification describes the conditions under which a run may be considered complete.

The public artifact may include declarations such as:

- criteria;
- allowed paths;
- structural / intent assertions;
- declared verification commands;
- protected checks.

The quality of verification is bounded by the quality and completeness of these declarations.

### 3.2 Run state

`RunState` records the lifecycle of one execution attempt.

The retained public phases include:

- `CREATED`
- `RUNNING`
- `WAITING_FOR_INPUT`
- `VERIFYING`
- `DONE`
- `BLOCKED`
- `STOPPED`
- `PAUSED`

`PAUSED` is terminal for the current run record. A future resume would be represented by a new run seeded from persisted state; resume orchestration is not implemented in this snapshot.

### 3.3 Observation

An observation is a caller-supplied record of something that occurred during execution, such as a command, mutation, test result or artifact state.

The verifier assumes observation capture is honest. It does not provide cryptographically trusted observation collection.

### 3.4 Evidence record

An evidence record binds a task criterion to an observed invocation / artifact reference.

The public model may include identifiers for:

- evidence;
- run;
- criterion;
- observation invocation;
- artifact;
- freshness;
- content digest.

Evidence is not authoritative merely because an object exists. Linkage, freshness and consistency checks are part of verification.

### 3.5 Verification verdict

The public verifier distinguishes outcomes including:

- `PASS`
- `PASS_WITH_WARNINGS`
- `REPAIR_REQUIRED`
- `BLOCKED`
- `STOPPED`

Only `PASS` can authorize `DONE` through the public state model.

## 4. Completion Authority Invariant

`RunState.finish()` MUST reject completion unless both conditions hold:

```text
phase == VERIFYING
verification_status == PASS
```

Any other phase or verdict MUST NOT produce `DONE`.

In particular:

```text
PASS_WITH_WARNINGS != PASS
executor says success != PASS
artifact changed != PASS
checks green != PASS unless required evidence/criteria also support completion
```

This separation between execution and completion authority is the primary public invariant.

## 5. Execution / Verification Separation

The public artifact uses a scripted executor only to demonstrate behavior.

The verifier is deterministic and has no model call. It evaluates task declarations, observations, evidence and artifact state separately from executor prose.

This independence is **logical/module-level** only.

It is not:

- process isolation;
- security isolation;
- an independent organization;
- cryptographic attestation;
- formal proof of arbitrary correctness.

A malicious Python caller that forges state or observations is outside the guarantees of this artifact.

## 6. Evidence Binding Invariant

A criterion that requires evidence MUST NOT be treated as satisfied merely because execution reported success.

Evidence used for completion MUST be associated with the current run and the relevant criterion / observation as required by the verifier.

The verifier rejects or degrades completion when evidence is:

- missing;
- stale;
- associated with another run;
- associated with an observation that does not support the declared operation;
- inconsistent with the artifact state reread during verification;
- otherwise insufficient under the declared criterion.

## 7. Artifact Re-Read Invariant

Captured evidence does not permanently authorize an artifact.

When supported by the check, the verifier may reread the artifact and compare its current content or digest to the evidence that was captured earlier.

A post-capture drift can therefore invalidate completion.

The public tests exercise this as a negative case.

## 8. Command Identity Contract

Declared verification commands are compared against observed command executions using syntactic normalization.

### 8.1 Controlled interpreter normalization

A leading executable may be removed only when its basename matches a controlled Python interpreter pattern such as a supported `python`, `python.exe`, `python3` or versioned Python name.

Arbitrary names that merely contain the substring `python` MUST NOT be normalized as Python interpreters.

Examples:

```text
python.exe -m pytest        -> interpreter-normalized
python3.14.exe -m pytest    -> interpreter-normalized
evilpython.exe -m pytest    -> NOT interpreter-normalized
python-wrapper.exe -m pytest -> NOT interpreter-normalized
```

This is syntactic command normalization, not runtime executable attestation. The verifier does not cryptographically prove which binary ran.

## 9. Path Constraint Contract

Observed changed paths are validated as declared strings.

The public verifier rejects at least:

- absolute POSIX paths;
- drive-letter absolute paths;
- leading parent traversal;
- any normalized path segment equal to `..`;
- Git-internal paths covered by the gate;
- credential-sensitive names covered by the gate;
- paths outside declared `allowed_paths`.

Examples:

```text
allowed/file.txt              -> may be allowed
../outside.txt                -> rejected
allowed/../../outside.txt     -> rejected
safe/../outside.txt           -> rejected
```

These are string / declared-boundary checks. They are not symlink-aware filesystem confinement. Actual filesystem and permission isolation belong to the ToolRuntime or caller boundary.

## 10. Declared Check Execution

A declared check is satisfied only when the verifier can associate it with an observed execution of the expected command and that execution succeeded under the public observation model.

A missing observation, mismatched command identity or non-success status MUST NOT be treated as a satisfied declared check.

The Tests Gate, final criterion coverage and evidence binding should use one consistent interpretation of declared-check execution so that one subsystem cannot treat a check as satisfied while another treats it as missing.

## 11. Semantic Completion Anchors

A changed file or passing generic check is not automatically proof that the requested behavior exists.

For tasks requiring an artifact mutation, the public verifier can require structural / intent assertions that inspect the artifact, such as declared imports, constants, symbols, call/return relationships or forbidden text.

Constraint-only assertions such as "this path changed" are not equivalent to semantic proof of requested behavior.

This mechanism is deliberately limited. It provides deterministic checks for declared properties; it does not solve general semantic verification of arbitrary software tasks.

## 12. Destructive Pattern Gate

The public `Destructive Pattern Gate` is a heuristic, pattern-based inspection over observed top-level argv and declared changed-path strings.

It may reject known destructive command patterns and suspicious path changes.

It MUST NOT be interpreted as:

- a shell parser;
- complete destructive-action prevention;
- command sandboxing;
- tool confinement;
- a permission system.

Nested payloads inside `powershell`, `cmd`, `bash`, `python -c` and similar wrappers are not generally parsed by this artifact.

Actual authorization and confinement belong to the ToolRuntime / caller boundary.

## 13. Run-State Semantics

### 13.1 Start

Only `CREATED` may enter `RUNNING` through `start()`.

### 13.2 Verification

Only the allowed active execution phase may enter `VERIFYING` under the state model.

### 13.3 Completion

Only `VERIFYING + PASS` may enter `DONE`.

### 13.4 Repair

A repair transition is allowed only from verification state with the corresponding repair-required verdict.

### 13.5 Pause

`PAUSED` is terminal for the current run record. Once paused, the same run record MUST NOT transition to `BLOCKED`, `DONE`, restart or begin a new verification cycle.

A resumed workflow would require a new run record outside this snapshot.

## 14. Fail-Closed Completion Cases

The public artifact demonstrates that the following situations do not produce verified completion:

- executor claims success but no required mutation occurred;
- executor changed the wrong value / artifact;
- required evidence is missing;
- execution is partial;
- evidence is stale;
- evidence belongs to another run;
- artifact changed after evidence capture;
- undeclared / out-of-scope change is reported;
- required semantic anchor is absent;
- command identity does not match the declared check;
- path evidence contains parent traversal;
- final verdict is not `PASS`.

The preferred outcome is explicit non-completion rather than inferring success from partial signals.

## 15. Reference Conformance Scenarios

### Scenario A — verified completion

```text
executor attempts requested mutation
observation records the actual mutation
evidence links the correct criterion to the observation
artifact reread satisfies the declared assertion
verifier -> PASS
RunState.phase == VERIFYING
```

Expected: `finish()` may transition to `DONE`.

### Scenario B — false completion claim

```text
executor says success
required mutation did not occur
```

Expected: verifier does not return an authorizing `PASS`; run does not enter `DONE`.

### Scenario C — wrong artifact

```text
write occurred
artifact value does not satisfy the declared assertion
```

Expected: no verified completion.

### Scenario D — missing evidence

```text
artifact happens to be correct
required criterion evidence is absent
```

Expected: no verified completion.

### Scenario E — command identity mismatch

```text
declared: python -m check
observed: evilpython.exe -m check
```

Expected: observed command does not satisfy the declared command identity.

### Scenario F — path traversal

```text
allowed_paths: ["allowed"]
observed changed path: allowed/../../outside.txt
```

Expected: rejected by path-integrity checking.

### Scenario G — paused run

```text
RUNNING -> PAUSED
```

Expected: the same run record cannot transition onward to `BLOCKED`, `DONE`, restart or re-enter verification.

## 16. Non-Goals

The V1.1 public artifact does **not** claim to implement:

- a complete autonomous coding agent;
- broad agent-task benchmark performance;
- a production ToolRuntime;
- process-isolated verification;
- cryptographically trusted observations;
- executable attestation;
- symlink-aware filesystem sandboxing;
- complete destructive command analysis;
- arbitrary semantic correctness proofs;
- durable trace storage;
- a full permission system;
- long-horizon recovery validation;
- MemoryGraph, RetrievalPolicy or RAG;
- embodied / robot / VLM / VLA evaluation;
- the full eight-metric Veritas quality benchmark.

## 17. Research Interpretation

Veritas should be interpreted as a **verification protocol / mechanism prototype**, not as a complete coding agent.

Its main implemented proposition is:

> **Execution and completion are separate authorities: task completion requires evidence that survives an independent verification gate.**

The public artifact demonstrates this proposition with deterministic synthetic examples and adversarial negative cases.

It does not establish that the verifier is universally correct, secure against arbitrary malicious callers, or sufficient for all autonomous-agent domains.

## 18. Current Evidence

The V1.1 release gate records:

- 24 passing public tests;
- one positive offline scenario reaching `DONE`;
- four negative demo scenarios refusing completion;
- adversarial command-identity regressions;
- parent-traversal path regressions;
- PAUSED terminal-state regression;
- isolated installation and offline reproduction.

These results support conformance of this public artifact to the invariants above; they are not an agent success-rate benchmark or a claim of state-of-the-art verification.

## 19. Shared Research Program

Veritas and Kairos explore two instances of a broader reliability question:

> **How can long-running agents know what to trust?**

- **Kairos:** stored memory is not authoritative; current retrieval eligibility depends on historical provenance and current source state.
- **Veritas:** executor success is not authoritative; current completion eligibility depends on evidence and an independent verification verdict.

The common design stance is that trust-relevant agent state should be justified by evidence rather than self-authorized by the component that produced it.
