---
name: "multireview"
description: "Fix all self-review findings, durable wake, and mandatory finding attribution."
---

# Multi-Review (Consolidated Reviewers)

Run several independent reviewer subagents, then the orchestrator consolidates, deduplicates, and publishes one final review.

**Orchestrator:** the agent that reads and runs this skill (the main session). Do not hardcode a model — only reviewer subagents get explicit models.  
**Subagents:** analysis-only. They never post to GitHub or edit repo files.

## Variants

### A. PR review → GitHub PR comments

Use when the user asks to review a PR or merge request.

Output:
- one PR summary comment
- optional inline comments on the PR diff

### B. Scope review without PR → Markdown file

Use when the user asks to review a folder, module, repo area, deploy files, scripts, etc. without a PR.

Output:
- consolidated Markdown file committed to the repo, usually at the root of the reviewed scope:
  - `apps/REVIEW.md`
  - `scripts/REVIEW.md`
  - `deploy/REVIEW.md`
  - `<nested/module>/REVIEW.md`
  - for special scopes, use a clear descriptive name like `REVIEW_EDGE.md`.

## Default reviewer set

Use these three unless the user asks otherwise or a model is unavailable:

1. `openai/gpt-5.6-terra` — GPT-5.6 Terra (fixed as reviewer; also the consolidation model)
2. `zai/glm-5.3` — GLM 5.3 (was GLM-5.2 → openrouter; zai works again with 5.3)
3. `openrouter/deepseek/deepseek-v4.1-flash` — DeepSeek V4.1 Flash (roster actualizado 2026-09-17 por rmitxe; reemplaza a Qwen 3.7 Max)

**Panel alternativo usado en PR #118 (gates tender_workflows):** GLM 5.3 · DeepSeek · Grok 4.6:

- DeepSeek 4.1 Flash: **`openrouter/deepseek/deepseek-v4.1-flash`** (con la «v» — `deepseek-4.1-flash` sin «v» NO es válido y el gateway cae en fallback silencioso)
- Grok 4.6 (sustituto de Terra sin cuota): **`xai/grok-4.6`**

⚠️ Gotcha: si un model ID no existe en la config del gateway, el gateway usa el modelo default (glm-5.3-flash) pero los archivos de reseña igual se nombran con el modelo pretendido. La primera línea de todo consolidado debe declarar los modelos REALES que respondieron; una pasada en fallback no cuenta.

Default timeout: set `agents.defaults.subagents.runTimeoutSeconds` (e.g. 900 for large reviews, 600 for small PRs). Do NOT pass it per-spawn — `sessions_spawn` rejects a per-call `runTimeoutSeconds`. Use `cleanup: "keep"`.

## Disk output pattern (critical)

Subagent responses can truncate on long reviews. To prevent data loss, **every subagent must write its findings to a file on disk** and return only a short confirmation.

### How it works

1. The orchestrator prepares ONE review directory per round containing both snapshots and findings:
   - Canonical location (sandbox-safe): `~/.openclaw/workspace/.openclaw/tmp/<scope>-pr<N>-review/` (add a round suffix like `-r2` on subsequent rounds). NOTE: subagents run with file-tools sandboxed to the workspace — they can only `read` inside `~/.openclaw/workspace`; any findings/snapshots left in `/tmp` are unreadable to them and break consolidation. So the review directory MUST live under the workspace.
   - Contains: `base/`, `head/`, `PR_DIFF.patch`, `PR_METADATA.json`, and `findings-*.md` (all inside the review dir).
   - `/tmp/<scope>-pr<N>-review/` only works if subagents are spawned WITHOUT a filesystem sandbox (rare); do not rely on it.
   - Keep the dir gitignored: add `.openclaw/tmp/` to the workspace `.gitignore`.
2. Before spawning each subagent, the orchestrator assigns an output file path inside that same directory:
   - `<review-dir>/findings-terra.md`
   - `<review-dir>/findings-glm.md`
   - `<review-dir>/findings-deepseek.md`
