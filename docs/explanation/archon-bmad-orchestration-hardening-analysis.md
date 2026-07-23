---
title: "Archon-BMAD Orchestration Hardening"
description: Lessons and priorities for durable, reusable Archon-BMAD automation
---

# Archon-BMAD Orchestration Hardening Analysis

**Date:** 2026-07-15

**Status:** Proposed

**Priority focus:** Critical and High items must be addressed before unattended multi-repository rollout.

## Purpose

This document records lessons from a long-running Archon/BMAD implementation and integration-validation cycle for
the Arize AX tracing feature in `migration-tool-agents`.

The goal is to make the process repeatable across different repositories, programming languages, build systems, and
technology stacks while preserving interoperability between BMAD planning/review workflows and Archon execution.

This is an analysis and roadmap, not an implementation specification. Items remain unchecked until implemented and
validated.

## Executive Summary

The primary lesson is that this was not mainly an LLM-quality problem. The prompts contained most of the right intent,
but safety, durability, ownership, and completion rules were expressed as prose instead of enforced as executable
invariants.

The generalized solution should be a deterministic orchestration kernel with bounded LLM workers:

- Code owns preflight, branch topology, worktree leases, Git operations, state transitions, retries, command
  execution, push verification, provenance, artifact promotion, heartbeat events, resume, and final invariants.
- LLMs own implementation choices, root-cause reasoning, ambiguity classification, test-design reasoning,
  adversarial review, and remediation proposals.
- Each LLM phase receives a compact structured context packet rather than the complete workflow manual.
- Each phase returns schema-validated output.
- Read-only phases are enforced as read-only.
- A final reconciliation gate proves every production delta on an integration branch exists on a durable owning
  branch before success is possible.

The most important design rule is:

> Prompts may express intent, but only code and validators should enforce durability, branch ownership, permissions,
> and completion.

## Observed Process

The run implemented eight reopened stories across five epics, performed story reviews, built cumulative integration
branches, generated acceptance tests, and ran antagonistic integration reviews.

The process eventually produced a clean result:

- All five epics and all fifteen stories were completed.
- The final cumulative review found zero Critical and zero High findings.
- The final durable stack passed 224 tests, with five expected skips.

However, successful completion required substantial manual supervision and post-run orchestration:

1. Verify current BMAD skills and Archon workflows were installed.
2. Verify identical BMAD content existed on all fifteen story branches.
3. Diagnose a worktree branch-lock failure and relaunch.
4. Monitor process liveness using `ps`, CPU counters, file timestamps, and log tails during long silent phases.
5. Distinguish a real hang from an active but quiet LLM process.
6. Interpret a misleading `main` divergence that existed only in the run's disposable worktree.
7. Discover that production fixes had landed only on the disposable integration branch.
8. Determine the correct owning story branch for those fixes.
9. Cherry-pick the missing fix onto Story 4.3.
10. Cascade-rebase Stories 4.4, 5.1, and 5.2.
11. Resolve mechanical conflicts.
12. Run targeted and full validation.
13. Push and verify all corrected durable branches.
14. Promote review artifacts and final sprint status to the durable stack tip.
15. Clean up stale Archon worktrees.

## Concrete Evidence

### Integration-only production fixes

The workflow prompt contained a hard rule that fixes must never remain on a disposable integration branch. Despite
that instruction, two important commits existed only on `integration/epic-5-review-20260715-162033`:

- `9717b81`: applied the round-two correct-course production changes on the integration branch.
- `11abdeb`: bound `run_session_id()` in `AgentRunner.run_stream()`.

The latter fixed a genuine High finding: streaming and non-interactive HITL execution could emit spans without
`migration.session_id`.

The run state still reported:

```json
{
  "integrationFixRetry": 0,
  "integrationFixLog": []
}
```

This means the integration reviewer mutated production code directly instead of reporting the finding and routing it
through the correction phase.

### Disposable acceptance tests

The functional-test prompt explicitly generated epic-level and project-level acceptance tests only on the disposable
integration branch. These tests were useful evidence, but they were not assigned a durable destination and could have
been lost with worktree cleanup.

