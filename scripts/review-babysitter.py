#!/usr/bin/env python3
"""Detached completion watchdog for the multireview skill."""
from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

POLL_INTERVAL = int(os.environ.get("BABYSITTER_POLL_INTERVAL", "300"))
STALL_THRESHOLD = int(os.environ.get("BABYSITTER_STALL_THRESHOLD", "900"))
OPENCLAW = os.environ.get("OPENCLAW_BIN", "openclaw")
AGENT_ID = os.environ.get("OPENCLAW_AGENT_ID", "main")
SESSIONS_DIR = Path(os.environ.get(
    "OPENCLAW_SESSIONS_DIR",
    str(Path.home() / ".openclaw" / "agents" / AGENT_ID / "sessions"),
))
TERMINAL_MODEL_STATES = {"done", "lost", "stalled-with-artifact"}
TERMINAL_ROUNDS = {"complete", "complete-with-losses", "deadline", "monitor-error"}
FINDING_RE = re.compile(r"===FINDING===\n.*?\n===END_FINDING===", re.S)
MARKER_LINE_RE = re.compile(r"^\s*===(?:FINDING|END_FINDING)===\s*$")
REQUIRED_FIELDS = ("severity", "title", "file", "line", "reasoning", "fix", "trace")
SEVERITIES = {"critical", "high", "medium", "low"}
MAX_CONSECUTIVE_ERRORS = 3
DIAGNOSTIC_TAIL_BYTES = 256 * 1024


def now_ms() -> int:
    return int(time.time() * 1000)


def runtime_path(config_path: Path) -> Path:
    return config_path.with_name(config_path.stem + ".runtime.json")


def load_json(path: Path) -> dict:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def safe_save(path: Path, data: dict) -> bool:
    """Atomic write; returns False instead of raising on I/O failure."""
    try:
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def sessions_dir_for(agent_id: str) -> Path:
    return Path(os.environ.get(
        "OPENCLAW_SESSIONS_DIR",
        str(Path.home() / ".openclaw" / "agents" / agent_id / "sessions"),
    ))


def agent_id_for(session_key: str | None) -> str:
    """Resolve the owning agent id from an `agent:<id>:...` session key."""
    if session_key and session_key.startswith("agent:"):
        parts = session_key.split(":")
        if len(parts) > 1 and parts[1]:
            return parts[1]
    return AGENT_ID


def _block_valid(block: str) -> bool:
    """A finding block must carry every required field with a known severity."""
    fields: dict[str, str] = {}
    for line in block.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip().lower()] = value.strip()
    for field in REQUIRED_FIELDS:
        if not fields.get(field):
            return False
    return fields["severity"].lower() in SEVERITIES


def artifact_state(review_dir: Path, filename: str) -> tuple[str, dict | None]:
    """Return missing|valid|invalid and a stat observation."""
    if not filename:
        return "missing", None
    path = review_dir / filename
    try:
        if not path.is_file():
            return "missing", None
        stat = path.stat()
        obs = {"size": stat.st_size, "mtimeNs": stat.st_mtime_ns}
        raw = path.read_bytes()
    except OSError:
        # TOCTOU with the reviewer rewriting its file: observe again next poll.
        return "missing", None
    text = raw.decode("utf-8-sig", errors="ignore")
    # Normalize line endings so CRLF files and BOMs do not invalidate a review.
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return "invalid", obs
    blocks = FINDING_RE.findall(text)
    remainder = FINDING_RE.sub("", text).strip()
    # Stray markers count as truncation evidence only when they appear as
    # marker-only lines; summaries may legitimately mention the token inline.
    if any(MARKER_LINE_RE.match(line) for line in remainder.splitlines()):
        return "invalid", obs
    if blocks:
        return ("valid" if all(_block_valid(block) for block in blocks) else "invalid"), obs
    # Zero findings: the sentinel line (optionally followed by a summary) is valid;
    # anything else without blocks is a truncated/foreign file.
    if text == "NO FINDINGS" or text.startswith("NO FINDINGS\n"):
        return "valid", obs
    return "invalid", obs