3. The subagent task includes `OUTPUT_FILE: <path>` as a prominent instruction.
4. The subagent writes ALL findings to that file in `===FINDING===` format.
5. The subagent returns only: `DONE: wrote N findings to <path>` (or an error message).
6. After all subagents complete, the orchestrator reads the files from disk for consolidation.

### Output file instruction block

Include this in every subagent task (adapt path per model):

```text
OUTPUT FILE: <review-dir>/findings-<model>.md

Write ALL your findings to this file using the ===FINDING=== format below.
Do NOT return findings in your response message — write them to the file.
After writing, reply with ONLY: "DONE: wrote N findings to <path>"
If you found no issues, write a non-empty file containing exactly `NO FINDINGS`, then reply: `DONE: 0 findings to <path>`.
```

### Orchestrator consolidation from disk

After all subagents complete:

Use the exact filenames from the current round's `pending-state.json`; do not use a broad glob that can pull in prior rounds.

Read each expected file for the current round, extract `===FINDING===` blocks, then consolidate per the rules below. A file containing exactly `NO FINDINGS` is a successful zero-finding result. A missing or empty file is a reviewer failure, never a clean result.

## Common reviewer task contract

Every subagent task must include:

```text
IMPORTANT: You are an ANALYSIS-ONLY reviewer. Do NOT post anything to GitHub.
```

And require findings written to the output file in this exact format:

```text
===FINDING===
severity: [Critical|High|Medium|Low]
title: <one line title>
file: <path>
line: <line_number>
reasoning: <what the code does, why it's wrong, what triggers it>
fix: <concrete fix suggestion>
trace: <concrete trace for logic claims, or N/A>
===END_FINDING===
```

Tell reviewers to end the file with a short quality summary. Exception: with zero findings the file must contain ONLY the exact line `NO FINDINGS` — no summary, nothing else (the artifact validator accepts the sentinel line plus optional trailing text, but the safest zero-finding artifact is the bare sentinel).

## Reviewer discipline

Add these rules to reviewer tasks when the review is bug-finding oriented:

- Surface only impactful findings; skip trivial style issues covered by linters/formatters.
- Read surrounding files and called/calling code before flagging; do not rely only on a diff hunk.
- State assumptions explicitly when a claim depends on code outside the reviewed snippet.
- Do not flag the same issue twice; choose the most actionable location.
- If a bug is really a preference/improvement, mark it Low and frame it as a suggestion.
- For any logic claim (sort comparator, regex, off-by-one, async ordering, boolean condition), include a concrete trace. If no concrete trace can be built, downgrade to Low with "Consider verifying...".
- Aim for roughly 5-15 high-signal findings per reviewer. A long list of mostly Low findings is worse than a short list of important ones.

## What reviewers should look for

Surface only impactful findings. Default categories include:

- **Logic & errors:** runtime errors, logic gaps, race conditions, edge cases, bad state transitions.
- **Regressions:** behavior the change breaks compared to current main or documented behavior.
- **Security & hardening:** vulnerabilities, authorization/authentication mistakes, injection/unsafe input handling, secrets handling, unsafe external calls, exposed debug behavior, risky defaults. Include this category by default unless the user explicitly asks to omit it.
- **Performance & scalability:** N+1 queries, inefficient loops, unbounded waits, memory/file descriptor leaks, missing timeouts/retries, avoidable API cost.
- **Reliability & operations:** partial failures, idempotency, crash recovery, background workers, external API/tool failures, corrupt/partial artifacts, migration/upgrade issues.
- **Data consistency & persistence:** transaction boundaries, stale state, cache invalidation, schema/data migrations, rollback/commit ordering, filesystem-vs-DB consistency.
- **Parsing/extraction/API/UI correctness:** brittle parsers, format/schema drift, UI state bugs, integration contract mismatches.
- **Maintainability:** DRY violations, unclear naming, brittle invariants, dead code, confusing contracts — only when they create concrete risk.
- **Tests:** coverage gaps only when tied to a specific runtime risk or regression-prone path.

Skip trivial style issues already covered by linters/formatters/Prettier.

Security/hardening can create a lot of noise early in a project. If the user says **do not review security/hardening**, or asks for a focused non-security pass, include this exact instruction in each subagent task:

```text
DO NOT review security/hardening. Explicitly ignore security/privacy/vulnerability/hardening concerns unless they directly cause non-security correctness failure. Do not spend tokens on security.
```

## Multi-round reviews (subsequent rounds)

When reviewing a PR for a second (or later) round after the developer pushed fixes:

- **Reviewer subagents: always fresh-eyes.** Spawn them exactly as if it were the first round. Do NOT include the previous round's findings, issues, or fix descriptions in their tasks — no context about what was reported or fixed. This keeps their signal independent and avoids anchoring/duplicate bias.
- **Orchestrator verifies fixes in parallel.** While the reviewers run, the orchestrator (main agent) itself verifies the previous round's findings against the new head: read each finding, check the fix commit/diff, and classify as FIXED / PARTIALLY FIXED / NOT FIXED / REJECTED (with reasoning) / MOOT.
- **Final report consolidates both:** the orchestrator's per-finding fix verification AND the deduplicated consolidated fresh-eyes review. Keep them as two clearly separated sections of the report (e.g. "Fix verification (R1 findings)" followed by "Fresh-eyes review — Ronda N").

Snapshot directory for subsequent rounds: reuse the same review directory (under `~/.openclaw/workspace/.openclaw/tmp/`), renaming the previous head snapshot (e.g. `head` → `head.r1`) and extracting the new one. Keep previous-round findings files and name new ones `findings-r<N>-<model>.md`. Consolidate only the exact current-round filenames recorded in `pending-state.json`; never glob prior rounds back into the fresh-eyes report.

## Workflow: PR review

1. Identify repo and PR number.
2. Fetch PR metadata:

```bash
gh pr view <N> --repo <owner/repo> --json title,headRefName,headRefOid,baseRefName,baseRefOid,additions,deletions,changedFiles,files \
  -q '{branch: .headRefName, base: .baseRefName, baseCommit: .baseRefOid, commit: .headRefOid, additions: .additions, deletions: .deletions, files: [.files[].path], changedFiles: .changedFiles, title: .title}'
```

3. **Check diff size and decide on test exclusion.** If the PR diff exceeds 2000 lines (additions + deletions), exclude test/spec directories from the reviewer snapshot and add an explicit instruction to ignore tests (see the Test exclusion section below for the exact reviewer block and rationale):
   ```bash
   TOTAL_LINES=$(gh pr view <N> --repo <owner>/<repo> --json additions,deletions -q '.additions + .deletions')
   TEST_DIRS="scannerTests Tests __tests__ test tests spec specs"
   EXCLUDE_TESTS=false
   if [ "$TOTAL_LINES" -gt 2000 ]; then
     EXCLUDE_TESTS=true
   fi
   ```
4. Prepare one shared local PR review snapshot before spawning reviewers:
   - `base/` checkout at the PR base commit or base branch tip
   - `head/` checkout at the PR head commit
   - `PR_DIFF.patch` from `gh pr diff`
   - `PR_METADATA.json` with branch, base, commit, changed files, stats
   - If `EXCLUDE_TESTS=true`, remove test directories from `head/` after checkout:
     ```bash
     cd "$BASE/head"
     for d in scannerTests Tests __tests__ test tests spec specs; do
       find . -type d -name "$d" -exec rm -rf {} + 2>/dev/null || true
     done
     # Also regenerate the diff patch without test files
     git diff origin/<base_branch>...HEAD -- . ':(exclude)**/scannerTests/**' ':(exclude)**/Tests/**' ':(exclude)**/test/**' ':(exclude)**/tests/**' ':(exclude)**/__tests__/**' ':(exclude)**/spec/**' ':(exclude)**/specs/**' > "$BASE/PR_DIFF.patch"
     ```