### Stale or branch-relative status

`sprint-status.yaml` was declared the source of truth, but its content differed by branch. `main` was stale while the
stack tip contained current implementation status. Story-file status, sprint status, and commit history could also
disagree.

### Misleading default-branch escalation

The workflow treated divergence in its own disposable worktree's local `main` as a human decision concerning the real
repository. The actual repository's local and remote `main` remained aligned. The run should have used an immutable
remote base ref rather than depending on worktree-local `main`.

### Weak observability

Logs often remained unchanged for long periods even while the Claude child process was active. Determining health
required:

- checking parent and child PIDs;
- sampling process CPU time;
- inspecting file modification times;
- checking pytest cache updates;
- repeatedly tailing buffered logs.

### State inconsistency

The final state included:

```json
{
  "epicsValidated": [],
  "currentValidationEpic": 5,
  "status": "needs-attention"
}
```

At the same time, integration-branch commits and reports asserted clean validation results. State was not finalized
from durable evidence.

## Prompt Chain Analysis

| Layer | What worked | What failed |
|---|---|---|
| User instructions | Clearly required project-level tests, repeated correction, owning-story branches, current BMAD installation, and final antagonistic review | Requirements accumulated conversationally rather than becoming one immutable run contract |
| Supervisor launch prompt | Named all target stories, the approved proposal, stacked-branch rule, and validation cycle | It was a dense natural-language paragraph without an explicit branch graph, ownership map, artifact policy, validation matrix, or resume contract |
| Workflow prompt | Described stack handling, testing, integration review, and correction | It was a large monolithic prompt with contradictory responsibilities and no enforcement mechanism |
| Archon runtime | Preserved a run ID, worktree, report, and state file | It lacked useful structured progress, first-class resume, and automatic reconciliation |
| Post-run supervision | Recovered lost fixes and established a durable clean stack | Required manual intervention that should be part of the workflow |

### Prompt contradictions

The workflow stated that only the `commit` phase may run `git commit`, while other sections instructed functional-test,
retro, and correction phases to commit. Such contradictions make compliance probabilistic.

### Repetition is not enforcement

Repeating "never commit fixes on the integration branch" did not prevent exactly that behavior. The correct solution
is a phase permission boundary and post-phase diff validator.

## Critical Changes

These items must be completed before the workflow is trusted for unattended operation.

- [ ] **C1 - Replace the monolithic prompt with a deterministic orchestration kernel.**
  Code must execute state transitions, Git operations, retries, and validators. LLM workers should perform bounded
  reasoning tasks.

- [ ] **C2 - Enforce phase permissions.**
  Define allowed mutations for each phase. For example:
  - review: reports only;
  - functional-test synthesis: tests and test reports only;
  - correction: declared files on declared owning branches;
  - reporting: report artifacts only.
  Fail the phase if unauthorized files change.

- [ ] **C3 - Add an integration-only production-delta gate.**
  Before leaving review, correction, or finalization, compare the integration branch with the durable story stack. Any
  production-code delta that exists only on the integration branch must route back to correction.

- [ ] **C4 - Use immutable remote base refs.**
  Create an Archon-owned synthetic base ref directly from the configured remote default branch. Never rely on or
  mutate worktree-local `main`.

- [ ] **C5 - Compile an immutable run manifest before launch.**
  The manifest must contain:
  - proposal path and content hash;
  - exact target stories and findings;
  - ordered branch graph;
  - base remote and SHA;
  - branch ownership policy;
  - durable artifact policy;
  - required validation scopes;
  - completion invariants;
  - resume policy.

- [ ] **C6 - Make push verification transactional.**
  A phase is not durably complete until the remote ref equals the expected SHA. Pushes receive bounded retry and
  explicit remote verification. Offline progress may be `locally_complete`, never `durably_complete`.

- [ ] **C7 - Define durable destinations for generated acceptance tests.**
  - Story regression test: owning story branch.
  - Cross-story, epic, or project acceptance test: declared quality branch or stack-tip quality commit.
  - Diagnostic probe: disposable integration branch.