def query_sessions(agent_id: str = AGENT_ID) -> dict:
    try:
        out = subprocess.run(
            [OPENCLAW, "sessions", "--json", "--limit", "all", "--agent", agent_id],
            capture_output=True, text=True, timeout=60,
        )
        if out.returncode != 0:
            raise RuntimeError(out.stderr.strip() or f"openclaw sessions exited {out.returncode}")
        payload = json.loads(out.stdout)
        sessions = payload.get("sessions", payload) if isinstance(payload, dict) else payload
        mapping: dict = {}

        def _activity(item: dict) -> int:
            return item.get("lastInteractionAt") or item.get("updatedAt") or item.get("sessionStartedAt") or 0

        for item in sessions:
            if isinstance(item, dict) and item.get("key"):
                key = item["key"]
                # Duplicate (recycled) keys: keep the freshest observation.
                if key not in mapping or _activity(item) >= _activity(mapping[key]):
                    mapping[key] = item
        return mapping
    except Exception as exc:
        return {"__error__": str(exc)}


def last_assistant_text(session: dict | None) -> str:
    """Diagnostic only; never replaces the required artifact. Reads only the file tail."""
    try:
        if not session:
            return ""
        raw = session.get("sessionFile")
        fallback_dir = sessions_dir_for(agent_id_for(session.get("key")))
        path = Path(raw) if raw else fallback_dir / f"{session.get('sessionId', '')}.jsonl"
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > DIAGNOSTIC_TAIL_BYTES:
                handle.seek(-DIAGNOSTIC_TAIL_BYTES, os.SEEK_END)
                # Drop the first (probably partial) line of the tail.
                handle.readline()
            data = handle.read()
        for line in reversed(data.decode("utf-8", errors="ignore").splitlines()):
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item.get("type") != "message":
                continue
            message = item.get("message", {})
            if message.get("role") != "assistant":
                continue
            content = message.get("content", "")
            if isinstance(content, str):
                return content.strip()
            if isinstance(content, list):
                return "\n".join(
                    part.get("text", "") for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                ).strip()
        return ""
    except OSError as exc:
        return f"unreadable: {exc}"


def stale_activity_ts(session: dict) -> int | None:
    """Best available activity timestamp; None when the session exposes none."""
    return (
        session.get("lastInteractionAt")
        or session.get("updatedAt")
        or session.get("sessionStartedAt")
        or None
    )


def classify(
    session: dict | None,
    review_dir: Path,
    entry: dict,
    previous: dict | None = None,
    *,
    clock_ms: int | None = None,
) -> tuple[str, dict | None]:
    """Classify one reviewer model.

    `previous` is the prior runtime entry for this sessionKey. Terminality that
    depends on stability (absent session, stalled running session) requires the
    same observation across two consecutive polls.
    """
    artifact, observation = artifact_state(review_dir, entry.get("file", ""))
    previous = previous or {}

    if session is None:
        # A structurally valid artifact alone is not enough: require it to have
        # been observed unchanged on the previous poll as well.
        if artifact == "valid" and previous.get("artifactObservation") == observation:
            return "done", observation
        return "unknown", observation

    status = session.get("status")
    if status != "running":
        return ("done" if artifact == "valid" else "lost"), observation

    activity = stale_activity_ts(session)
    current = now_ms() if clock_ms is None else clock_ms
    # Missing timestamps cannot confirm liveness: treat as stale and let the
    # two-consecutive-poll stability rule gate terminality.
    stale = activity is None or current - activity > STALL_THRESHOLD * 1000
    if not stale:
        return "active", observation
    # Terminality for a still-running session requires TWO consecutive stale
    # polls with an unchanged valid artifact (previous poll must also be stale).
    if (
        artifact == "valid"
        and previous.get("stale")
        and previous.get("artifactObservation") == observation
    ):
        return "stalled-with-artifact", observation
    return "hung", observation