5. Create the review working directory and assign output file paths per model.
6. Tell subagents to use the local snapshot, write findings to their output file, and **not** clone/fetch unless explicitly necessary.
7. Spawn reviewer subagents with the PR task (include test-exclusion instruction if `EXCLUDE_TESTS=true`).
8. After spawning, **write `pending-state.json`** (with the `childSessionKey` from each `sessions_spawn`, the review dir, expected findings filenames, and a `deadlineMs`) and **launch the babysitter** in background (see the Babysitter section) so a lost completion event or a crashed reviewer can never leave the review stalled forever.
9. **Wake = check ALL reviewers, not one.** Every wake (completion event, babysitter exit, or any user/runtime message arriving while reviewers run) must immediately check for ALL expected findings files on disk (the per-model output files). Any single "reviewer finished" ping does NOT mean only that reviewer finished — the others often finish before or at the same time and their events may be batched, delayed, or suppressed. Never wait for "the remaining" completion events if all files already exist.
   - Do not treat file existence alone as completion: a reviewer may still be writing it. End the wait only when the babysitter writes a terminal `round` state, or after independently confirming every artifact is structurally valid and every corresponding reviewer session is non-running.
   - If some are missing, malformed, or still running: keep working on anything else useful (e.g. fix verification) and wait for the next wake; do not sleep/poll and do not stop the babysitter.
   - Track expected filenames in the pending-watch state file (same as `pending-state.json`) so any wake can verify against the list.
10. Read only the exact current-round findings files listed in `pending-state.json`.
11. Consolidate findings.
12. Post one PR summary comment and inline comments where useful.
13. Report concise result to the user.

### PR local snapshot pattern

Prepare a shared snapshot once, then pass these paths to all reviewers:

```bash
BASE=~/.openclaw/workspace/.openclaw/tmp/<repo>-pr<N>-review
rm -rf "$BASE"
mkdir -p "$BASE"

gh repo clone <owner>/<repo> "$BASE/head" -- --no-checkout
cd "$BASE/head"
# GitHub's pull ref works for same-repo and fork PRs.
git fetch origin <base_branch> "+refs/pull/<N>/head:refs/remotes/origin/pr-<N>" --prune
git checkout <head_commit_sha>

cd "$BASE"
git clone --no-checkout "$BASE/head" base
cd "$BASE/base"
git checkout <base_commit_sha>  # or origin/<base_branch> if baseRefOid is unavailable

cd "$BASE"
gh pr diff <N> --repo <owner>/<repo> > PR_DIFF.patch
cat > PR_METADATA.json <<JSON
{"repo":"<owner>/<repo>","pr":<N>,"base":"<base_branch>","branch":"<head_branch>","baseCommit":"<base_commit_sha>","commit":"<head_commit_sha>","changedFiles":<count>}
JSON
```

The `base/` checkout is for regression comparisons. The `head/` checkout is the PR code as reviewed. `PR_DIFF.patch` identifies the changed hunks.

### PR reviewer task template

```text
IMPORTANT: You are an ANALYSIS-ONLY reviewer. Do NOT post anything to GitHub.
Your job is to read the PR code, find issues, and WRITE your findings to the output file.

OUTPUT FILE: {output_file}

Write ALL your findings to this file using the ===FINDING=== format below.
Do NOT return findings in your response message — write them to the file.
After writing, reply with ONLY: "DONE: wrote N findings to {output_file}"
If you found no issues, write a non-empty file containing exactly `NO FINDINGS`, then reply: `DONE: 0 findings to {output_file}`.

IMPORTANT CONTEXT:
- Repo: {owner}/{repo}
- PR: #{pr_number}
- Branch: {branch}
- Base: {base_branch}
- Head commit SHA: {commit_sha}
- Base commit SHA: {base_commit_sha}
- {stats_summary}
- Use the head commit SHA for line references.

Local review snapshot prepared by the orchestrator:
- Head snapshot: {snapshot_root}/head
- Base snapshot: {snapshot_root}/base
- Diff patch: {snapshot_root}/PR_DIFF.patch
- Metadata: {snapshot_root}/PR_METADATA.json

Do NOT clone or fetch the repo. Use the local snapshots and diff above.
For changed code, read files under `head/`.
For regression comparisons, compare against `base/`.
Use `PR_DIFF.patch` to identify changed files and line context.

Steps:
1. Read `PR_DIFF.patch` to understand the changed hunks.
2. Read changed files from `head/` and, when needed, compare to `base/`.
3. Analyze for the requested categories.
4. Apply the reviewer discipline rules from this skill: read surrounding context, avoid duplicates, use concrete traces for logic claims, and keep only high-signal findings.
5. Write ALL findings to {output_file} in the exact ===FINDING=== format.

Findings format:
===FINDING===
severity: [Critical|High|Medium|Low]
title: <one line title>
file: <path>
line: <line_number>
reasoning: <what the code does, why it's wrong, what triggers it>
fix: <concrete fix suggestion>
trace: <concrete trace for logic claims, or N/A>
===END_FINDING===

End the file with a short quality summary. Zero findings: write ONLY `NO FINDINGS` and stop — do not add a summary.

{include any user-specific exclusions, e.g. no security/hardening}
{test_exclusion_instruction_if_applicable}

Model identity: {model_identity}
```