- [ ] **C8 - Add a deterministic finalizer.**
  Final success requires:
  - every target story is done;
  - every required remote push is verified;
  - remote stack ancestry is valid;
  - all required tests are durable and passing;
  - no integration-only production delta exists;
  - zero Critical and zero High findings remain;
  - state agrees with commits, reports, and branch content.

- [ ] **C9 - Add native resumability with idempotent checkpoints.**
  Resume by run ID without repeating commits, pushes, reviews, tests, or reports. Every phase records expected pre/post
  SHAs and idempotency keys.

## High Changes

These items should be completed before broad multi-repository rollout.

- [ ] **H1 - Add a built-in Archon supervisor and heartbeat.**
  Provide phase, substep, elapsed time, active command, last artifact, heartbeat, and blocked reason through an
  `archon workflow watch`-style interface.

- [ ] **H2 - Add deterministic launch preflight.**
  Verify branch locks, worktrees, remotes, credentials, network, clean state, workflow hash, BMAD hash, tools, and
  target refs before the first LLM turn.

- [ ] **H3 - Persist the explicit stack graph.**
  Record ordered branches, parent refs, PR bases, old/new SHAs, and target story associations. Do not infer the complete
  topology from branch-name sorting.

- [ ] **H4 - Add an evidence-based ownership resolver.**
  Use blame, commit introduction, branch ancestry, story file lists, and confidence scoring. "First branch containing
  the file" is insufficient because every descendant also contains it.

- [ ] **H5 - Introduce a repository validation profile.**
  Generate and commit `_bmad/validation-profile.yaml` with:
  - detected ecosystems and packages;
  - install, build, lint, type-check, and test commands;
  - story, epic, cross-epic, and project test tiers;
  - timeouts and resource limits;
  - required environment;
  - artifact paths;
  - allowed commands.

- [ ] **H6 - Build an acceptance-criteria traceability matrix.**
  Map requirement to epic/story AC, implementation surface, owning branch, and durable test. Review uncovered rows
  rather than rereading all raw artifacts without a coverage model.

- [ ] **H7 - Require structured phase outputs.**
  Each worker must return schema-validated:
  - decision;
  - evidence;
  - changed files;
  - branch owner;
  - commands and results;
  - artifacts;
  - next phase;
  - escalation reason.

- [ ] **H8 - Make correction transactional.**
  Plan all fixes, prove ownership, apply in stack order, verify tests, push every branch, cascade, rebuild, and revalidate.
  Preserve partial progress as a resumable transaction.

- [ ] **H9 - Add a safe conflict classifier.**
  Automatically resolve only allowlisted mechanical conflicts such as regeneratable lockfiles, known generated status
  files, and monotonic version conflicts. Escalate semantic conflicts.

- [ ] **H10 - Stream structured runtime events.**
  Stream tool activity and state transitions separately from buffered LLM prose. Monitoring should not require another
  LLM or log-file polling.

- [ ] **H11 - Derive correction accounting from evidence.**
  Detect integration fixes from commits and diffs even if an LLM bypasses the intended route. `integrationFixRetry` and
  `integrationFixLog` must reflect reality.

## Medium Changes

- [ ] **M1 - Add a phase-specific prompt compiler.**
- [ ] **M2 - Add workflow and prompt linting for contradictory permissions and responsibilities.**
- [ ] **M3 - Use unique artifact IDs based on run ID, scope, and review round.**
- [ ] **M4 - Automate worktree and branch-lease cleanup after durable reconciliation.**
- [ ] **M5 - Route models by phase complexity and risk.**
- [ ] **M6 - Use ranked evidence and progressive retrieval to bound context.**
- [ ] **M7 - Route accepted retrospective action items into tracked backlog work.**
- [ ] **M8 - Record an installation manifest containing BMAD source commit, workflow commit, skill hashes, and timestamp.**

## Low Changes

