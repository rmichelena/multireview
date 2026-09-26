# Multi-Review — Round 6 (fresh-eyes)

**Panel (3/3 responded, no fallbacks):** gpt-5.6-terra · glm-5.3 · deepseek-v4.1-flash
**Target:** `rmichelena/multireview` @ `b1d9afee78e457b66cecfed8b72b7beb4e7de4f1` (round-5 fixes)
**Raw findings:** 16 (Terra 4 · GLM 5 · DeepSeek 7) → **13 unique: 1 High · 7 Medium · 5 Low**

---

## Fix verification (R5 findings)

| R5 | Verdict |
|---|---|
| H1 stale terminal runtime kills later rounds | **FIXED** — configFingerprint gates the terminal-replay path; reproduced mismatch now monitors fresh |
| M1 unqueryable ≠ absent | **FIXED** — `query_failed` holds last status (but left doc drift: see M6) |
| M2 first-poll ENOENT | **FIXED** — `saw_config` gate; never-seen config is a bounded config error |
| M3 status whitelist | **FIXED** — `KNOWN_TERMINAL_STATUSES` + `unknown` fallback (doc drift: see M6) |
| M4 empty listing / agent derivation | **FIXED** — empty listing is a query failure; per-entry `agent_id_for` (over-broad side effect: see L2/M7) |
| L1 fenced template in summary | **FIXED** — fence stripping (new edge case: see L5) |
| L2 env int crash | **FIXED** — `_env_int` defensive parse (bounds gap: see M3) |
| L3 silent wrong-agent fallback | **PARTIALLY FIXED** — ids derived per entry, but non-`agent:` keys still fall back silently (see M4) |
| L4 placeholder detector | **PARTIALLY FIXED** — exact matching works but the placeholder strings don't match SKILL.md (see L1) |
| L5 stall gate 2 polls | **FIXED** |
| L6 SKILL.md exit-0 paths | **PARTIALLY FIXED** — still undercounted (see L3) |
| L7 reviewDir absolute | **FIXED** |
| L8 injectable wake sleep | **FIXED** |
| L9 stale comment | **FIXED** |

⚠️ **Process finding (orchestrator):** the round-5 fix commit was announced with "regression suite green (exit 0)". That verification was **invalid**: the suite hangs at the stale-replay case (H1 below) and the orchestrator's `| tail` pipeline masked the 150s timeout kill as exit 0. Both r6 reviewers independently reproduced the hang.

---

## Fresh-eyes review — Round 6

### High

- **H1. Regression test suite hangs forever at the stale-replay case (2/3 reviewers: gpt-5.6-terra, glm-5.3)** — `test-review-babysitter.py:452`. `main_impl(stale_state, sleep_fn=lambda s: None)` with a perpetually-running fresh session, far-future deadline, and a no-op sleeper: the monitoring loop has no terminal condition. Busy-loops until the 1h deadline, then `round == "deadline"` fails the assertion. Verified by execution by both reviewers; the hang also silently disabled the r5 regression gate (see process finding above). **Fix:** make the sleeper unlink the config after N polls (as the h2 test does) or use an expired deadline and assert the intended one-poll behavior.

### Medium