### PR posting pattern

Important GitHub collision rules:

- Subagents never post anything.
- The orchestrator must not create pending review drafts with `pulls/{n}/reviews` unless using a final explicit event. Pending drafts collide because all reviewers use the same GitHub identity.
- Prefer standalone inline review comments via `POST /repos/{owner}/{repo}/pulls/{pull_number}/comments`; each creates its own resolvable thread without draft state.
- Do not delete, edit, resolve, or dismiss other reviews/comments unless explicitly asked.

Summary:

```bash
gh pr comment <N> --repo <owner/repo> --body "<consolidated-summary>"
```

Inline comments:

```bash
COMMENT_FILE="$HOME/.openclaw/workspace/.openclaw/tmp/comment.md"
gh api /repos/{owner}/{repo}/pulls/{pr_number}/comments \
  --method POST \
  -F body=@"$COMMENT_FILE" \
  -f path="src/file.ts" \
  -f line=42 \
  -f side="RIGHT" \
  -f commit_id="<commit_sha>"
```

Write the full Markdown body to `COMMENT_FILE` first; `-F body=@"$COMMENT_FILE"` reads it correctly. Keep the severity tag as the first visible token.

## Workflow: non-PR scope review → Markdown

1. **Always fetch/pull before spawning reviewers** unless the user explicitly names a commit/branch/tag or says not to. Assume the user wants the current version.
2. Record the exact branch and commit SHA reviewed.
3. Prefer a full clean clone/snapshot under `~/.openclaw/workspace/.openclaw/tmp/<repo>-<scope>-review/` and pass reviewers the scope path inside that clone. Subagent file tools cannot read `/tmp`. Avoid hand-copying subsets unless the repo is huge or the user requests it; incomplete snapshots cause false positives.
4. If excluding experimental/archive/example folders, state that explicitly in the reviewer task.
5. Create the review working directory and assign output file paths per model.
6. Spawn the three reviewer subagents with a scope-specific task.
7. Write `pending-state.json`, launch the babysitter, and use the same wake handling as the PR variant. Scope reviews have the same lost-event and crashed-reviewer risks.
8. Read only the exact current-round findings filenames recorded in `pending-state.json`.
9. Consolidate and deduplicate into a Markdown review.
10. Publish the Markdown file via GitHub contents API (or local commit if working in a clone) at the scope root.
11. Report the URL and top findings.

### Snapshot pattern

For GitHub repos, prefer a full clean clone or refreshed clone:

```bash
BASE="$HOME/.openclaw/workspace/.openclaw/tmp/<repo>-<scope>-review"
mkdir -p "$(dirname "$BASE")"
if [ -d "$BASE/head/.git" ]; then
  cd "$BASE/head"
  git fetch origin main --prune
  git checkout main
  git pull --ff-only origin main
else
  git clone --depth 1 https://github.com/<owner>/<repo>.git "$BASE/head"
  cd "$BASE/head"
fi
COMMIT=$(git rev-parse HEAD)
```

Use the clone itself as the review snapshot when practical. Point subagents at the relevant scope path, e.g. `$BASE/head/<scope>`.

Only create a reduced copy when the repo is too large or when explicitly needed. If using a reduced copy, include adjacent context required for imports/contracts and verify obvious file-existence claims against the full repo before publishing.

### Non-PR reviewer task template

