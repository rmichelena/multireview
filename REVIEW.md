# Multireview — Round 5 Consolidated Review (rmichelena/multireview @ 806dfb1)

> Panel: 3/3 reviewers — **gpt-5.6-terra**, **glm-5.3**, **deepseek-v4.1-flash** (fresh-eyes, analysis-only, REVIEW.md ignored).
> 15 raw findings → **14 unique: 1 High · 4 Medium · 9 Low**.

## High

### H1. Stale terminal runtime file makes every later round's babysitter exit without monitoring (1/3 reviewers: deepseek-v4.1-flash)
`scripts/review-babysitter.py:447` — the r4 restart-continuity feature (seed runtime from disk; if terminal, retry wake and exit) is not keyed to the current round: the skill reuses the same review directory (and thus the same `pending-state.runtime.json`) across rounds, so round N+1's fresh babysitter reads round N's terminal record, re-wakes, and exits. **Reproduced by the reviewer.** Introduced by the r4 M3 fix. **Fix:** persist a config fingerprint (hash of models[].sessionKey + deadlineMs + reviewDir) in the runtime and only treat a terminal record as a restart when the fingerprint matches; otherwise start monitoring fresh.

## Medium

### M1. Session-query fallback treats "unqueryable" as "absent" and can complete a round while a reviewer is still running (2/3 reviewers: gpt-5.6-terra, deepseek-v4.1-flash)
`scripts/review-babysitter.py:509` — on sessions-query errors the watchdog feeds `sessions={}` into `evaluate_round`; after two failed queries (30s apart) a valid stable artifact marks the reviewer `done` via the absent-session gate — even though it may still be writing. The short 30s retry interval also shrinks the two-poll margin from 300s to 60s. **Fix:** on query failure preserve each model's prior status (do NOT set `sessionAbsent`); require N confirmations at the normal interval, or an atomic-rename artifact protocol, before artifact-only completion.

### M2. Transient/absent pending-state.json on the first poll causes a permanent silent stop with no wake (1/3 reviewers: glm-5.3)
`scripts/review-babysitter.py:480` — FileNotFoundError on the very first poll maps to `stopped-config-gone` (exit 0, no wake, no restart), indistinguishable from a post-publication cleanup. A write/start ordering hiccup silently kills the watchdog guarantee. **Fix:** treat ENOENT as `stopped-config-gone` only after at least one successful load in this process; before that, count it under the bounded config-error policy (which can still fire the no-key/terminal wake).

### M3. Any non-`running` status — including a missing one — instantly terminates a reviewer as done/lost (1/3 reviewers: glm-5.3)
`scripts/review-babysitter.py:243` — `status != "running"` is treated as terminal, so an absent `status` field or a future transient value (`queued`, `starting`, `retrying`) flips a possibly-live reviewer to `lost` on the first poll. **Fix:** whitelist known-terminal statuses; unknown/missing → non-terminal `unknown` with the raw status recorded in runtime.

### M4. Sessions query enumerates unbounded history each poll; empty listing accepted as "all absent" (1/3 reviewers: deepseek-v4.1-flash)
`scripts/review-babysitter.py:138` — `--limit all` over a 462-session store grows toward the 60s timeout, pushing the watchdog into the M1 fallback; and `sessions: []` passes the r4 malformed-envelope guard, feeding "all absent". **Fix:** query only the configured sessionKeys; treat an empty listing (that omits known-live keys) as an error sentinel.

## Low

### L1. Fenced code block quoting the format invalidates the whole artifact (1/3 reviewers: glm-5.3) — `scripts/review-babysitter.py:124`
A summary that re-quotes the finding template inside a ``` fence trips the stray-marker/pseudo-block heuristics and the entire review is discarded. **Fix:** strip fenced regions before the stray-marker scan.

### L2. Invalid numeric env vars crash at import into an unlogged restart loop (1/3 reviewers: glm-5.3) — `scripts/review-babysitter.py:17`
`int()` on `POLL_INTERVAL`/`STALL_THRESHOLD` at import dies before any error policy exists → deterministic 30s systemd loop. **Fix:** defensive parse with fallback/warning, or validate in `main()` and exit 0 like other invocation errors.

### L3. Agent-id fallback silently scopes the query to the wrong agent (1/3 reviewers: glm-5.3) — `scripts/review-babysitter.py:78`
Non-`agent:`-shaped orchestrator keys fall back to `AGENT_ID` with no signal; reviewers under another agent become permanently `unknown`. **Fix:** derive agent ids from each model entry's own sessionKey, or record `agentIdFallback: true` in runtime.

### L4. Placeholder detector rejects legitimate angle-bracket values and drops the whole reviewer (1/3 reviewers: deepseek-v4.1-flash) — `scripts/review-babysitter.py:23`
`^<.*>$` rejects a genuine `title: <script>` finding; one invalid block invalidates the file → reviewer `lost`. **Fix:** match the exact template placeholder strings instead of all `<...>`.

### L5. Stalled gate needs three equal transcript samples, docs say two (1/3 reviewers: deepseek-v4.1-flash) — `scripts/review-babysitter.py:263`
`prev_transcript == transcript` plus `previous.stale` (itself transcript-derived) sums to three polls. Safe but slower than documented. **Fix:** relax the gate or update SKILL.md.

### L6. SKILL.md undercounts exit-0 paths and mislabels the wake text placeholder (1/3 reviewers: deepseek-v4.1-flash) — `SKILL.md:538`
Docs say "two deliberate exit-0 paths"; there are five. Wake template shows `<round>` but the code substitutes the terminal state name. **Fix:** enumerate all paths; fix the wake-text doc.

### L7. reviewDir not required absolute — resolves against systemd's CWD (1/3 reviewers: deepseek-v4.1-flash) — `scripts/review-babysitter.py:384`
A relative reviewDir silently reads the wrong tree under `systemd-run` (CWD=$HOME) → spurious losses. **Fix:** reject non-absolute reviewDir in validate_config.

### L8. Wake retry uses non-injectable `time.sleep(5)`, slowing the suite ~30-40s per run (1/3 reviewers: deepseek-v4.1-flash) — `scripts/review-babysitter.py:366`
**Fix:** parameterize the retry delay; tests pass 0.

### L9. Comment claims error counters survive restart; only model observations are seeded (1/3 reviewers: deepseek-v4.1-flash) — `scripts/review-babysitter.py:440`
**Fix:** correct the comment.

---

## Nota de proceso
- Snapshot: commit `806dfb1` (post-fix ronda 4), revisión fresh-eyes sin acceso a reviews previas.
- Consolidación por este orquestador (acuerdo del orquestador no suma revisores).
- Archivos brutos: `findings-r5-terra.md` (1), `findings-r5-glm.md` (5), `findings-r5-deepseek.md` (9).
- H1 y M1 fueron **reproducidos empíricamente** por el revisor (probes contra el código real).
- El H1 es una consecuencia no deseada del fix M3 de ronda 4 (continuidad de runtime) — hallazgo legítimo de regresión arquitectural, no de implementación.
