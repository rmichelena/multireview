#!/usr/bin/env python3
"""Detached completion watchdog for the multireview skill."""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path

def _env_int(name: str, default: int, minimum: int | None = None) -> int:
    """Parse an int env var defensively (r5 L2): a malformed value must not
    crash at import into an unlogged systemd restart loop. Out-of-bounds
    values fall back to the default too (r6 M3): sleep(-1) raises outside
    the guarded loop and systemd would restart-loop on it."""
    raw = (os.environ.get(name) or "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        print(f"[babysitter] invalid {name}={raw!r}; using default {default}",
              file=sys.stderr)
        return default
    if minimum is not None and value < minimum:
        print(f"[babysitter] {name}={value} below minimum {minimum}; using default {default}",
              file=sys.stderr)
        return default
    return value


POLL_INTERVAL = _env_int("BABYSITTER_POLL_INTERVAL", 300, minimum=1)
STALL_THRESHOLD = _env_int("BABYSITTER_STALL_THRESHOLD", 900, minimum=1)
OPENCLAW = os.environ.get("OPENCLAW_BIN", "openclaw")
AGENT_ID = os.environ.get("OPENCLAW_AGENT_ID", "main")
TERMINAL_MODEL_STATES = {"done", "lost", "stalled-with-artifact"}
TERMINAL_ROUNDS = {"complete", "complete-with-losses", "deadline", "monitor-error"}
MARKER_WS = r"[ \t]*"
FINDING_RE = re.compile(
    r"===FINDING===" + MARKER_WS + r"\n.*?\n" + MARKER_WS + r"===END_FINDING===",
    re.S,
)
FENCE_RE = re.compile(r"^```.*?^```", re.S | re.M)
MARKER_LINE_RE = re.compile(r"^\s*===(?:FINDING|END_FINDING)===\s*$")
REQUIRED_FIELDS = ("severity", "title", "file", "line", "reasoning", "fix", "trace")
TEMPLATE_PLACEHOLDERS = {
    "<one line title>", "<path>", "<line_number>",
    # r6 L1: keep in sync with the exact strings emitted by the SKILL.md
    # reviewer task templates, or parroted placeholders pass validation.
    "<what the code does, why it's wrong, what triggers it>",
    "<concrete fix suggestion>", "<concrete trace for logic claims, or N/A>",
}
SEVERITIES = {"critical", "high", "medium", "low"}
MAX_CONSECUTIVE_ERRORS = 3
KNOWN_TERMINAL_STATUSES = {
    "done", "complete", "failed", "error", "killed", "aborted",
    "cancelled", "canceled", "archived", "pruned",
}
DIAGNOSTIC_TAIL_BYTES = 256 * 1024