```text
IMPORTANT: You are an ANALYSIS-ONLY reviewer. Do NOT post anything to GitHub.

OUTPUT FILE: {output_file}

Write ALL your findings to this file using the ===FINDING=== format below.
Do NOT return findings in your response message — write them to the file.
After writing, reply with ONLY: "DONE: wrote N findings to {output_file}"
If you found no issues, write a non-empty file containing exactly `NO FINDINGS`, then reply: `DONE: 0 findings to {output_file}`.

Review target: {owner}/{repo}, branch {branch}, commit {commit_sha}.
Scope: {scope_description}. Local snapshot: {snapshot_path} (findings still go to the review directory above)
Adjacent context included: {context_files_or_dirs}

USER REQUEST: {brief review theme}. {security_exclusion_if_any}

Focus areas:
- {scope-specific bullets}

Apply reviewer discipline: read surrounding/calling code before flagging, avoid duplicates, include concrete traces for logic claims, downgrade uncertain logic claims to Low, and keep only high-signal findings.

Write high-signal findings only to {output_file} in EXACTLY this format:
===FINDING===
severity: [Critical|High|Medium|Low]
title: <one line title>
file: <path>
line: <line_number>
reasoning: <what the code does, why it's wrong, what triggers it>
fix: <concrete fix suggestion>
trace: <concrete trace for logic claims, or N/A>
===END_FINDING===

End the file with a short quality summary. Zero findings: write ONLY `NO FINDINGS` and stop — do not add a summary.
Model identity: {model_identity}
```

### Markdown publishing pattern

Write the consolidated review to `$REVIEW_DIR/REVIEW-CONSOLIDATED.md` inside the round's review directory (agent file tools are workspace-sandboxed and may not see `/tmp`; use `/tmp` only in explicitly non-sandboxed environments), then publish:

```bash
PATH_IN_REPO="<scope>/REVIEW.md"
SHA=$(gh api "/repos/<owner>/<repo>/contents/$PATH_IN_REPO" --jq '.sha' 2>/dev/null || true)
if [ -n "$SHA" ]; then
  gh api "/repos/<owner>/<repo>/contents/$PATH_IN_REPO" \
    --method PUT \
    -f message="docs: add/update <scope> multi-review" \
    -f content="$(base64 < "$REVIEW_DIR/REVIEW-CONSOLIDATED.md" | tr -d '\n')" \
    -f sha="$SHA" \
    -f branch="main" \
    --jq '.content.html_url'
else
  gh api "/repos/<owner>/<repo>/contents/$PATH_IN_REPO" \
    --method PUT \
    -f message="docs: add <scope> multi-review" \
    -f content="$(base64 < "$REVIEW_DIR/REVIEW-CONSOLIDATED.md" | tr -d '\n')" \
    -f branch="main" \
    --jq '.content.html_url'
fi
```

## Spawning pattern

Use `sessions_spawn` once per reviewer. Parallel spawning is fine. Each reviewer gets a unique output file path.

```json
{
  "runtime": "subagent",
  "model": "openai/gpt-5.6-terra",
  "cleanup": "keep",
  "label": "<scope>-review-terra",
  "task": "<reviewer task with OUTPUT_FILE: <review-dir>/findings-terra.md>"
}
```

Label pattern:
- `<scope>-review-terra`
- `<scope>-review-glm`
- `<scope>-review-deepseek`

Output file pattern (in the review directory, one per model):
- `<review-dir>/findings-terra.md`
- `<review-dir>/findings-glm.md`
- `<review-dir>/findings-deepseek.md`

Keep labels short but descriptive enough to identify the scope and model.

## Test exclusion (large PRs)

When `EXCLUDE_TESTS=true` (diff > 2000 lines), apply it in three places:

1. **Remove the test dirs from `head/`** and regenerate `PR_DIFF.patch` without them (see workflow step 4).
2. **Add this block to every reviewer task** after the findings format section:

```text
EXCLUDE test/spec files from your review. Do NOT read or analyze files under
scannerTests/, Tests/, test/, tests/, __tests__/, spec/, or specs/.
Concentrate on production code only.
```