- [ ] **L1 - Improve `worktree_file_copy_partial` messaging when no copy is required.**
- [ ] **L2 - Normalize `archon complete` identifiers and print exact cleanup commands.**
- [ ] **L3 - Publish expected phase-duration ranges and slow-but-healthy indicators.**
- [ ] **L4 - Clearly distinguish ephemeral-worktree state from actual repository state in reports.**
- [ ] **L5 - Register custom test markers to remove distracting warnings.**

## Failure Mode Analysis

| Component | Failure mode | Detection | Prevention |
|---|---|---|---|
| Launch | Target branch is checked out elsewhere | Worktree lease inventory | Reject during preflight |
| Installation | BMAD or workflow versions differ | Content-hash mismatch | Installation manifest and ref-level audit |
| Base selection | Worktree inherits polluted local default branch | Local base differs from configured remote SHA | Synthetic immutable remote base |
| Stack discovery | Numeric naming gives wrong topology | Parent differs from PR base or ancestry | Explicit stack graph |
| Status | Branch-local sprint status is stale | Status conflicts with commits or stack tip | Resolve from declared tip and validate |
| Ownership | Descendant is mistaken for owner | Selected branch did not introduce defective lines | Provenance-based ownership proof |
| Review | Reviewer changes source | Post-phase source diff | Read-only enforcement |
| Correction | Fix exists only on integration branch | Integration-only production delta | Mandatory reconciliation gate |
| Tests | Valuable tests disappear with integration branch | Test absent from durable refs | Artifact disposition policy |
| Rebase | Agent guesses through semantic conflict | Conflict markers or behavioral change | Mechanical allowlist, semantic escalation |
| Push | Local success is not on remote | Remote SHA mismatch | Transactional push verification |
| State | State disagrees with evidence | Schema/finalizer mismatch | Deterministic finalizer |
| Monitoring | Active run appears hung | Missing heartbeat | Structured heartbeat |
| Interruption | Process dies after useful work | Durable checkpoint but no process | Native resume |
| Portability | Wrong ecosystem commands run | Validation-profile mismatch | Reviewed command profile |
| Completion | Workflow reports success without durable result | Missing refs, artifacts, or statuses | Final invariant gate |
| Cleanup | Stale worktrees block future runs | Completed run still holds leases | Automated teardown |
| Prompt evolution | Rules contradict each other | Static prompt/workflow analysis | Prompt compiler and linter |

## Second-Order Guardrails

### Do not reset user branches

Automatic recovery must not reset legitimate user work. Use an Archon-owned synthetic base and isolated refs instead
of rewriting local `main`.

### Do not auto-resolve semantic conflicts

Automatic conflict handling should be narrow and deterministic. Mechanical conflict resolution must be followed by
regeneration and validation.

### Do not put every generated test on a story branch

Cross-epic and project acceptance tests often have no single story owner. Use an explicit quality artifact destination
rather than assigning them arbitrarily.

### Do not turn monitoring into log spam

Emit heartbeats on state changes plus a low-frequency liveness interval. Heartbeats must not invoke an LLM.

### Do not let ecosystem discovery execute arbitrary commands

Discovery may propose commands, but runtime executes only commands from a reviewed validation profile, with timeouts
and resource limits.

### Do not call local progress durable success

Allow offline checkpointing, but require remote verification for final success.

### Do not force low-confidence ownership decisions

Return ownership evidence and confidence. Ambiguous or multi-owner fixes become an explicit correction plan or human
decision.

### Do not let resume repeat side effects

Resume must recognize already-created commits, branches, pushes, PRs, tests, and reports.

### Do not over-constrain LLM reasoning

Schemas should be strict around permissions, evidence, inputs, and outputs while leaving implementation and root-cause
reasoning open-ended.

## Proposed Run Manifest

