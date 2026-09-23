# MultiReview Agent Skill

A reusable agent skill/runbook for running **consolidated multi-model code reviews**.

It supports two review modes:

1. **PR review** — multiple reviewer agents inspect a GitHub pull request; the orchestrator consolidates findings and posts one summary plus inline PR comments.
2. **Scope review without PR** — multiple reviewer agents inspect a folder/module/repo area; the orchestrator consolidates findings into a Markdown review file committed to the repo.

The workflow is designed for any capable coding agent that can:

- spawn independent reviewer agents/subagents,
- read local files and run shell commands,
- use GitHub CLI/API for repository access and PR comments,
- consolidate structured findings.

It has been tested in production-style workflows with **OpenClaw** and **Hermes**. The detached completion watchdog is OpenClaw-CLI + Linux/systemd only; the rest of the workflow (orchestrator, reviewer prompts, artifact contract) is platform-agnostic and works with any agent runtime, including Hermes.

> **Platform note:** the detached babysitter watchdog is Linux-only (uses `fcntl` and a `systemd-run --user` unit with `Restart=on-failure`). On macOS/non-systemd hosts, run the skill without the watchdog or use the (unverified) `nohup` fallback. The watchdog resolves binaries from `OPENCLAW_BIN`/`PATH` — set both explicitly in the unit environment.

## Files

- [`SKILL.md`](./SKILL.md) — the complete skill/runbook.
- [`scripts/review-babysitter.py`](./scripts/review-babysitter.py) — asynchronous reviewer/session watchdog.
- [`scripts/test-review-babysitter.py`](./scripts/test-review-babysitter.py) — focused regression tests for the watchdog.

## Default model pattern

The current default panel has three independent reviewers:

- GPT-5.6 Terra
- GLM 5.3
- DeepSeek V4.1 Flash

The orchestrator is whichever main agent runs the skill; its model is not hardcoded.


## Why multi-model review?

Different models have different failure modes, strengths, and blind spots. Running several independent reviewers increases the chance that at least one model catches a real bug that others miss.

The orchestrator then consolidates the results. Findings reported by multiple models carry more confidence because independent reviewers converged on the same issue. Single-model findings are still useful, but they are filtered more aggressively for evidence, traces, and false positives.

In practice, this gives two benefits:

- **Broader bug discovery:** diversity catches more edge cases.
- **Stronger signal:** consensus makes important findings easier to trust and prioritize.

The goal is not to produce five separate reviews; it is to produce one high-signal review backed by independent analysis.

## Core ideas

- Reviewers are **analysis-only**; they do not post to GitHub or edit files.
- The orchestrator prepares local snapshots/diffs, spawns reviewers, deduplicates findings, verifies obvious false positives, and publishes one consolidated result.
- Every consolidated finding identifies its supporting panel members as `(N/M reviewers: ...)`.
- The completion babysitter runs as a detached transient `systemd --user` service and explicitly wakes the orchestrator through an OpenClaw system event.
- Babysitter configuration and runtime state are separate, so monitoring cannot overwrite reviewer replacements or deadline changes.
- PR reviews avoid GitHub pending-review collisions by using standalone inline PR comments instead of parallel draft reviews.
- Non-PR reviews are delivered as Markdown files in the reviewed repo.

## Usage

Copy the complete repository contents into your agent's skills/runbooks directory, including `scripts/`.

For OpenClaw-style skills, place it as:

```text
skills/multireview/SKILL.md
```

Then ask your agent for a multi-review of a PR or a repo scope.

## Status

This skill is evolving from real multi-review workflows. Update it when you discover better reviewer prompts, model sets, false-positive filters, snapshot patterns, or publishing conventions.
