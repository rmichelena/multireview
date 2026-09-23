# Multireview — Round 4 Consolidated Review (rmichelena/multireview @ 9ce9950)

> Panel: 3/3 reviewers — **gpt-5.6-terra**, **glm-5.3**, **deepseek-v4.1-flash** (fresh-eyes, analysis-only, REVIEW.md ignored).
> 12 raw findings → **11 unique: 2 High · 5 Medium · 4 Low**.

## High

### H1. Duplicate-instance lock exit 3 causes an unbounded systemd restart loop (1/3 reviewers: glm-5.3)
`scripts/review-babysitter.py:391` — a second babysitter for the same state path exits 3 on lock contention; under the documented `Restart=on-failure`/`RestartSec=30` unit this restart-loops forever (30s spacing never trips systemd start limits). **Fix:** return 0 when the lock is already held (a running peer is steady state); reserve non-zero for restartable conditions (wake failure).

### H2. Frozen liveness timestamps can terminalize a still-running reviewer as stalled-with-artifact (1/3 reviewers: deepseek-v4.1-flash)
`scripts/review-babysitter.py:227` — session-store timestamps (`lastInteractionAt`/`updatedAt`) only advance at turn boundaries (verified live against a running reviewer); after `STALL_THRESHOLD` (900s) a long-running reviewer looks stale, and with an unchanged valid artifact it reaches the terminal `stalled-with-artifact` → false `complete` + wake while still working. **Fix:** require the session to leave `running` (or transcript-growth evidence) before terminalizing; make `STALL_THRESHOLD` strictly greater than the configured subagent timeout; regression test for frozen-but-advancing sessions.

## Medium

### M1. Empty `models: []` panel is accepted and reported as a successful review (1/3 reviewers: gpt-5.6-terra)
`scripts/review-babysitter.py:339` — `validate_config` doesn't require a non-empty `models`; `all([])` is true, so a zero-member round is marked `complete` and wakes with zero artifacts. **Fix:** reject empty `models` in `validate_config` + regression test.

### M2. Absent-session `done` lacks a two-poll absence gate (1/3 reviewers: glm-5.3)
`scripts/review-babysitter.py:213` — the terminal path checks artifact stability across polls but not session *absence* across polls; a transiently partial sessions listing (no error envelope) can shortcut a running reviewer to `done`. **Fix:** persist `sessionAbsent` per poll and require it on two consecutive polls; test "running in poll N, absent in N+1".

### M3. Restart discards all runtime history: observations, counters, wake state (2/3 reviewers: glm-5.3, deepseek-v4.1-flash)
`scripts/review-babysitter.py:393` — `main_impl` rebuilds runtime from scratch each start (and never reads the persisted file): two-poll stability history is lost, error counters reset (alternating failures never reach the 3-consecutive bound), and terminal/wake records the orchestrator is told to read get overwritten. **Fix:** seed runtime from `pending-state.runtime.json` at startup (preserve `models` observations, update pid); regression test running `main_impl` twice on the same state.

### M4. Session-query errors skip artifact evaluation; completed reviews get mislabeled `monitor-error` (1/3 reviewers: glm-5.3)
`scripts/review-babysitter.py:429` — on `__error__` the loop sleeps the full 300s without evaluating; disk-only terminality is blocked and after ~15 min the round ends as `monitor-error` even with every artifact complete and stable. **Fix:** artifact-only evaluation fallback on session-query failure (and/or shorten the session-error sleep like the config path); record the fallback in runtime.

### M5. `query_sessions` silently returns `{}` for malformed JSON payloads, defeating the `__error__` guard (1/3 reviewers: deepseek-v4.1-flash)
`scripts/review-babysitter.py:130` — `payload.get("sessions", payload)` on an object without a `sessions` array iterates dict keys → empty mapping, no error sentinel → reviewer sessions appear "absent". **Fix:** require a list; `raise RuntimeError` on unexpected shape so the bounded-error path fires.

## Low

### L1. Validator accepts template placeholder values (1/3 reviewers: glm-5.3)
`scripts/review-babysitter.py:105` — a reviewer parroting the SKILL.md template (`title: <one line title>`, …) with only a real severity passes validation and pollutes consolidation. **Fix:** reject values matching `^<.*>$`.

### L2. `write_errors` is not a consecutive-error counter (1/3 reviewers: deepseek-v4.1-flash)
`scripts/review-babysitter.py:448` — reset only on the main-path save; config/session branches that succeed never reset it, so three scattered failures trip `monitor-error`. **Fix:** reset after any successful `safe_save()` on every branch.

### L3. Dead duplicate `SESSIONS_DIR` constant (1/3 reviewers: deepseek-v4.1-flash)
`scripts/review-babysitter.py:18` — unused; `sessions_dir_for()` recomputes the same value. **Fix:** remove it or route `sessions_dir_for` through it (single source of truth for the env override).

### L4. Documentation diverges from code (1/3 reviewers: deepseek-v4.1-flash)
`SKILL.md:553` — terminal-state list omits `stopped-config-gone` (and its no-wake semantics); README claims Hermes compatibility for a workflow whose watchdog is OpenClaw-CLI + systemd only; debugging snippet omits `--agent <id>`. **Fix:** document `stopped-config-gone`, scope the Hermes claim, add `--agent` to the debug example.

---

## Nota de proceso
- Snapshot: commit `9ce9950` (post-fix ronda 3), revisión fresh-eyes sin acceso a reviews previas.
- Consolidación por este orquestador (acuerdo del orquestador no suma revisores).
- Archivos brutos: `findings-r4-terra.md` (1), `findings-r4-glm.md` (5), `findings-r4-deepseek.md` (6).
- El H2 de DeepSeek incluye verificación empírica en vivo: timestamps congelados medidos sobre una sesión de revisor en ejecución durante ~12 minutos.