```yaml
schema_version: 1
intent: remediate-and-revalidate

proposal:
  path: _bmad-output/planning-artifacts/sprint-change-proposal.md
  sha256: "<content-hash>"

targets:
  - story: "4.3"
    branch: feat/example/story-4.3
    findings: [F2, F5]

stack:
  base_remote: upstream
  base_branch: main
  base_sha: "<immutable-sha>"
  ordered_branches:
    - feat/example/story-1.1
    - feat/example/story-1.2

policies:
  production_fix_destination: owning_story_branch
  review_mutability: reports_only
  integration_branch_mutability: tests_and_reports_only
  persistent_acceptance_tests: required

validation:
  profile: _bmad/validation-profile.yaml
  required_scopes:
    - story
    - epic
    - cross_epic
    - project

completion:
  all_target_stories_done: true
  zero_critical_high: true
  no_integration_only_production_delta: true
  remote_pushes_verified: true
  remote_stack_ancestry_valid: true

resume:
  policy: last_durable_checkpoint
```

## Proposed Skills

### Critical: `bmad-archon-supervisor`

An umbrella skill for:

1. compiling and reviewing the run manifest;
2. running preflight;
3. launching Archon;
4. monitoring structured events;
5. resuming interrupted runs;
6. handling safe operational recovery;
7. reconciling integration-only changes;
8. running final remote and invariant verification;
9. cleaning up worktrees.

### Critical: `bmad-stack-audit`

Build and verify branch topology, parentage, ownership, worktree leases, remote SHAs, PR bases, and stack-tip status.

### Critical: `bmad-run-reconciliation`

Compare disposable integration output with durable branches, classify missing commits/tests/reports, move each artifact
to its proper durable destination, cascade, test, push, and verify.

### High: `bmad-validation-profile`

Discover technology stacks and propose a committed validation profile covering commands, test tiers, timeouts, package
boundaries, and durable acceptance-test locations.

### High: `bmad-archon-recovery`

Resume from durable state after process loss, network failure, branch conflict, or human decision without replaying
completed side effects.

### Medium: `bmad-workflow-prompt-audit`

Review workflow prompts for contradictions, missing permissions, excessive context, unenforceable rules, ambiguous
ownership, and incomplete state contracts.

### Medium: `bmad-run-postmortem`

Analyze run events, retries, escalations, manual interventions, lost artifacts, elapsed time by phase, and proposed
workflow improvements.

## Recommended Implementation Sequence

### Phase 1: Immediate hardening

Implement:

- phase permissions;
- integration-only production-delta gate;
- immutable remote base;
- transactional push verification;
- durable test disposition;
- deterministic finalizer;
- launch preflight;
- structured heartbeat.

### Phase 2: Controller extraction

Implement:

- run-manifest schema;
- explicit stack graph;
- schema-validated state;
- idempotent phase executor;
- native resume;
- transactional correction.

### Phase 3: Cross-repository generalization

Implement:

- validation profiles;
- acceptance traceability;
- artifact disposition profiles;
- ecosystem adapters;
- bounded phase-specific context packets.

### Phase 4: Skill layer

Add:

- `bmad-archon-supervisor`;
- `bmad-stack-audit`;
- `bmad-run-reconciliation`;
- `bmad-validation-profile`;
- `bmad-archon-recovery`;
- audit and postmortem skills.

### Phase 5: Reliability testing

Exercise:

- process death and machine sleep;
- network loss during fetch and push;
- branch locks;
- stale refs;
- polluted local default branches;
- mechanical and semantic rebase conflicts;
- failed remote verification;
- reviewer attempts to modify source;
- integration-only fixes;
- interrupted resume after every side-effect boundary.

## Completion Criteria for This Roadmap

The Critical and High roadmap is complete only when an unattended test run can demonstrate:

1. A branch lock is rejected before launch.
2. A polluted local default branch cannot affect the run base.
3. A reviewer cannot modify production code.
4. An integration-only source fix is detected and routed to its durable owner.
5. A failed push prevents durable success.
6. Process termination can resume without duplicate side effects.
7. Generated project-level acceptance tests survive worktree cleanup.
8. A semantic rebase conflict pauses with a resumable checkpoint.
9. Monitoring reports useful progress throughout long LLM phases.
10. Final state is mechanically consistent with remote refs, tests, reports, and sprint status.