**Rationale:** Across 9 reviewer-runs (3 models × 3 rounds) on PR #14, test files produced 0 findings while consuming ~40% of the context budget (~2500 lines of test code). Excluding them on large PRs saves tokens, reduces latency, and focuses reviewers on production code where bugs actually live.

## Consolidation rules

1. Read only the exact current-round findings files listed in `pending-state.json`.
2. Extract all `===FINDING===` blocks from each file.
3. Drop security/hardening findings if excluded by user.
4. Drop weak findings that are explicitly speculative, disproven by inspection, or caused by an incomplete snapshot.
5. Deduplicate by file + issue theme.
6. Preserve source attribution while deduplicating:
   - Every published finding MUST end its heading with `(N/M reviewers: model-a[, model-b...])`, where M is the declared panel size and N is the number of current-round reviewer artifacts that independently support it.
   - Use the real responding model names declared in the panel, not output-file aliases.
   - Single source example: `(1/3 reviewers: gpt-5.6-terra)`.
   - Multiple sources example: `(2/3 reviewers: gpt-5.6-terra, glm-5.3)`.
   - Keep the highest severity, merge the strongest reasoning and fix, and list every supporting reviewer.
   - Orchestrator verification may be stated separately but does not increase N.
7. Sort by severity: Critical, High, Medium, Low.
8. Prefer 8-20 high-signal findings over long low-value lists.
9. Include a recommended fix order.
10. Add a short note listing timed-out/no-useful-result reviewers.

## False-positive handling

Before publishing, verify obvious build/startup claims when cheap:
- file existence (`test -e`, `find`),
- command references (`grep`),
- config/compose snippets,
- package entrypoints (`__main__.py`, `pyproject`, etc.).

If a finding came from an incomplete snapshot or is false after verification, omit it and mention only if useful.

## Timeout / weak result policy

- If a reviewer times out with no findings file written, do not wait indefinitely.
- An empty or missing output file is a reviewer failure. A successful zero-finding review must contain the `NO FINDINGS` sentinel.
- If at least 2 strong reviewers completed and findings converge, consolidate.
- If only 1 reviewer completed for a large scope, consider relaunching timed-out reviewers with a narrower task.
- If a late reviewer returns after publication, reply `NO_REPLY` unless it adds a genuinely important new finding; then update the Markdown.

## Babysitter (completion watchdog)

Reviewer completion events can be lost. The babysitter is a detached service that monitors sessions and disk artifacts, persists runtime state separately from operator configuration, and explicitly wakes the orchestrator only at a terminal round state.

**Script:** `~/.openclaw/workspace/skills/multireview/scripts/review-babysitter.py`

### State files

The orchestrator writes immutable/replace-only `pending-state.json`:

- `reviewDir`: absolute path under the workspace.
- `deadlineMs`: absolute deadline.
- `orchestratorSessionKey`: full main-session key to wake.
- `models`: `{model, sessionKey, file}` entries using the exact `childSessionKey` values from `sessions_spawn`.

The babysitter never rewrites that configuration. It writes `pending-state.runtime.json` atomically with PID, observations, per-model statuses, losses, terminal round state, and wake result. To extend a deadline or replace a reviewer, atomically replace `pending-state.json`; the next poll reloads it. This separation prevents a stale watchdog write from clobbering operator changes.

### Durable launch

Launch it outside the executor/turn lifecycle. On Linux with a systemd user manager (preferred and tested):

```bash
STATE="$HOME/.openclaw/workspace/.openclaw/tmp/<review>/pending-state.json"
UNIT="multireview-babysitter-$(printf '%s' "$STATE" | sha256sum | cut -c1-12)"
OPENCLAW_BIN="$(command -v openclaw)"
systemd-run --user --unit="$UNIT" --collect \
  --property=Restart=on-failure --property=RestartSec=30 \
  --setenv="OPENCLAW_BIN=$OPENCLAW_BIN" --setenv=PYTHONUNBUFFERED=1 \
  python3 "$HOME/.openclaw/workspace/skills/multireview/scripts/review-babysitter.py" "$STATE"
systemctl --user is-active "$UNIT"
```

