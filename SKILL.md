---
name: multireview
description: Run consolidated multi-model code reviews on GitHub repositories. Supports two variants: PR reviews posted back to GitHub as a summary plus inline comments, and non-PR scope reviews delivered as a consolidated Markdown file in the repo. Use when asked for multi-review, PR review, code review with multiple models, fresh eyes, review a folder/subtree, or publish a review document.
---

# Multi-Review (Consolidated Reviewers)

Run several independent reviewer subagents, then the orchestrator consolidates, deduplicates, and publishes one final review.

**Orchestrator:** main agent, currently GPT-5.5 (`openai-codex/gpt-5.5`).  
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

Use these five unless the user asks otherwise or a model is unavailable:

1. `openai-codex/gpt-5.5` — GPT-5.5
2. `fireworks/accounts/fireworks/models/deepseek-v4-pro` — DeepSeek V4 Pro
3. `fireworks/accounts/fireworks/models/kimi-k2p6` — Kimi K2.6
4. `fireworks/accounts/fireworks/models/qwen3p6-plus` — Qwen 3.6 Plus
5. `fireworks/accounts/fireworks/models/glm-5p1` — GLM-5.1

Default timeout: `runTimeoutSeconds: 900` for large reviews, `600` for small PRs. Use `cleanup: "keep"`.

## Common reviewer task contract

Every subagent task must include:

```text
IMPORTANT: You are an ANALYSIS-ONLY reviewer. Do NOT post anything to GitHub.
```

And require findings in this exact format:

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

Tell reviewers to end with a short quality summary.

## Reviewer discipline

Add these rules to reviewer tasks when the review is bug-finding oriented:

- Surface only impactful findings; skip trivial style issues covered by linters/formatters.
- Read surrounding files and called/calling code before flagging; do not rely only on a diff hunk.
- State assumptions explicitly when a claim depends on code outside the reviewed snippet.
- Do not flag the same issue twice; choose the most actionable location.
- If a bug is really a preference/improvement, mark it Low and frame it as a suggestion.
- For any logic claim (sort comparator, regex, off-by-one, async ordering, boolean condition), include a concrete trace. If no concrete trace can be built, downgrade to Low with “Consider verifying...”.
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

## Workflow: PR review

1. Identify repo and PR number.
2. Fetch PR metadata:

```bash
gh pr view <N> --repo <owner/repo> --json title,headRefName,headRefOid,additions,deletions,changedFiles \
  -q '{branch: .headRefName, commit: .headRefOid, additions: .additions, deletions: .deletions, files: .changedFiles, title: .title}'
```

3. Spawn reviewer subagents with the PR task.
4. Wait for completion events. Do not poll in loops.
5. Consolidate findings.
6. Post one PR summary comment and inline comments where useful.
7. Report concise result to the user.

### PR reviewer task template

```text
IMPORTANT: You are an ANALYSIS-ONLY reviewer. Do NOT post anything to GitHub.
Your job is to read the PR code, find issues, and RETURN your findings as structured text.

IMPORTANT CONTEXT:
- Repo: {owner}/{repo}
- PR: #{pr_number}
- Branch: {branch}
- Head commit SHA: {commit_sha}
- {stats_summary}
- Use this commit SHA for line references.

Steps:
1. Run: gh pr diff {pr_number} --repo {owner}/{repo}
2. Read files referenced in the diff for full context.
3. Analyze for the requested categories.
4. Apply the reviewer discipline rules from this skill: read surrounding context, avoid duplicates, use concrete traces for logic claims, and keep only high-signal findings.
5. Return findings in the exact ===FINDING=== format.

{include any user-specific exclusions, e.g. no security/hardening}

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
gh api /repos/{owner}/{repo}/pulls/{pr_number}/comments \
  --method POST \
  -f body="[Severity] **Title**\n\nReasoning...\n\n**Fix:** ..." \
  -f path="src/file.ts" \
  -f line=42 \
  -f side="RIGHT" \
  -f commit_id="<commit_sha>"
```

Use `-f body-file=/tmp/comment.md` for long bodies. Keep the severity tag as the first visible token in each inline comment body.

## Workflow: non-PR scope review → Markdown

1. **Always fetch/pull before spawning reviewers** unless the user explicitly names a commit/branch/tag or says not to. Assume the user wants the current version.
2. Record the exact branch and commit SHA reviewed.
3. Prefer a full clean clone/snapshot under `/tmp/<repo>-<scope>-review` and pass reviewers the scope path inside that clone. Avoid hand-copying subsets unless the repo is huge or the user requests it; incomplete snapshots cause false positives.
4. If excluding experimental/archive/example folders, state that explicitly in the reviewer task.
5. Spawn the five reviewer subagents with a scope-specific task.
6. Wait for completion events. If one model times out or returns no useful findings, proceed with the useful reviewers when coverage is sufficient.
7. Consolidate and deduplicate into a Markdown review.
8. Publish the Markdown file via GitHub contents API (or local commit if working in a clone) at the scope root.
9. Report the URL and top findings.

### Snapshot pattern

For GitHub repos, prefer a clean clone or refreshed clone:

```bash
cd /tmp
if [ -d /tmp/<repo>/.git ]; then
  cd /tmp/<repo>
  git fetch origin main --prune
  git checkout main
  git pull --ff-only origin main
else
  git clone --depth 1 https://github.com/<owner>/<repo>.git /tmp/<repo>
  cd /tmp/<repo>
fi
COMMIT=$(git rev-parse HEAD)
```