def evaluate_round(
    config: dict,
    runtime: dict,
    sessions: dict,
    *,
    clock_ms: int | None = None,
) -> dict:
    current = now_ms() if clock_ms is None else clock_ms
    review_dir = Path(config["reviewDir"])
    old_by_key = {
        item.get("sessionKey"): item
        for item in runtime.get("models", [])
        if item.get("sessionKey")
    }
    models = []
    for source in config["models"]:
        entry = dict(source)
        old = old_by_key.get(entry["sessionKey"], {})
        session = sessions.get(entry["sessionKey"])
        if session is None:
            entry["diagnostic"] = "session key not found in sessions query"
        state, observation = classify(
            session, review_dir, entry, old,
            clock_ms=current,
        )
        activity = stale_activity_ts(session) if session else None
        entry["status"] = state
        entry["stale"] = bool(
            session and session.get("status") == "running"
            and (activity is None or current - activity > STALL_THRESHOLD * 1000)
        )
        entry["artifactObservation"] = observation
        if activity is not None:
            entry["lastActivityAt"] = activity
        if state == "lost":
            entry["diagnosticLastAssistant"] = last_assistant_text(session)
        models.append(entry)

    result = dict(runtime)
    result["models"] = models
    result["lastQueryAt"] = current
    losses = [item["model"] for item in models if item["status"] == "lost"]
    result["lostModels"] = losses
    result["lostCount"] = len(losses)

    if all(item["status"] in TERMINAL_MODEL_STATES for item in models):
        result["round"] = "complete-with-losses" if losses else "complete"
    elif current >= config["deadlineMs"]:
        result["round"] = "deadline"
    else:
        result["round"] = "monitoring"
    if result["round"] in TERMINAL_ROUNDS:
        result["completedAtMs"] = current
    return result


def wake_orchestrator(config: dict | None, state_path: Path, runtime: dict,
                      session_key: str | None = None) -> bool:
    key = session_key or (config or {}).get("orchestratorSessionKey")
    if not key:
        runtime["wakeDelivered"] = False
        runtime["wakeError"] = "no orchestratorSessionKey available"
        safe_save(state_path, runtime)
        return False
    message = f"Multireview babysitter {runtime.get('round', 'monitoring')}. Read {state_path}"
    last_error = ""
    for attempt in range(3):
        try:
            out = subprocess.run(
                [OPENCLAW, "system", "event", "--mode", "now",
                 "--session-key", key, "--text", message,
                 "--timeout", "30000"],
                capture_output=True, text=True, timeout=45,
            )
            if out.returncode == 0:
                runtime["wakeDelivered"] = True
                runtime["wakeAtMs"] = now_ms()
                runtime.pop("wakeError", None)
                safe_save(state_path, runtime)
                return True
            last_error = out.stderr.strip() or f"openclaw system event exited {out.returncode}"
        except Exception as exc:
            last_error = str(exc)
        if attempt < 2:
            time.sleep(5)
    runtime["wakeDelivered"] = False
    runtime["wakeError"] = last_error
    safe_save(state_path, runtime)
    return False


def validate_config(config: dict) -> None:
    for field in ("reviewDir", "deadlineMs", "orchestratorSessionKey", "models"):
        if not config.get(field):
            raise ValueError(f"pending-state missing {field}")
    if not isinstance(config["deadlineMs"], (int, float)) or isinstance(config["deadlineMs"], bool):
        raise ValueError("deadlineMs must be an epoch-milliseconds number")
    if not isinstance(config["models"], list):
        raise ValueError("models must be a list")
    review_dir = Path(config["reviewDir"])
    if not review_dir.is_dir():
        raise ValueError("reviewDir does not exist")
    seen_files: set[str] = set()
    seen_keys: set[str] = set()
    for item in config["models"]:
        if not isinstance(item, dict):
            raise ValueError("models entries must be objects")
        if not item.get("model") or not item.get("sessionKey") or not item.get("file"):
            raise ValueError("incomplete model entry")
        if item["file"] in seen_files:
            raise ValueError(f"duplicate artifact file: {item['file']}")
        if item["sessionKey"] in seen_keys:
            raise ValueError(f"duplicate sessionKey: {item['sessionKey']}")
        seen_files.add(item["file"])
        seen_keys.add(item["sessionKey"])
        artifact = (review_dir / item["file"]).resolve()
        try:
            artifact.relative_to(review_dir.resolve())
        except ValueError as exc:
            raise ValueError("artifact path escapes reviewDir") from exc