- **M1. Non-terminal seeded runtime inherits two-poll evidence across config replacement (1/3 reviewers: gpt-5.6-terra)** — `review-babysitter.py:535`. The fingerprint is only consulted when the seeded runtime is *terminal*; a non-terminal seeded runtime keeps its models even when the new config has a different fingerprint. Replacing `pending-state.json` for a new round that reuses a sessionKey can satisfy the absent/stalled two-poll gates on the first poll of the new round with the previous round's observations — publishing a stale artifact as the new round's completion. **Fix:** compare fingerprints for non-terminal seeds too; on mismatch reset models and stability observations.
- **M2. NaN/Infinity deadlineMs pass validation and disable the deadline backstop (1/3 reviewers: gpt-5.6-terra)** — `review-babysitter.py:436`. `isinstance(deadlineMs, (int, float))` accepts NaN/inf (json.loads accepts them); `current >= NaN` is always False so the round never terminates by deadline. **Fix:** `math.isfinite` check + regression cases.
- **M3. Negative/zero poll intervals bypass defensive env parsing (1/3 reviewers: gpt-5.6-terra)** — `review-babysitter.py:27`. `_env_int` rejects malformed text but accepts `-1` → `time.sleep(-1)` raises outside the guarded loop → restart loop under `Restart=on-failure`; `0` spins the poll loop at full rate; negative stall threshold marks everything stale. **Fix:** validate operational bounds, fall back to defaults.
- **M4. `agent_id_for` silently falls back to the default agent for non-`agent:` keys (1/3 reviewers: glm-5.3)** — `review-babysitter.py:99`. Keys of other shapes (e.g. `subagent:<uuid>`) query the wrong agent's listing; the reviewer is never found and sits at `unknown` until deadline, with no diagnostic of the shape mismatch (contradicts the r5 L3 "no silent fallback" comment). **Fix:** validate key shape at config load or record `unresolvedSessionKeys`.
- **M5. SKILL.md documents the artifact-only fallback that r5 M1 removed (1/3 reviewers: deepseek-v4.1-flash)** — `SKILL.md:578` / `review-babysitter.py:342`. The `query_failed` branch holds the last status and never reads artifacts, so a stable valid artifact cannot terminalize during a sessions outage — but SKILL.md and the inline comment still advertise exactly that. Doc/comment drift vs code. **Fix:** restore an artifact-aware fallback or correct the docs to say the fallback only holds status.
- **M6. SKILL.md `lost`/`unknown` contract contradicts the r5 M3 whitelist (2/3 reviewers: glm-5.3, deepseek-v4.1-flash)** — `SKILL.md:547`. Docs say null/any future non-running status is `lost`; code returns non-terminal `unknown` for null/unrecognized statuses. Misleads operators reading runtime state during incidents. **Fix:** update the state contract bullets to the whitelist semantics.
- **M7. Unbounded `sessions --limit all` + 3-strike terminal policy can abort a healthy round (1/3 reviewers: deepseek-v4.1-flash)** — `review-babysitter.py:178`. A growing session store can push the listing past the 60s timeout; three consecutive failures (~90s apart) terminalize as `monitor-error` and wake while reviewers are still healthy — and the (advertised but missing, M5) artifact fallback can't rescue it. **Fix:** scope the query to configured sessionKeys and/or make repeated query failures non-terminal with the deadline as backstop.

### Low

- **L1. TEMPLATE_PLACEHOLDERS strings don't match the canonical SKILL.md template (2/3 reviewers: glm-5.3, deepseek-v4.1-flash)** — `review-babysitter.py:37`. The set has `<concrete fix>` / `<what the code does, why it is wrong, trigger>` but templates emit `<concrete fix suggestion>` / `...what triggers it`; parroted reasoning/fix/trace placeholders pass validation. **Fix:** sync strings with SKILL.md or normalize `<...>` free-text fields; add a verbatim-template regression test.
- **L2. Legitimately empty sessions listing is an error forever (1/3 reviewers: glm-5.3)** — `review-babysitter.py:629`. All sessions pruned → `{"sessions": []}` → permanent error path → `monitor-error` despite stable valid artifacts, while the absent-session gate would have completed correctly. **Fix:** distinguish rc==0 empty listing (genuine absence) from shape/CLI failures.
- **L3. "Two deliberate exit-0 paths" undercounts (1/3 reviewers: deepseek-v4.1-flash)** — `SKILL.md:538`. Code has ≥5: usage error, lock contention, terminal-restart-no-key, no-key monitor-error, stopped-config-gone, successful wake. **Fix:** enumerate them.
- **L4. Fence stripping invalidates a fully-fenced findings file (1/3 reviewers: deepseek-v4.1-flash)** — `review-babysitter.py:157`. A reviewer wrapping its entire well-formed output in a code fence gets classified `lost`. **Fix:** strip fences only for the stray-marker scan, or preserve regions containing complete blocks.
- **L5. Trailing space on the opening marker discards a complete artifact (1/3 reviewers: deepseek-v4.1-flash)** — `review-babysitter.py:33`. `===FINDING=== ` (trailing space) doesn't match `FINDING_RE` but matches `MARKER_LINE_RE` → whole artifact invalid; closing marker is asymmetrically tolerant. **Fix:** allow optional trailing whitespace on both markers.

---

## Recommended fix order

1. **H1** — the suite is the safety net; it must run to completion (and re-verify with `timeout` + direct exit code, no pipes).
2. **M1** (round-identity for non-terminal seeds — the fingerprint's stated purpose), **M5/M6** (doc truthfulness), **M7+L2** (query robustness).
3. **M2, M3, M4** (validation bounds and diagnostics).
4. Lows (parser tolerance + doc counts).