Use the clone itself as the review snapshot when practical. Point subagents at the relevant scope path, e.g. `/tmp/<repo>/<scope>`.

Only create a reduced copy when the repo is too large or when explicitly needed. If using a reduced copy, include adjacent context required for imports/contracts and verify obvious file-existence claims against the full repo before publishing.

### Non-PR reviewer task template

```text
IMPORTANT: You are an ANALYSIS-ONLY reviewer. Do NOT post anything to GitHub.

Review target: {owner}/{repo}, branch {branch}, commit {commit_sha}.
Scope: {scope_description}. Local snapshot: {snapshot_path}
Adjacent context included: {context_files_or_dirs}

USER REQUEST: {brief review theme}. {security_exclusion_if_any}

Focus areas:
- {scope-specific bullets}

Apply reviewer discipline: read surrounding/calling code before flagging, avoid duplicates, include concrete traces for logic claims, downgrade uncertain logic claims to Low, and keep only high-signal findings.

Return high-signal findings only in EXACTLY this format:
===FINDING===
severity: [Critical|High|Medium|Low]
title: <one line title>
file: <path>
line: <line_number>
reasoning: <what the code does, why it's wrong, what triggers it>
fix: <concrete fix suggestion>
trace: <concrete trace for logic claims, or N/A>
===END_FINDING===

End with a short quality summary.
Model identity: {model_identity}
```

### Markdown publishing pattern

Write the consolidated review to `/tmp/<review>.md`, then publish:

```bash
PATH_IN_REPO="<scope>/REVIEW.md"
SHA=$(gh api "/repos/<owner>/<repo>/contents/$PATH_IN_REPO" --jq '.sha' 2>/dev/null || true)
if [ -n "$SHA" ]; then
  gh api "/repos/<owner>/<repo>/contents/$PATH_IN_REPO" \
    --method PUT \
    -f message="docs: add/update <scope> multi-review" \
    -f content="$(base64 -w0 /tmp/<review>.md)" \
    -f sha="$SHA" \
    -f branch="main" \
    --jq '.content.html_url'
else
  gh api "/repos/<owner>/<repo>/contents/$PATH_IN_REPO" \
    --method PUT \
    -f message="docs: add <scope> multi-review" \
    -f content="$(base64 -w0 /tmp/<review>.md)" \
    -f branch="main" \
    --jq '.content.html_url'
fi
```

## Spawning pattern

Use `sessions_spawn` once per reviewer. Parallel spawning is fine.

```json
{
  "runtime": "subagent",
  "model": "openai-codex/gpt-5.5",
  "runTimeoutSeconds": 900,
  "cleanup": "keep",
  "label": "<scope>-review-gpt55",
  "task": "<reviewer task>"
}
```

Label pattern:
- `<scope>-review-gpt55`
- `<scope>-review-deepseek`
- `<scope>-review-kimi`
- `<scope>-review-qwen`
- `<scope>-review-glm`

Keep labels short but descriptive enough to identify the scope and model.

## Consolidation rules

1. Extract all `===FINDING===` blocks.
2. Drop security/hardening findings if excluded by user.
3. Drop weak findings that are explicitly speculative, disproven by inspection, or caused by an incomplete snapshot.
4. Deduplicate by file + issue theme.
5. If multiple reviewers flag the same issue:
   - keep highest severity,
   - merge the strongest reasoning,
   - keep the most actionable fix,
   - note reviewers in the consolidated Markdown.
6. Sort by severity: Critical, High, Medium, Low.
7. Prefer 8-20 high-signal findings over long low-value lists.
8. Include a recommended fix order.
9. Add a short note listing timed-out/no-useful-result reviewers.

## False-positive handling

Before publishing, verify obvious build/startup claims when cheap:
- file existence (`test -e`, `find`),
- command references (`grep`),
- config/compose snippets,
- package entrypoints (`__main__.py`, `pyproject`, etc.).

If a finding came from an incomplete snapshot or is false after verification, omit it and mention only if useful.

## Timeout / weak result policy

- If a reviewer times out with no findings, do not wait indefinitely.
- If at least 3 strong reviewers completed and findings converge, consolidate.
- If only 1-2 reviewers completed for a large scope, consider relaunching timed-out reviewers with a narrower task.
- If a late reviewer returns after publication, reply `NO_REPLY` unless it adds a genuinely important new finding; then update the Markdown.

## Debugging reviewer subagents

Use these workspace scripts when you need to audit what the orchestrator sent to a reviewer or why a reviewer behaved oddly:

```bash
# Map/list sessions and find recent subagents
python3 ~/.openclaw/workspace/scripts/sessions-map.py

# View a specific subagent transcript by session id
python3 ~/.openclaw/workspace/scripts/subagent-view.py <session-id>

# Only user/assistant messages, no tool chatter
python3 ~/.openclaw/workspace/scripts/subagent-view.py <session-id> --messages

# Full untruncated content
python3 ~/.openclaw/workspace/scripts/subagent-view.py <session-id> --full
```

Quick recent-subagent listing:

```bash
openclaw sessions --json | python3 -c '
import json,sys
d=json.load(sys.stdin)
for s in (d.get("sessions", d) if isinstance(d, dict) else d):
    if "subag" in s.get("key", "") and s.get("ageMs", 999999999) < 86400000:
        print(s["sessionId"], s.get("model", "?"), f"{s.get("ageMs", 0)/3600000:.1f}h ago")
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