Do not run it as a long foreground `exec` command: that process belongs to the executor and can die at a turn/model/session boundary. `nohup ... &` is only a fallback when no user manager exists and must be verified after a later turn. Inspect the canonical service log with `journalctl --user -u "$UNIT"`.

### State machine and artifact contract

A usable artifact is either exactly `NO FINDINGS` or one or more complete `===FINDING===` / `===END_FINDING===` blocks. Arbitrary text, empty files, and truncated blocks are invalid.

Per-model states:

- `done`: non-running/pruned session plus valid artifact.
- `lost`: present non-running session (`done`, `failed`, `killed`, `aborted`, null, or any future non-`running` status) without a valid artifact.
- `active`: running with recent activity.
- `hung`: stale running session without a stable valid artifact.
- `stalled-with-artifact`: stale running session whose valid artifact is unchanged across two polls; terminal but explicitly recorded.
- `unknown`: session absent and no valid artifact; blocks until deadline.

A transcript `DONE: 0 findings` is diagnostic only. It never substitutes for the mandatory `NO FINDINGS` artifact.

Terminal round states:

- `complete`: every reviewer produced a usable artifact.
- `complete-with-losses`: all reviewers are terminal but one or more are `lost`.
- `deadline`: deadline reached with unresolved `active`, `hung`, or `unknown`.
- `monitor-error`: three consecutive session-query or state-I/O failures.

### Explicit wake and recovery

Because the service is detached, `exit(0)` cannot wake a completed exec call. At a terminal round state the script runs:

```bash
openclaw system event --mode now \
  --session-key "<orchestratorSessionKey>" \
  --text "Multireview babysitter <round>. Read <runtime-state-path>"
```

Wake delivery is retried three times and recorded in the runtime state. Wake failure returns non-zero so systemd restarts the service and retries. On every wake, the orchestrator reads both configuration and runtime state, validates the exact current-round artifacts, and reports missing/lost reviewers.

The script holds a single-instance lock for the review. A second babysitter exits without modifying state. Runtime writes use PID-unique temporary files plus `os.replace`. Configuration/state read errors use a bounded consecutive-error policy rather than crashing silently.

To stop early after independent terminal verification, use `systemctl --user stop "$UNIT"`. Do not kill by an unverified PID.

## Debugging reviewer subagents

Audit what the orchestrator sent to a reviewer or why a reviewer behaved oddly using the OpenClaw CLI directly (no external scripts required):

```bash
# List recent sessions incl. subagents (find reviewer child sessions and their state)
openclaw sessions --json --limit all | jq '.sessions[] | select(.key | contains("subagent"))'

# Inspect a specific session transcript (last assistant diagnostic)
tail -c 262144 ~/.openclaw/agents/main/sessions/<session-id>.jsonl   | jq -r 'select(.type=="message") | select(.message.role=="assistant") | .message.content' | tail -20
```

# Check if a reviewer wrote its output file
ls -la $HOME/.openclaw/workspace/.openclaw/tmp/<scope>-pr<N>-review/findings-*.md
cat $HOME/.openclaw/workspace/.openclaw/tmp/<scope>-pr<N>-review/findings-<model>.md
```

Quick recent-subagent listing:

```bash
openclaw sessions --json | python3 -c '
import json,sys
d=json.load(sys.stdin)
for s in (d.get("sessions", d) if isinstance(d, dict) else d):
    if "subag" in s.get("key", "") and s.get("ageMs", 999999999) < 86400000:
        age_h=s.get("ageMs", 0)/3600000
        print(s["sessionId"], s.get("model", "?"), f"{age_h:.1f}h ago")
' | head -10
```

This is useful for checking the exact reviewer prompt, tool calls, file reads, command output, and whether a bad finding came from an incomplete task, missing context, or reviewer error.

## Continuous refinement

After each real review batch, update this skill when a pattern changes:
- new default reviewer models,
- better task wording,
- recurring false positives,
- better output paths,
- new scope templates,
- improved timeout or consolidation policy.

Keep this file concise and procedural. Prefer concrete patterns from successful reviews over abstract advice.
