#!/usr/bin/env python3
"""Regression tests for review-babysitter.py."""
from __future__ import annotations

import importlib.util
import json
import os
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
        write_valid(root, "===FINDING===\nseverity: High\n===END_FINDING===\nSummary")
        assert bs.artifact_state(root, "findings.md")[0] == "valid"
        # L2: CRLF and BOM tolerance
        write_valid(root, "===FINDING===\r\nseverity: High\r\n===END_FINDING===\r\nSummary\r\n")
        assert bs.artifact_state(root, "findings.md")[0] == "valid"
        write_valid(root, "NO FINDINGS\n\nQuality summary: nothing found.")
        assert bs.artifact_state(root, "findings.md")[0] == "valid"
        write_valid(root, "\ufeffNO FINDINGS")
        assert bs.artifact_state(root, "findings.md")[0] == "valid"
        write_valid(root, "loose text without blocks")
        assert bs.artifact_state(root, "findings.md")[0] == "invalid"
        # M3: NO FINDINGS followed by stray FINDING marker is still invalid
        write_valid(root, "NO FINDINGS\n===FINDING===\n")
        assert bs.artifact_state(root, "findings.md")[0] == "invalid"

        # --- classify -------------------------------------------------------
        write_valid(root, "===FINDING===\nseverity: High\n===END_FINDING===\nSummary")
        for status in ("done", "failed", "killed", "aborted", None):
            assert bs.classify(session(status), root, entry(), clock_ms=now)[0] == "done"
        (root / "findings.md").unlink()
        for status in ("done", "failed", "killed", "aborted", None):
            assert bs.classify(session(status), root, entry(), clock_ms=now)[0] == "lost"

        # M2: absent session needs two-poll stability before "done"
        write_valid(root)
        _, obs1 = bs.classify(None, root, entry(), clock_ms=now)
        assert bs.classify(None, root, entry(), clock_ms=now)[0] == "unknown"
        prev = {"artifactObservation": obs1}
        assert bs.classify(None, root, entry(), prev, clock_ms=now)[0] == "done"
        (root / "findings.md").unlink()
        assert bs.classify(None, root, entry(), prev, clock_ms=now)[0] == "unknown"

        # M4: stalled-with-artifact requires two consecutive STALE polls
        write_valid(root, "===FINDING===\nseverity: High\n===END_FINDING===\nSummary")
        recent = session("running", now)
        assert bs.classify(recent, root, entry(), clock_ms=now)[0] == "active"
        stale = session("running", now - (bs.STALL_THRESHOLD + 1) * 1000)
        first, observation = bs.classify(stale, root, entry(), clock_ms=now)
        assert first == "hung"
        prev = {"artifactObservation": observation}  # previous poll NOT stale
        again, observation = bs.classify(stale, root, entry(), prev, clock_ms=now)
        assert again == "hung"  # still only one stale poll
        prev = {"artifactObservation": observation, "stale": True}
        assert bs.classify(stale, root, entry(), prev, clock_ms=now)[0] == "stalled-with-artifact"
        # Changing artifact blocks terminality even with stale history
        (root / "findings.md").write_text("===FINDING===\nseverity: High\n===END_FINDING===\nSummary v2")
        assert bs.classify(stale, root, entry(), prev, clock_ms=now)[0] == "hung"

        # M5: missing lastInteractionAt falls back to updatedAt / sessionStartedAt
        ghost = session("running", 0, updatedAt=now - (bs.STALL_THRESHOLD + 1) * 1000)
        assert bs.classify(ghost, root, entry(), clock_ms=now)[0] == "hung"
        no_ts = {"status": "running"}  # no timestamps at all
        assert bs.classify(no_ts, root, entry(), prev, clock_ms=now)[0] == "hung"

        # --- evaluate_round -------------------------------------------------
        write_valid(root, "===FINDING===\nseverity: High\n===END_FINDING===\nSummary")
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
            assert bs.wake_orchestrator(config, state_path, runtime)
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
            assert bs.wake_orchestrator(config, state_path, runtime) is False
        finally:
            bs.subprocess.run = original_run
        saved = json.loads(state_path.read_text())
        assert saved["wakeDelivered"] is False and "boom" in saved["wakeError"]

        # H1: config-error path wakes and never silent-restarts forever;
        # missing config file is a clean stop.
        state_dir = root / "h1"
        state_dir.mkdir()
        state_config = state_dir / "pending-state.json"
        runtime_path = bs.runtime_path(state_config)
        try:
            def never_event(argv, **kwargs):
                raise AssertionError("openclaw should not be called for missing config")
            bs.subprocess.run = never_event
            rc = bs.main_impl(state_config, sleep_fn=lambda s: None)
            assert rc == 0, f"missing config should be clean stop, got {rc}"
        finally:
            bs.subprocess.run = original_run
        assert json.loads(runtime_path.read_text())["round"] == "stopped-config-gone"

        # Malformed config x3 -> monitor-error with wake attempt (delivery fails
        # because OPENCLAW_BIN points at /bin/false), exit 1.
        state_dir2 = root / "h1b"
        state_dir2.mkdir()
        bad_state = state_dir2 / "pending-state.json"
        bad_state.write_text("{not json")
        env_backup = dict(os.environ)
        try:
            os.environ["OPENCLAW_BIN"] = "/bin/false"
            rc = bs.main_impl(bad_state, sleep_fn=lambda s: None)
            assert rc == 1, f"config-error path should exit 1 after failed wake, got {rc}"
        finally:
            os.environ.clear(); os.environ.update(env_backup)
        saved = json.loads(bs.runtime_path(bad_state).read_text())
        assert saved["round"] == "monitor-error"
        assert saved["wakeDelivered"] is False
        assert "configErrors" in saved and "lastConfigError" in saved

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