def _terminal(config: dict | None, state_path: Path, runtime: dict,
              fallback_key: str | None) -> int:
    """Record monitor-error, attempt the wake, and map it to an exit code."""
    runtime["round"] = "monitor-error"
    runtime["completedAtMs"] = now_ms()
    safe_save(state_path, runtime)
    key = fallback_key or (config or {}).get("orchestratorSessionKey")
    if not key:
        # Nothing to wake and no way to learn the key: exit 0 so systemd's
        # Restart=on-failure does not loop forever on an unrecoverable config.
        runtime["wakeDelivered"] = False
        runtime["wakeError"] = runtime.get("wakeError", "no orchestratorSessionKey available")
        safe_save(state_path, runtime)
        return 0
    return 0 if wake_orchestrator(config, state_path, runtime, fallback_key) else 1


def main_impl(config_path: Path, sleep_fn=time.sleep) -> int:
    """Watchdog loop; `sleep_fn` is injectable for tests."""
    state_path = runtime_path(config_path)
    lock_path = config_path.with_suffix(config_path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with lock_path.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("[babysitter] another instance already holds the lock", file=sys.stderr)
            return 3

        runtime: dict = {"babysitterPid": os.getpid(), "round": "monitoring", "models": []}
        print(f"[babysitter] config={config_path} runtime={state_path}", flush=True)
        config_errors = session_errors = write_errors = internal_errors = 0
        last_good_key: str | None = None
        while True:
            # Outer guard: any uncaught failure (evaluate_round, save) feeds the
            # bounded error policy instead of crash-looping under systemd.
            try:
                try:
                    config = load_json(config_path)
                    # Capture the wake key before validation: a config that
                    # parses but fails validation still tells us who to wake.
                    last_good_key = config.get("orchestratorSessionKey") or last_good_key
                    validate_config(config)
                    config_errors = 0
                    runtime.pop("configErrors", None)
                    runtime.pop("lastConfigError", None)
                except FileNotFoundError:
                    # Config gone (e.g. cleaned up after publication): clean stop.
                    runtime["round"] = "stopped-config-gone"
                    runtime["completedAtMs"] = now_ms()
                    safe_save(state_path, runtime)
                    return 0
                except Exception as exc:
                    config_errors += 1
                    runtime["configErrors"] = config_errors
                    runtime["lastConfigError"] = str(exc)
                    if not safe_save(state_path, runtime):
                        write_errors += 1
                    if config_errors >= MAX_CONSECUTIVE_ERRORS:
                        # Terminal: must wake, never silently restart-loop.
                        return _terminal(None, state_path, runtime, last_good_key)
                    sleep_fn(min(POLL_INTERVAL, 30))
                    continue

                sessions = query_sessions(agent_id_for(last_good_key or config.get("orchestratorSessionKey")))
                if "__error__" in sessions:
                    session_errors += 1
                    runtime["sessionQueryErrors"] = session_errors
                    runtime["lastSessionError"] = sessions["__error__"]
                    if not safe_save(state_path, runtime):
                        write_errors += 1
                    if session_errors >= MAX_CONSECUTIVE_ERRORS:
                        return _terminal(config, state_path, runtime, last_good_key)
                    sleep_fn(POLL_INTERVAL)
                    continue

                session_errors = 0
                runtime["sessionQueryErrors"] = 0
                runtime = evaluate_round(config, runtime, sessions)
                if not safe_save(state_path, runtime):
                    write_errors += 1
                    if write_errors >= MAX_CONSECUTIVE_ERRORS:
                        return _terminal(config, state_path, runtime, last_good_key)
                else:
                    write_errors = 0
                if runtime["round"] in TERMINAL_ROUNDS:
                    return 0 if wake_orchestrator(config, state_path, runtime) else 1
                # H2: the internal-error counter is a CONSECUTIVE-error policy;
                # a fully successful poll resets it like the other counters.
                internal_errors = 0
                runtime.pop("internalErrors", None)
                runtime.pop("lastInternalError", None)
            except Exception as exc:
                internal_errors += 1
                runtime["internalErrors"] = internal_errors
                runtime["lastInternalError"] = str(exc)
                if not safe_save(state_path, runtime):
                    write_errors += 1
                if internal_errors >= MAX_CONSECUTIVE_ERRORS or write_errors >= MAX_CONSECUTIVE_ERRORS:
                    return _terminal(config, state_path, runtime, last_good_key)
            sleep_fn(POLL_INTERVAL)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: review-babysitter.py <pending-state.json>", file=sys.stderr)
        return 2
    return main_impl(Path(sys.argv[1]).resolve())


if __name__ == "__main__":
    raise SystemExit(main())