def config_fingerprint(config: dict) -> str:
    """Identity of the round a runtime file belongs to (r5 H1): the same
    review directory is reused across rounds, so a persisted terminal runtime
    must never be mistaken for a restart of the CURRENT round."""
    payload = json.dumps({
        "reviewDir": config.get("reviewDir"),
        "deadlineMs": config.get("deadlineMs"),
        "models": [
            {"sessionKey": item.get("sessionKey"), "file": item.get("file")}
            for item in config.get("models", []) if isinstance(item, dict)
        ],
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


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


def transcript_obs(session: dict) -> dict | None:
    """Observe the session transcript file (size + mtime) for liveness."""
    try:
        raw = session.get("sessionFile")
        fallback_dir = sessions_dir_for(agent_id_for(session.get("key")))
        path = Path(raw) if raw else fallback_dir / f"{session.get('sessionId', '')}.jsonl"
        stat = path.stat()
        return {"size": stat.st_size, "mtimeNs": stat.st_mtime_ns}
    except OSError:
        return None


def _block_valid(block: str) -> bool:
    """A finding block must carry every required field with a known severity.

    Template placeholder values (`<one line title>`) do not count as content:
    a reviewer parroting the task template carries zero information.
    """
    fields: dict[str, str] = {}
    for line in block.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip().lower()] = value.strip()
    for field in REQUIRED_FIELDS:
        value = fields.get(field)
        if not value or value.lower() in TEMPLATE_PLACEHOLDERS:
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
    # A summary that re-quotes the finding template inside a fenced code block
    # is legitimate prose, not truncated markers: strip fences first (r5 L1).
    stripped = FENCE_RE.sub("", text)
    if not stripped:
        # The whole artifact was fenced (r6 L4): a complete findings file
        # wrapped in a code fence is still a review. Validate from the
        # original text instead of discarding it.
        fenced_blocks = FINDING_RE.findall(text)
        if fenced_blocks and all(_block_valid(b) for b in fenced_blocks):
            return "valid", obs
        return "invalid", obs
    blocks = FINDING_RE.findall(stripped)
    remainder = FINDING_RE.sub("", stripped).strip()
    # Stray markers count as truncation evidence only when they appear as
    # marker-only lines; summaries may legitimately mention the token inline.
    if any(MARKER_LINE_RE.match(line) for line in remainder.splitlines()):
        return "invalid", obs
    if blocks:
        return ("valid" if all(_block_valid(block) for block in blocks) else "invalid"), obs
    # Zero findings: the sentinel line (optionally followed by a summary) is valid;
    # anything else without blocks is a truncated/foreign file.
    if stripped == "NO FINDINGS" or stripped.startswith("NO FINDINGS\n"):
        return "valid", obs
    # r6 L4: no blocks survive fence-stripping, but the original text may
    # still carry a complete, fully-fenced findings file.
    fenced_blocks = FINDING_RE.findall(text)
    if fenced_blocks and all(_block_valid(b) for b in fenced_blocks):
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
        sessions = payload.get("sessions") if isinstance(payload, dict) else payload
        if not isinstance(sessions, list):
            # A JSON object without a sessions array is a malformed envelope,
            # not an empty review: route it through the error sentinel instead
            # of silently treating every reviewer session as absent.
            raise RuntimeError("unexpected sessions payload shape")
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
        # Absence must itself be stable: the session must have been absent on
        # the previous poll too. A transiently partial sessions listing must
        # not shortcut a running reviewer to "done" (r4 M2).
        if (
            artifact == "valid"
            and previous.get("sessionAbsent")
            and previous.get("artifactObservation") == observation
        ):
            return "done", observation
        return "unknown", observation

    status = session.get("status")
    if status != "running":
        if status in KNOWN_TERMINAL_STATUSES:
            return ("done" if artifact == "valid" else "lost"), observation
        # Unknown or MISSING status: a schema extension or a not-yet-started
        # session is not evidence of exit (r5 M3). Non-terminal; the deadline
        # remains the backstop.
        return "unknown", observation

    activity = stale_activity_ts(session)
    current = now_ms() if clock_ms is None else clock_ms
    # Session-store timestamps only advance at turn boundaries, so they freeze
    # mid-turn and age purely with wall clock (r4 H2). Transcript growth is the
    # real liveness signal for a still-running session.
    stale = activity is None or current - activity > STALL_THRESHOLD * 1000
    transcript = transcript_obs(session)
    prev_transcript = previous.get("transcriptObservation")
    growing = (
        transcript is not None
        and prev_transcript is not None
        and prev_transcript != transcript
    )
    if not stale or growing:
        return "active", observation
    # Terminality for a still-running session requires TWO consecutive stale
    # polls, an unchanged valid artifact, AND a transcript that did not grow
    # between them. Without transcript evidence of a stall, stay non-terminal:
    # the deadline remains the backstop.
    if (
        artifact == "valid"
        and transcript is not None
        and prev_transcript == transcript
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
    query_failed: bool = False,
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
        if query_failed:
            # r5 M1 + r6 M5: a failed sessions query means UNQUERYABLE, not
            # absent. Hold the last known status; only a valid artifact that
            # stays unchanged across TWO consecutive query-failed polls can
            # terminalize (artifact-aware fallback). A single unqueryable
            # poll never promotes anything.
            state, observation = artifact_state(review_dir, entry.get("file", ""))
            failed_streak = int(old.get("queryFailedStreak") or 0) + 1
            old_status = old.get("status", "unknown")
            if (state == "valid"
                    and failed_streak >= 2
                    and old.get("artifactObservation") == observation
                    and old_status in TERMINAL_MODEL_STATES | {"active", "hung"}):
                entry["status"] = "done"
                entry["diagnostic"] = ("session query failed; stable valid "
                                       "artifact across 2 polls")
            else:
                entry["status"] = old_status
                entry["diagnostic"] = "session query failed; holding last status"
            entry["queryFailedStreak"] = failed_streak
            entry["sessionAbsent"] = False
            entry["stale"] = bool(old.get("stale"))
            entry["artifactObservation"] = observation
            entry["transcriptObservation"] = old.get("transcriptObservation")
            models.append(entry)
            continue
        if session is None:
            entry["diagnostic"] = "session key not found in sessions query"
        state, observation = classify(
            session, review_dir, entry, old,
            clock_ms=current,
        )
        activity = stale_activity_ts(session) if session else None
        transcript = transcript_obs(session) if session else None
        entry["status"] = state
        entry["sessionAbsent"] = session is None
        if session is not None:
            entry["rawStatus"] = session.get("status")
        entry["stale"] = bool(
            session and session.get("status") == "running"
            and (activity is None or current - activity > STALL_THRESHOLD * 1000)
        )
        entry.pop("queryFailedStreak", None)
        entry["artifactObservation"] = observation
        entry["transcriptObservation"] = transcript
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
                      session_key: str | None = None,
                      sleep_fn=time.sleep) -> bool:
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
            sleep_fn(5)
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
    if not math.isfinite(config["deadlineMs"]):
        # NaN/Infinity parse from JSON and defeat `current >= deadlineMs`
        # forever, disabling the deadline backstop (r6 M2).
        raise ValueError("deadlineMs must be a finite epoch-milliseconds number")
    if not isinstance(config["models"], list) or not config["models"]:
        # An empty panel would satisfy all([]) and report a "successful"
        # zero-reviewer round; that must be a config error, never a round (M1).
        raise ValueError("models must be a non-empty list")
    if not os.path.isabs(config["reviewDir"]):
        # Under systemd-run the CWD is $HOME: a relative reviewDir silently
        # reads the wrong tree and every artifact reports missing (r5 L7).
        raise ValueError("reviewDir must be an absolute path")
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
        if not str(item["sessionKey"]).startswith("agent:"):
            # A key of any other shape silently resolved to the default agent
            # and the reviewer was never found in its listing (r6 M4).
            raise ValueError("sessionKey must be an 'agent:<id>:...' key")
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
            # A running peer is the desired steady state, not a failure: exit 0
            # so systemd Restart=on-failure does not loop on duplicate starts.
            return 0

        # Restart continuity (r4 M3 / r5 H1): seed from the persisted runtime
        # so per-model observations and stability history survive a restart.
        # NOTE: consecutive-error counters always restart at zero; only model
        # observations are seeded.
        seeded: dict = {}
        try:
            if state_path.exists():
                seeded = load_json(state_path)
        except Exception:
            seeded = {}
        current_fp: str | None = None
        try:
            current_fp = config_fingerprint(load_json(config_path))
        except Exception:
            current_fp = None
        if (seeded.get("round") in TERMINAL_ROUNDS
                and seeded.get("configFingerprint") == current_fp
                and current_fp is not None):
            # Restart after a terminal record of THIS round (fingerprint
            # match): retry the wake instead of re-monitoring and erasing it.
            # A stale terminal runtime from an EARLIER round in the same
            # review dir falls through and monitors fresh (r5 H1).
            seeded["babysitterPid"] = os.getpid()
            print(f"[babysitter] terminal state {seeded['round']} on restart; retrying wake",
                  flush=True)
            wake_key: str | None = None
            try:
                wake_key = load_json(config_path).get("orchestratorSessionKey")
            except Exception:
                pass
            if not wake_key:
                # No key, no progress: exit 0 (see _terminal) to avoid looping.
                return 0
            return 0 if wake_orchestrator(None, state_path, seeded, wake_key) else 1
        runtime: dict = {**seeded, "babysitterPid": os.getpid()}
        runtime.setdefault("round", "monitoring")
        if seeded.get("round") in TERMINAL_ROUNDS:
            # Terminal record from a PREVIOUS round: do not carry it (or its
            # stale observations) into this round (r5 H1).
            runtime = {"babysitterPid": os.getpid(), "round": "monitoring",
                       "models": [], "staleTerminalFrom": seeded.get("round")}
        elif (seeded.get("models")
              and current_fp is not None
              and seeded.get("configFingerprint") != current_fp):
            # r6 M1: a NON-terminal runtime from a different round must not
            # lend its two-poll stability evidence (sessionAbsent, artifact /
            # transcript observations) to the replacement round.
            runtime["models"] = []
            runtime["staleObservationsFrom"] = seeded.get("configFingerprint")
            runtime.pop("configFingerprint", None)
        print(f"[babysitter] config={config_path} runtime={state_path}", flush=True)
        config_errors = session_errors = write_errors = internal_errors = 0
        last_good_key: str | None = None
        saw_config = False
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
                    saw_config = True
                    runtime["configFingerprint"] = config_fingerprint(config)
                    config_errors = 0
                    runtime.pop("configErrors", None)
                    runtime.pop("lastConfigError", None)
                except FileNotFoundError:
                    if saw_config:
                        # Config gone after a successful load (e.g. cleaned up
                        # after publication): clean stop.
                        runtime["round"] = "stopped-config-gone"
                        runtime["completedAtMs"] = now_ms()
                        safe_save(state_path, runtime)
                        return 0
                    # Never seen the config (ordering hiccup, wrong path):
                    # treat as a config error so the bounded policy — and the
                    # terminal wake — still get a chance (r5 M2).
                    config_errors += 1
                    runtime["configErrors"] = config_errors
                    runtime["lastConfigError"] = "pending-state.json not found (never loaded)"
                    if not safe_save(state_path, runtime):
                        write_errors += 1
                    else:
                        write_errors = 0
                    if config_errors >= MAX_CONSECUTIVE_ERRORS:
                        return _terminal(None, state_path, runtime, last_good_key)
                    sleep_fn(min(POLL_INTERVAL, 30))
                    continue
                except Exception as exc:
                    config_errors += 1
                    runtime["configErrors"] = config_errors
                    runtime["lastConfigError"] = str(exc)
                    if not safe_save(state_path, runtime):
                        write_errors += 1
                    else:
                        write_errors = 0
                    if config_errors >= MAX_CONSECUTIVE_ERRORS:
                        # Terminal: must wake, never silently restart-loop.
                        return _terminal(None, state_path, runtime, last_good_key)
                    sleep_fn(min(POLL_INTERVAL, 30))
                    continue

                # Query per distinct agent id derived from the model entries
                # themselves (r5 L3): no silent fallback to a possibly-wrong
                # default agent; queried ids recorded for diagnosis.
                agent_ids = sorted({agent_id_for(item.get("sessionKey"))
                                    for item in config.get("models", [])})
                runtime["agentIdsQueried"] = agent_ids
                merged_sessions: dict = {}
                sessions: dict = {}
                for aid in agent_ids:
                    part = query_sessions(aid)
                    if "__error__" in part:
                        sessions = part
                        break
                    merged_sessions.update(part)
                else:
                    # A successful but EMPTY listing is genuine absence (r6
                    # L2): e.g. every reviewer session was pruned out of the
                    # CLI listing. The absent-session two-poll gate classifies
                    # it. Malformed envelopes and CLI failures still route
                    # through __error__ inside query_sessions (r5 M4).
                    sessions = merged_sessions
                if "__error__" in sessions:
                    session_errors += 1
                    runtime["sessionQueryErrors"] = session_errors
                    runtime["lastSessionError"] = sessions["__error__"]
                    # Artifact-aware fallback (r6 M5): while the sessions CLI
                    # is down, a valid artifact stable across two consecutive
                    # query-failed polls still terminalizes. The flag records
                    # polls that ran without a session query.
                    runtime["sessionQueryFallback"] = True
                    runtime = evaluate_round(config, runtime, {}, query_failed=True)
                    if not safe_save(state_path, runtime):
                        write_errors += 1
                    else:
                        write_errors = 0
                    if runtime["round"] in TERMINAL_ROUNDS:
                        return 0 if wake_orchestrator(config, state_path, runtime) else 1
                    # Session-query failures are NON-terminal (r6 M7): a slow
                    # or failing listing is infrastructure, not a dead round.
                    # The deadline remains the backstop; the counters and
                    # lastSessionError diagnose it for the orchestrator.
                    sleep_fn(min(POLL_INTERVAL, 30))
                    continue

                session_errors = 0
                runtime["sessionQueryErrors"] = 0
                runtime.pop("lastSessionError", None)
                runtime.pop("sessionQueryFallback", None)
                runtime = evaluate_round(config, runtime, sessions)
                if not safe_save(state_path, runtime):
                    write_errors += 1
                else:
                    write_errors = 0
                if write_errors >= MAX_CONSECUTIVE_ERRORS:
                    return _terminal(config, state_path, runtime, last_good_key)
                if runtime["round"] in TERMINAL_ROUNDS:
                    return 0 if wake_orchestrator(config, state_path, runtime,
                                                  sleep_fn=sleep_fn) else 1
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
                else:
                    write_errors = 0
                if internal_errors >= MAX_CONSECUTIVE_ERRORS or write_errors >= MAX_CONSECUTIVE_ERRORS:
                    return _terminal(config, state_path, runtime, last_good_key)
            sleep_fn(POLL_INTERVAL)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: review-babysitter.py <pending-state.json>", file=sys.stderr)
        # A deterministic invocation error cannot be fixed by a restart: exit 0
        # so systemd Restart=on-failure does not loop forever (H1).
        return 0
    return main_impl(Path(sys.argv[1]).resolve())


if __name__ == "__main__":
    raise SystemExit(main())
