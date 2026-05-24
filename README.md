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

It has been tested in production-style workflows with **OpenClaw** and **Hermes**.

## Files

- [`SKILL.md`](./SKILL.md) — the complete skill/runbook.

## Default model pattern

The skill documents a five-reviewer setup:

- GPT-5.5
- DeepSeek V4 Pro
- Kimi K2.6
- Qwen 3.6 Plus
- GLM-5.1

The orchestrator is GPT-5.5 in the tested setup, but the method is agent-agnostic.

## Core ideas

- Reviewers are **analysis-only**; they do not post to GitHub or edit files.
- The orchestrator prepares local snapshots/diffs, spawns reviewers, deduplicates findings, verifies obvious false positives, and publishes one consolidated result.
- PR reviews avoid GitHub pending-review collisions by using standalone inline PR comments instead of parallel draft reviews.
- Non-PR reviews are delivered as Markdown files in the reviewed repo.

## Usage

Copy `SKILL.md` into your agent's skills/runbooks directory, or adapt its workflow into your agent instructions.

For OpenClaw-style skills, place it as:

```text
skills/multireview/SKILL.md
```

Then ask your agent for a multi-review of a PR or a repo scope.

## Status

This skill is evolving from real multi-review workflows. Update it when you discover better reviewer prompts, model sets, false-positive filters, snapshot patterns, or publishing conventions.
