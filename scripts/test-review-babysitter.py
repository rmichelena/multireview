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
    return {"model": "m", "sessionKey": "k", "file": file}


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
        runtime = bs.evaluate_round(config, {}, {"k": session("killed")}, clock_ms=now)
        assert runtime["round"] == "complete"
        (root / "findings.md").unlink()
        runtime = bs.evaluate_round(config, {}, {"k": session("killed")}, clock_ms=now)
        assert runtime["round"] == "complete-with-losses"
        assert runtime["lostModels"] == ["m"]
        runtime = bs.evaluate_round(
            {**config, "deadlineMs": now - 1}, {}, {}, clock_ms=now
        )
        assert runtime["round"] == "deadline"
        # M7: missing session key gets an explicit diagnostic
        runtime = bs.evaluate_round(config, {}, {}, clock_ms=now)
        assert runtime["models"][0].get("diagnostic")

        # M1 (r5): query_failed holds the last status — never absent, never
        # promoted to done by the fallback.
        prev_runtime = bs.evaluate_round(config, {}, {"k": session("running")}, clock_ms=now)
        assert prev_runtime["models"][0]["status"] == "active"
        fb = bs.evaluate_round(config, prev_runtime, {}, clock_ms=now, query_failed=True)
        assert fb["models"][0]["status"] == "active"
        assert fb["models"][0]["sessionAbsent"] is False
        assert fb["models"][0]["diagnostic"] == "session query failed; holding last status"
        assert fb["round"] == "monitoring"
        # With no prior observation, an unknown status stays non-terminal too.
        fb2 = bs.evaluate_round(config, {}, {}, clock_ms=now, query_failed=True)
        assert fb2["models"][0]["status"] == "unknown"
        assert fb2["round"] == "monitoring"

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
                        {"sessions": [{"key": "k", "status": "running",
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

        # H1 (r5): a STALE terminal runtime from a previous round (fingerprint
        # mismatch) must NOT kill the new round: the watchdog monitors fresh.
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
                        {"sessions": [{"key": "k", "status": "running",
                                       "lastInteractionAt": bs.now_ms()}]}
                    )
                return Result()
            bs.subprocess.run = stale_run
            rc = bs.main_impl(stale_state, sleep_fn=lambda s: None)
            assert rc == 0
        finally:
            bs.subprocess.run = original_run
        assert stale_calls and stale_calls[0][1] == "sessions", "must monitor, not wake"
        saved = json.loads(stale_runtime.read_text())
        assert saved["round"] in ("monitoring", "complete")
        assert "staleTerminalFrom" in saved or saved.get("round") == "monitoring"

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
            {"key": "k", "status": "killed", "lastInteractionAt": 100},
            {"key": "k", "status": "done", "lastInteractionAt": 900},
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
        assert mapping["k"]["status"] == "done" and "__duplicate__" not in mapping
        # M4 (r5): an EMPTY sessions listing is a query failure, not "all
        # absent" — the watchdog must enter the fallback, never terminalize.
        empty_dir = root / "emptyq"
        empty_dir.mkdir()
        empty_state = empty_dir / "pending-state.json"
        empty_state.write_text(json.dumps(h2_config))
        try:
            def empty_sessions(argv, **kwargs):
                class Result:
                    returncode = 0
                    stderr = ""
                    stdout = json.dumps({"sessions": []})
                return Result()
            bs.subprocess.run = empty_sessions
            rc = bs.main_impl(empty_state, sleep_fn=lambda s: None)
            assert rc == 0
        finally:
            bs.subprocess.run = original_run
        saved = json.loads(bs.runtime_path(empty_state).read_text())
        assert saved.get("sessionQueryFallback") is True
        assert saved["models"][0]["sessionAbsent"] is False
        assert saved["round"] == "monitor-error"
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
                        {"sessions": [{"key": "k", "status": "done",
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
