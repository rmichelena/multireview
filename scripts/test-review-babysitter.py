#!/usr/bin/env python3
"""Regression tests for review-babysitter.py."""
from __future__ import annotations

import importlib.util
import json
import os
import fcntl
import stat
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).with_name("review-babysitter.py")
spec = importlib.util.spec_from_file_location("review_babysitter", SCRIPT)
assert spec and spec.loader
bs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bs)


def session(status="done", last=None, **extra):
    data = {"status": status, "lastInteractionAt": bs.now_ms() if last is None else last}
    data.update(extra)
    return data


def write_valid(root: Path, text: str = "NO FINDINGS") -> None:
    (root / "findings.md").write_text(text)


def entry(file="findings.md"):
    # r6 M4: session keys must be real 'agent:<id>:...' keys now.
    return {"model": "m", "sessionKey": "agent:main:k", "file": file}


def run() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        now = bs.now_ms()

        # --- artifact_state -------------------------------------------------
        assert bs.artifact_state(root, "findings.md")[0] == "missing"
        (root / "findings.md").write_text("partial")
        assert bs.artifact_state(root, "findings.md")[0] == "invalid"
        (root / "findings.md").write_text("===FINDING===\nseverity: High")
        assert bs.artifact_state(root, "findings.md")[0] == "invalid"
        write_valid(root, "===FINDING===\nseverity: High\ntitle: t\nfile: a\nline: 1\nreasoning: r\nfix: f\ntrace: N/A\n===END_FINDING===")
        assert bs.artifact_state(root, "findings.md")[0] == "valid"
        # L2: CRLF and BOM tolerance
        write_valid(root, "===FINDING===\r\nseverity: High\ntitle: t\nfile: a\nline: 1\nreasoning: r\nfix: f\ntrace: N/A\n===END_FINDING===")
        assert bs.artifact_state(root, "findings.md")[0] == "valid"
        write_valid(root, "NO FINDINGS\n\nQuality summary: nothing found.")
        assert bs.artifact_state(root, "findings.md")[0] == "valid"
        write_valid(root, "\ufeffNO FINDINGS")
        assert bs.artifact_state(root, "findings.md")[0] == "valid"
        write_valid(root, "loose text without blocks")
        assert bs.artifact_state(root, "findings.md")[0] == "invalid"
        # M3 (r3): NO FINDINGS followed by a stray MARKER-ONLY line is invalid...
        write_valid(root, "NO FINDINGS\n===FINDING===\n")
        assert bs.artifact_state(root, "findings.md")[0] == "invalid"
        # ...but a summary merely MENTIONING the token inline is valid.
        write_valid(root, "NO FINDINGS\n\nQuality summary: no ===FINDING=== blocks emitted.")
        assert bs.artifact_state(root, "findings.md")[0] == "valid"
        # M1 (r3): structurally complete blocks must carry all required fields.
        write_valid(root, "===FINDING===\n\n===END_FINDING===")
        assert bs.artifact_state(root, "findings.md")[0] == "invalid"
        write_valid(root, "===FINDING===\nseverity: High\ntitle: x\nfile: a.py\nline: 1\n"
                          "reasoning: r\nfix: f\n===END_FINDING===")
        assert bs.artifact_state(root, "findings.md")[0] == "invalid"  # trace missing
        write_valid(root, "===FINDING===\nseverity: High\ntitle: x\nfile: a.py\nline: 1\n"
                          "reasoning: r\nfix: f\ntrace: N/A\n===END_FINDING===\n"
                          "Quality summary: wrote 1 ===FINDING=== block.")
        assert bs.artifact_state(root, "findings.md")[0] == "valid"
        # L1 (r4): template placeholder values are invalid even with all fields.
        write_valid(root, "===FINDING===\nseverity: High\ntitle: <one line title>\nfile: <path>\n"
                          "line: <line_number>\nreasoning: <what the code does>\n"
                          "fix: <concrete fix>\ntrace: <concrete trace or N/A>\n===END_FINDING===")
        assert bs.artifact_state(root, "findings.md")[0] == "invalid"
        # L4 (r5): the detector matches exact template placeholders only, not
        # every <...> value — a genuine `title: <script>` finding is content.
        write_valid(root, "===FINDING===\nseverity: High\ntitle: <script>\nfile: a\nline: 1\n"
                          "reasoning: r\nfix: f\ntrace: N/A\n===END_FINDING===")
        assert bs.artifact_state(root, "findings.md")[0] == "valid"
        # L1 (r5): a summary quoting the template inside a fenced code block
        # is legitimate prose — fences are stripped before marker scanning.
        write_valid(root, "===FINDING===\nseverity: High\ntitle: t\nfile: a\nline: 1\n"
                          "reasoning: r\nfix: f\ntrace: N/A\n===END_FINDING===\n"
                          "Quality summary:\n```text\n===FINDING===\nseverity: [X]\n===END_FINDING===\n```")
        assert bs.artifact_state(root, "findings.md")[0] == "valid"
        # L4 (r6): an artifact wrapped ENTIRELY in a code fence is still a review.
        write_valid(root, "```text\n===FINDING===\nseverity: High\ntitle: t\nfile: a\nline: 1\n"
                          "reasoning: r\nfix: f\ntrace: N/A\n===END_FINDING===\n```")
        assert bs.artifact_state(root, "findings.md")[0] == "valid"
        # L5 (r6): trailing whitespace on the markers must not invalidate.
        write_valid(root, "===FINDING=== \nseverity: High\ntitle: t\nfile: a\nline: 1\n"
                          "reasoning: r\nfix: f\ntrace: N/A\n ===END_FINDING===")
        assert bs.artifact_state(root, "findings.md")[0] == "valid"
        # L1 (r6): placeholders copied verbatim from the SKILL.md template
        # (not the validator's old strings) are invalid content.
        write_valid(root, "===FINDING===\nseverity: High\ntitle: t\nfile: a.py\nline: 1\n"
                          "reasoning: <what the code does, why it's wrong, what triggers it>\n"
                          "fix: <concrete fix suggestion>\n"
                          "trace: <concrete trace for logic claims, or N/A>\n===END_FINDING===")
        assert bs.artifact_state(root, "findings.md")[0] == "invalid"

        # --- classify -------------------------------------------------------
        write_valid(root, "===FINDING===\nseverity: High\ntitle: t\nfile: a\nline: 1\nreasoning: r\nfix: f\ntrace: N/A\n===END_FINDING===")
        for status in ("done", "failed", "killed", "aborted"):
            assert bs.classify(session(status), root, entry(), clock_ms=now)[0] == "done"
        (root / "findings.md").unlink()
        for status in ("done", "failed", "killed", "aborted"):
            assert bs.classify(session(status), root, entry(), clock_ms=now)[0] == "lost"
        # M3 (r5): unknown/missing status is NOT evidence of exit.
        write_valid(root)
        for status in (None, "queued", "starting"):
            assert bs.classify(session(status), root, entry(), clock_ms=now)[0] == "unknown"
        (root / "findings.md").unlink()

        # M2 (r4): absent session needs TWO consecutive absent polls plus a
        # stable artifact before "done".
        write_valid(root)
        _, obs1 = bs.classify(None, root, entry(), clock_ms=now)
        assert bs.classify(None, root, entry(), clock_ms=now)[0] == "unknown"
        prev = {"artifactObservation": obs1}  # artifact stable, but session was present
        assert bs.classify(None, root, entry(), prev, clock_ms=now)[0] == "unknown"
        prev = {"artifactObservation": obs1, "sessionAbsent": True}
        assert bs.classify(None, root, entry(), prev, clock_ms=now)[0] == "done"
        (root / "findings.md").unlink()
        assert bs.classify(None, root, entry(), prev, clock_ms=now)[0] == "unknown"

        # M4 (r3) + H2 (r4): stalled-with-artifact requires two consecutive
        # STALE polls, an unchanged valid artifact, AND no transcript growth.
        write_valid(root, "===FINDING===\nseverity: High\ntitle: t\nfile: a\nline: 1\nreasoning: r\nfix: f\ntrace: N/A\n===END_FINDING===")
        sess_file = root / "reviewer.jsonl"
        sess_file.write_text('{"type":"message"}\n')
        recent = session("running", now, sessionFile=str(sess_file))
        assert bs.classify(recent, root, entry(), clock_ms=now)[0] == "active"
        stale = session("running", now - (bs.STALL_THRESHOLD + 1) * 1000,
                        sessionFile=str(sess_file))
        first, observation = bs.classify(stale, root, entry(), clock_ms=now)
        assert first == "hung"
        trans = bs.transcript_obs(stale)
        assert trans is not None
        prev = {"artifactObservation": observation}  # previous poll NOT stale
        again, observation = bs.classify(stale, root, entry(), prev, clock_ms=now)
        assert again == "hung"  # still only one stale poll
        prev = {"artifactObservation": observation, "stale": True}
        assert bs.classify(stale, root, entry(), prev, clock_ms=now)[0] == "hung"  # no transcript evidence yet
        prev = {"artifactObservation": observation, "stale": True,
                "transcriptObservation": trans}
        assert bs.classify(stale, root, entry(), prev, clock_ms=now)[0] == "stalled-with-artifact"
        # H2 (r4): transcript GROWTH proves liveness even with stale timestamps
        # (session-store timestamps freeze mid-turn — verified live in round 4).
        sess_file.write_text('{"type":"message"}\n{"type":"message"}\n')
        assert bs.classify(stale, root, entry(), prev, clock_ms=now)[0] == "active"
        sess_file.write_text('{"type":"message"}\n')
        trans2 = bs.transcript_obs(stale)
        prev = {"artifactObservation": observation, "stale": True,
                "transcriptObservation": trans2}
        # Changing artifact blocks terminality even with stale history
        (root / "findings.md").write_text("===FINDING===\nseverity: High\n===END_FINDING===\nSummary v2")
        assert bs.classify(stale, root, entry(), prev, clock_ms=now)[0] == "hung"

        # M5: missing lastInteractionAt falls back to updatedAt / sessionStartedAt
        ghost = session("running", 0, updatedAt=now - (bs.STALL_THRESHOLD + 1) * 1000)
        assert bs.classify(ghost, root, entry(), clock_ms=now)[0] == "hung"
        no_ts = {"status": "running"}  # no timestamps at all
        assert bs.classify(no_ts, root, entry(), prev, clock_ms=now)[0] == "hung"

        # --- evaluate_round -------------------------------------------------
        write_valid(root, "===FINDING===\nseverity: High\ntitle: t\nfile: a\nline: 1\nreasoning: r\nfix: f\ntrace: N/A\n===END_FINDING===")
        config = {
            "reviewDir": str(root),
            "deadlineMs": now + 1000,
            "orchestratorSessionKey": "agent:main:discord:channel:123",
            "models": [entry()],
        }
        runtime = bs.evaluate_round(config, {}, {"agent:main:k": session("killed")}, clock_ms=now)
        assert runtime["round"] == "complete"
        (root / "findings.md").unlink()
        runtime = bs.evaluate_round(config, {}, {"agent:main:k": session("killed")}, clock_ms=now)
        assert runtime["round"] == "complete-with-losses"
        assert runtime["lostModels"] == ["m"]
        runtime = bs.evaluate_round(
            {**config, "deadlineMs": now - 1}, {}, {}, clock_ms=now
        )
        assert runtime["round"] == "deadline"
        # M7: missing session key gets an explicit diagnostic
        runtime = bs.evaluate_round(config, {}, {}, clock_ms=now)
        assert runtime["models"][0].get("diagnostic")

        # M1 (r5) + M5 (r6): query_failed holds the last status on the first
        # failed poll; only a valid artifact UNCHANGED across TWO consecutive
        # query-failed polls terminalizes (artifact-aware fallback).
        write_valid(root, "===FINDING===\nseverity: High\ntitle: t\nfile: a\nline: 1\nreasoning: r\nfix: f\ntrace: N/A\n===END_FINDING===")
        prev_runtime = bs.evaluate_round(config, {}, {"agent:main:k": session("running")}, clock_ms=now)
        assert prev_runtime["models"][0]["status"] == "active"
        fb = bs.evaluate_round(config, prev_runtime, {}, clock_ms=now, query_failed=True)
        assert fb["models"][0]["status"] == "active"
        assert fb["models"][0]["sessionAbsent"] is False
        assert fb["models"][0]["diagnostic"] == "session query failed; holding last status"
        assert fb["round"] == "monitoring"
        fb2 = bs.evaluate_round(config, fb, {}, clock_ms=now, query_failed=True)
        assert fb2["models"][0]["status"] == "done"
        assert "stable valid artifact" in fb2["models"][0]["diagnostic"]
        assert fb2["round"] == "complete"
        # With no prior observation, an unknown status stays non-terminal too.
        fb3 = bs.evaluate_round(config, {}, {}, clock_ms=now, query_failed=True)
        assert fb3["models"][0]["status"] == "unknown"
        assert fb3["round"] == "monitoring"
        # ...and unknown never promotes, not even after many failed polls.
        fb4 = bs.evaluate_round(config, fb3, {}, clock_ms=now, query_failed=True)
        assert fb4["models"][0]["status"] == "unknown"

        # --- validate_config -------------------------------------------------
        bad_config = {**config, "models": [{**entry(), "file": "../escape.md"}]}
        try:
            bs.validate_config(bad_config)
            raise AssertionError("path escape accepted")
        except ValueError:
            pass
        # M1: type validation
        try:
            bs.validate_config({**config, "deadlineMs": "2026-09-24T00:00:00Z"})
            raise AssertionError("string deadlineMs accepted")
        except ValueError:
            pass
        # M2 (r6): non-finite deadlines defeat the deadline backstop forever.
        try:
            bs.validate_config({**config, "deadlineMs": float("nan")})
            raise AssertionError("NaN deadlineMs accepted")
        except ValueError:
            pass
        try:
            bs.validate_config({**config, "deadlineMs": float("inf")})
            raise AssertionError("inf deadlineMs accepted")
        except ValueError:
            pass
        # M4 (r6): non-'agent:' session keys silently resolved to the wrong
        # agent; validation must reject them loudly.
        try:
            bs.validate_config({**config, "models": [{**entry(), "sessionKey": "subagent:abc"}]})
            raise AssertionError("non-agent sessionKey accepted")
        except ValueError:
            pass
        # M1 (r4): an empty panel is a config error, never a successful round.
        try:
            bs.validate_config({**config, "models": []})
            raise AssertionError("empty models accepted")
        except ValueError:
            pass
        # M8: duplicate file / sessionKey rejected
        dup_file = {**config, "models": [entry(), entry()]}
        try:
            bs.validate_config(dup_file)
            raise AssertionError("duplicate file accepted")
        except ValueError:
            pass
        dup_key = {**config, "models": [entry(), {**entry(), "file": "other.md"}]}
        try:
            bs.validate_config(dup_key)
            raise AssertionError("duplicate sessionKey accepted")
        except ValueError:
            pass
        # L7 (r5): reviewDir must be absolute — under systemd-run the CWD is
        # $HOME and a relative path silently reads the wrong tree.
        try:
            bs.validate_config({**config, "reviewDir": "relative/review"})
            raise AssertionError("relative reviewDir accepted")
        except ValueError:
            pass
        # M3 (r6): out-of-bounds env values fall back to the default instead
        # of reaching an unguarded sleep().
        saved_env = {k: os.environ.get(k) for k in ("T_NEG", "T_ZERO")}
        try:
            os.environ["T_NEG"] = "-1"
            os.environ["T_ZERO"] = "0"
            assert bs._env_int("T_MISSING", 7) == 7
            assert bs._env_int("T_NEG", 300, minimum=1) == 300
            assert bs._env_int("T_ZERO", 300, minimum=1) == 300
            assert bs._env_int("T_ZERO", 300) == 0  # unbounded parse stays permissive
        finally:
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

        # --- wake_orchestrator ----------------------------------------------
        state_path = root / "pending-state.runtime.json"
        calls = []
        original_run = bs.subprocess.run
        try:
            def fake_run(argv, **kwargs):
                calls.append(argv)
                class Result:
                    returncode = 0
                    stderr = ""
                return Result()
            bs.subprocess.run = fake_run
            runtime = {"round": "complete"}
            assert bs.wake_orchestrator(config, state_path, runtime,
                                        sleep_fn=lambda s: None)
        finally:
            bs.subprocess.run = original_run
        assert "--session-key" in calls[0]
        assert json.loads(state_path.read_text())["wakeDelivered"] is True

        # M9: wake failure x3 records wakeDelivered False + wakeError
        try:
            def failing_run(argv, **kwargs):
                class Result:
                    returncode = 1
                    stderr = "boom"
                return Result()
            bs.subprocess.run = failing_run
            runtime = {"round": "complete"}
            assert bs.wake_orchestrator(config, state_path, runtime,
                                        sleep_fn=lambda s: None) is False
        finally:
            bs.subprocess.run = original_run
        saved = json.loads(state_path.read_text())
        assert saved["wakeDelivered"] is False and "boom" in saved["wakeError"]

        # H1: missing config that was NEVER seen is now a bounded config-error
        # path (r5 M2): 3 errors -> monitor-error with no key, exit 0.
        state_dir = root / "h1"
        state_dir.mkdir()
        state_config = state_dir / "pending-state.json"
        runtime_path = bs.runtime_path(state_config)
        try:
            def never_event(argv, **kwargs):
                raise AssertionError("openclaw should not be called for missing config")
            bs.subprocess.run = never_event
            rc = bs.main_impl(state_config, sleep_fn=lambda s: None)
            assert rc == 0, f"no-key monitor-error must not restart-loop, got {rc}"
        finally:
            bs.subprocess.run = original_run
        saved = json.loads(runtime_path.read_text())
        assert saved["round"] == "monitor-error"
        assert saved.get("configErrors", 0) >= 3
        assert saved["wakeDelivered"] is False

        # H1b/M4 (r3): malformed config x3 -> monitor-error; with no wake key
        # available the process exits 0 so systemd does NOT restart-loop.
        state_dir2 = root / "h1b"
        state_dir2.mkdir()
        bad_state = state_dir2 / "pending-state.json"
        bad_state.write_text("{not json")
        try:
            def never_event(argv, **kwargs):
                raise AssertionError("no wake should be attempted without a key")
            bs.subprocess.run = never_event
            rc = bs.main_impl(bad_state, sleep_fn=lambda s: None)
            assert rc == 0, f"no-key monitor-error must not restart-loop, got {rc}"
        finally:
            bs.subprocess.run = original_run
        saved = json.loads(bs.runtime_path(bad_state).read_text())
        assert saved["round"] == "monitor-error"
        assert saved["wakeDelivered"] is False
        assert "configErrors" in saved and "lastConfigError" in saved

        # H1c (r3): config that PARSES but fails validation still yields the
        # wake key; the wake is attempted (and fails with a fake), exit 1.
        state_dir3 = root / "h1c"
        state_dir3.mkdir()
        bad_models_state = state_dir3 / "pending-state.json"
        bad_models_state.write_text(json.dumps(
            {**config, "models": "not-a-list"}))
        wake_calls = []
        try:
            def failing_run(argv, **kwargs):
                wake_calls.append(argv)
                class Result:
                    returncode = 1
                    stderr = "gateway down"
                return Result()
            bs.subprocess.run = failing_run
            rc = bs.main_impl(bad_models_state, sleep_fn=lambda s: None)
            assert rc == 1, f"keyed monitor-error should exit 1 after failed wake, got {rc}"
        finally:
            bs.subprocess.run = original_run
        assert wake_calls and any(argv[1] == "system" for argv in wake_calls)
        saved = json.loads(bs.runtime_path(bad_models_state).read_text())
        assert saved["round"] == "monitor-error" and saved["wakeDelivered"] is False

        # H2 (r3): internal_errors is CONSECUTIVE — scattered exceptions with
        # healthy polls in between must NOT terminate the round.
        h2_state = root / "h2" / "pending-state.json"
        h2_state.parent.mkdir()
        h2_config = {**config, "deadlineMs": bs.now_ms() + 3_600_000}  # far future
        h2_state.write_text(json.dumps(h2_config))
        poll_state = {"n": 0}
        real_evaluate = bs.evaluate_round

        def flaky_evaluate(config_arg, runtime, sessions, **kwargs):
            poll_state["n"] += 1
            # Raise on polls 1, 3 and 5 (never twice in a row).
            if poll_state["n"] in (1, 3, 5):
                raise RuntimeError(f"transient {poll_state['n']}")
            return real_evaluate(config_arg, runtime, sessions, **kwargs)

        # Polls 1/3/5 raise (never consecutively); after poll 6 the sleeper
        # removes the config so poll 7 stops cleanly (stopped-config-gone).
        def sleeper(_s):
            if poll_state["n"] >= 6:
                h2_state.unlink()
        try:
            def ok_sessions(argv, **kwargs):
                class Result:
                    returncode = 0
                    stderr = ""
                    stdout = json.dumps(
                        {"sessions": [{"key": "agent:main:k", "status": "running",
                                       "lastInteractionAt": bs.now_ms()}]}
                    )
                return Result()
            bs.subprocess.run = ok_sessions
            bs.evaluate_round = flaky_evaluate
            (root / "findings.md").unlink(missing_ok=True)  # keep the artifact out of the way
            rc = bs.main_impl(h2_state, sleep_fn=sleeper)
            assert rc == 0
        finally:
            bs.subprocess.run = original_run
            bs.evaluate_round = real_evaluate
        assert poll_state["n"] >= 6, f"expected >=6 polls before clean stop, got {poll_state['n']}"
        saved = json.loads(bs.runtime_path(h2_state).read_text())
        assert saved["round"] == "stopped-config-gone"
        assert "internalErrors" not in saved and "lastInternalError" not in saved
        # H2b: three BACK-TO-BACK internal errors DO terminate with monitor-error.
        h2b_state = root / "h2b" / "pending-state.json"
        h2b_state.parent.mkdir()
        h2b_state.write_text(json.dumps(h2_config))
        try:
            def always_raises(config_arg, runtime, sessions, **kwargs):
                raise RuntimeError("persistent")
            bs.subprocess.run = ok_sessions
            bs.evaluate_round = always_raises
            rc = bs.main_impl(h2b_state, sleep_fn=lambda s: None)
            assert rc == 0
        finally:
            bs.subprocess.run = original_run
            bs.evaluate_round = real_evaluate
        saved = json.loads(bs.runtime_path(h2b_state).read_text())
        assert saved["round"] == "monitor-error"
        assert saved.get("lastInternalError") == "persistent"

        # H1 (r4): a duplicate instance (lock already held) exits 0 — a running
        # peer is steady state, not a systemd restart-loop trigger.
        lock_dir = root / "locktest"
        lock_dir.mkdir()
        lock_state = lock_dir / "pending-state.json"
        lock_state.write_text(json.dumps(h2_config))
        lock_path = lock_state.with_suffix(".json.lock")
        with lock_path.open("w") as lock_handle:
            fcntl.flock(lock_handle, fcntl.LOCK_EX)
            rc = bs.main_impl(lock_state, sleep_fn=lambda s: None)
            assert rc == 0, f"lock contention must exit 0, got {rc}"

        # M3 (r4) + H1 (r5): restart with a persisted TERMINAL runtime of the
        # SAME round (fingerprint match) retries the wake; a stale terminal
        # runtime from a PREVIOUS round must monitor fresh instead.
        replay_dir = root / "replay"
        replay_dir.mkdir()
        replay_state = replay_dir / "pending-state.json"
        replay_config = {**h2_config, "reviewDir": str(root)}
        replay_state.write_text(json.dumps(replay_config))
        replay_runtime = bs.runtime_path(replay_state)
        replay_runtime.write_text(json.dumps(
            {"round": "monitor-error", "wakeError": "gateway down",
             "configFingerprint": bs.config_fingerprint(replay_config)}))
        replay_calls = []
        try:
            def replay_run(argv, **kwargs):
                replay_calls.append(argv)
                class Result:
                    returncode = 1
                    stderr = "still down"
                return Result()
            bs.subprocess.run = replay_run
            rc = bs.main_impl(replay_state, sleep_fn=lambda s: None)
            assert rc == 1, f"failed wake retry must exit 1, got {rc}"
        finally:
            bs.subprocess.run = original_run
        assert replay_calls and replay_calls[0][1] == "system", "wake must be retried, not monitored"
        saved = json.loads(replay_runtime.read_text())
        assert saved["round"] == "monitor-error" and saved["wakeDelivered"] is False

        # H1 (r5) + H1 (r6): a STALE terminal runtime from a previous round
        # (fingerprint mismatch) must NOT kill the new round: the watchdog
        # monitors fresh. The test previously busy-looped forever here (no
        # terminal condition, no-op sleeper) — the suite hung silently and a
        # `| tail` pipeline masked the timeout kill as exit 0.
        stale_dir = root / "stalereplay"
        stale_dir.mkdir()
        stale_state = stale_dir / "pending-state.json"
        stale_state.write_text(json.dumps(h2_config))
        stale_runtime = bs.runtime_path(stale_state)
        stale_runtime.write_text(json.dumps(
            {"round": "complete", "wakeDelivered": True,
             "configFingerprint": "deadbeef00000000"}))
        stale_calls = []
        try:
            def stale_run(argv, **kwargs):
                stale_calls.append(argv)
                class Result:
                    returncode = 0
                    stderr = ""
                    stdout = json.dumps(
                        {"sessions": [{"key": "agent:main:k", "status": "running",
                                       "lastInteractionAt": bs.now_ms()}]}
                    )
                return Result()
            bs.subprocess.run = stale_run
            stale_polls = {"n": 0}

            def stale_sleeper(_s):
                stale_polls["n"] += 1
                if stale_polls["n"] >= 2:
                    stale_state.unlink()  # clean stop after 2 monitoring polls

            rc = bs.main_impl(stale_state, sleep_fn=stale_sleeper)
            assert rc == 0
        finally:
            bs.subprocess.run = original_run
        assert stale_calls and stale_calls[0][1] == "sessions", "must monitor, not wake"
        saved = json.loads(stale_runtime.read_text())
        assert saved["round"] == "stopped-config-gone"
        assert saved.get("staleTerminalFrom") == "complete"

        # M1 (r6): a NON-terminal runtime from ANOTHER round must not lend its
        # two-poll stability evidence to the replacement round: with matching
        # seeded evidence an unrestarted watchdog would classify "done" on the
        # very first poll of the new round.
        xdir = root / "crossround"
        xdir.mkdir()
        xstate = xdir / "pending-state.json"
        xcfg = {**h2_config, "reviewDir": str(root),
                "deadlineMs": bs.now_ms() + 3_600_000}
        xstate.write_text(json.dumps(xcfg))
        (root / "findings.md").write_text("NO FINDINGS")
        st = (root / "findings.md").stat()
        real_obs = {"size": st.st_size, "mtimeNs": st.st_mtime_ns}
        xseed = {"round": "monitoring", "configFingerprint": "aaaa0000aaaa0000",
                 "models": [{"model": "m", "sessionKey": "agent:main:k",
                             "file": "findings.md", "status": "unknown",
                             "sessionAbsent": True, "artifactObservation": real_obs}]}
        bs.runtime_path(xstate).write_text(json.dumps(xseed))
        xcalls = []
        try:
            def x_run(argv, **kwargs):
                xcalls.append(argv)
                class Result:
                    returncode = 0
                    stderr = ""
                    stdout = json.dumps({"sessions": []})  # genuine absence (L2)
                return Result()
            bs.subprocess.run = x_run
            xp = {"n": 0}

            def x_sleeper(_s):
                xp["n"] += 1
                if xp["n"] >= 1:
                    xstate.unlink()

            rc = bs.main_impl(xstate, sleep_fn=x_sleeper)
            assert rc == 0
        finally:
            bs.subprocess.run = original_run
        saved = json.loads(bs.runtime_path(xstate).read_text())
        m0 = saved["models"][0]
        # First poll of the new round: absence is NOT yet stable -> unknown.
        assert m0["status"] == "unknown", m0
        assert m0["sessionAbsent"] is True
        assert saved.get("staleObservationsFrom") == "aaaa0000aaaa0000"

        # M7 (r6): repeated session-query failures are NON-terminal — the
        # round keeps polling (deadline backstop) instead of monitor-error.
        m7dir = root / "sesserr"
        m7dir.mkdir()
        m7state = m7dir / "pending-state.json"
        m7state.write_text(json.dumps({**h2_config, "reviewDir": str(root)}))
        (root / "findings.md").unlink()
        try:
            def err_sessions(argv, **kwargs):
                class Result:
                    returncode = 1
                    stderr = "cli down"
                    stdout = ""
                return Result()
            bs.subprocess.run = err_sessions
            m7p = {"n": 0}

            def m7_sleeper(_s):
                m7p["n"] += 1
                if m7p["n"] >= 5:
                    m7state.unlink()

            rc = bs.main_impl(m7state, sleep_fn=m7_sleeper)
            assert rc == 0
        finally:
            bs.subprocess.run = original_run
        saved = json.loads(bs.runtime_path(m7state).read_text())
        assert saved["round"] == "stopped-config-gone"
        assert saved.get("sessionQueryErrors", 0) >= 5

        # M5 (r4): a JSON object without a sessions array is an error, not an
        # empty review (silent {} would mark every reviewer absent).
        try:
            def weird_sessions(argv, **kwargs):
                class Result:
                    returncode = 0
                    stderr = ""
                    stdout = json.dumps({"error": "internal", "count": 0})
                return Result()
            bs.subprocess.run = weird_sessions
            mapping = bs.query_sessions("main")
        finally:
            bs.subprocess.run = original_run
        assert "__error__" in mapping and "shape" in mapping["__error__"]

        # L2 (r3): duplicate session keys keep the FRESHEST entry, no sentinel.
        dedup_input = [
            {"key": "agent:main:k", "status": "killed", "lastInteractionAt": 100},
            {"key": "agent:main:k", "status": "done", "lastInteractionAt": 900},
        ]
        try:
            def dup_sessions(argv, **kwargs):
                assert "--agent" in argv  # H1 (r3): agent id passed explicitly
                class Result:
                    returncode = 0
                    stderr = ""
                    stdout = json.dumps({"sessions": dedup_input})
                return Result()
            bs.subprocess.run = dup_sessions
            mapping = bs.query_sessions("main")
        finally:
            bs.subprocess.run = original_run
        assert mapping["agent:main:k"]["status"] == "done" and "__duplicate__" not in mapping
        # L2 (r6): a successful EMPTY listing is genuine absence — the normal
        # absent-session two-poll gate classifies it (no fallback flag, no
        # monitor-error). Two absent polls + stable valid artifact = complete.
        empty_dir = root / "emptyq"
        empty_dir.mkdir()
        empty_state = empty_dir / "pending-state.json"
        empty_state.write_text(json.dumps(h2_config))
        (root / "findings.md").write_text("NO FINDINGS")
        try:
            def empty_sessions(argv, **kwargs):
                class Result:
                    returncode = 0
                    stderr = ""
                    stdout = json.dumps({"sessions": []})
                return Result()
            bs.subprocess.run = empty_sessions
            ep = {"n": 0}

            def empty_sleeper(_s):
                ep["n"] += 1
                if ep["n"] >= 2:
                    empty_state.unlink()  # clean stop after the round completed

            rc = bs.main_impl(empty_state, sleep_fn=empty_sleeper)
            assert rc == 0
        finally:
            bs.subprocess.run = original_run
        saved = json.loads(bs.runtime_path(empty_state).read_text())
        assert saved["round"] == "complete", saved.get("round")
        assert saved["models"][0]["status"] == "done"
        assert saved["models"][0]["sessionAbsent"] is True
        assert "sessionQueryFallback" not in saved
        # L4: successful config load clears stale error fields
        runtime = {"configErrors": 1, "lastConfigError": "old"}
        good_state = state_dir / "pending-state.json"
        good_state.write_text(json.dumps(config))
        try:
            def ok_event(argv, **kwargs):
                class Result:
                    returncode = 0
                    stderr = ""
                    stdout = ""
                if len(argv) > 1 and argv[1] == "sessions":
                    Result.stdout = json.dumps(
                        {"sessions": [{"key": "agent:main:k", "status": "done",
                                       "lastInteractionAt": bs.now_ms()}]}
                    )
                return Result()
            bs.subprocess.run = ok_event
            rc = bs.main_impl(good_state, sleep_fn=lambda s: None)
            assert rc == 0
        finally:
            bs.subprocess.run = original_run
        saved = json.loads(bs.runtime_path(good_state).read_text())
        assert "configErrors" not in saved and "lastConfigError" not in saved
        assert saved["round"] in ("complete", "complete-with-losses", "deadline")

    print("review-babysitter regression tests: OK")


if __name__ == "__main__":
    run()
