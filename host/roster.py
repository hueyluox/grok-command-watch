#!/usr/bin/env python3
"""Update ~/.grok/command-watch/roster.json from a Grok hook event on stdin."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home() / ".grok" / "command-watch"
SLOTS_PATH = HOME / "slots.json"
ROSTER_PATH = HOME / "roster.json"
PANES_PATH = HOME / "panes.json"
SESSION_DIR = Path.home() / ".grok" / "sessions"

SLOT_ORDER = ("1L", "1R", "2L", "2R", "3L", "3R", "4L", "4R")
COMPLETE_HOLD_S = 3.0


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: Path, data) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


_DIR_CACHE: dict[str, Path] = {}


def session_dir(session_id: str | None) -> Path | None:
    if not session_id:
        return None
    cached = _DIR_CACHE.get(session_id)
    if cached is not None and cached.is_dir():
        return cached
    for path in SESSION_DIR.rglob(session_id):
        if path.is_dir() and path.name == session_id:
            _DIR_CACHE[session_id] = path
            return path
    return None


def workflow_label(session_id: str | None) -> str | None:
    root = session_dir(session_id)
    if root is None:
        return None
    wf_root = root / "workflows"
    if not wf_root.is_dir():
        return None
    for state_path in wf_root.glob("*/state.json"):
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        st = data.get("state") if isinstance(data.get("state"), dict) else data
        status = str(st.get("status") or "").lower()
        if status not in ("active", "running", "pending", "paused"):
            continue
        name = st.get("name") or "workflow"
        phase = st.get("current_phase") or ""
        return ("WF " + " ".join(f"{name} {phase}".split()))[:24]
    return None


def session_hot(session_id: str | None, now: float, window: float = 45.0) -> bool:
    root = session_dir(session_id)
    if root is None:
        return False
    summary = root / "summary.json"
    stamp = 0.0
    if summary.exists():
        try:
            data = json.loads(summary.read_text(encoding="utf-8"))
            raw = data.get("last_active_at") or data.get("updated_at") or ""
            if raw:
                stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
        except (OSError, json.JSONDecodeError, ValueError):
            stamp = 0.0
        if stamp <= 0:
            stamp = summary.stat().st_mtime
    if stamp and (now - stamp) < window:
        return True
    return workflow_label(session_id) is not None


# updates.jsonl kinds while Grok is thinking / waiting / calling tools.
_BUSY_UPDATES = frozenset({
    "agent_thought_chunk",
    "agent_message_chunk",
    "user_message_chunk",
    "tool_call",
    "tool_call_update",
    "turn_started",
    "user_message",
})
_SETTLE_UPDATES = frozenset({"turn_completed"})
_UPD_CACHE: dict[str, tuple] = {}


def _event_ts(data: dict, upd: dict) -> float:
    raw = data.get("timestamp") or upd.get("timestamp")
    if isinstance(raw, (int, float)):
        ts = float(raw)
        if ts > 1e12:
            ts /= 1000.0
        return ts
    if raw:
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0
    return 0.0


def last_session_update(session_id: str | None) -> tuple[str | None, str | None, float]:
    """Latest busy/settle update: kind, prompt_id, timestamp."""
    root = session_dir(session_id)
    if root is None:
        return None, None, 0.0
    path = root / "updates.jsonl"
    if not path.exists():
        return None, None, 0.0
    try:
        st = path.stat()
    except OSError:
        return None, None, 0.0
    cache_key = (str(path), st.st_mtime, st.st_size)
    cached = _UPD_CACHE.get(session_id)
    if cached and cached[0] == cache_key:
        return cached[1], cached[2], cached[3]
    try:
        with path.open("rb") as fh:
            fh.seek(max(0, st.st_size - 262144))
            chunk = fh.read().decode("utf-8", "replace")
    except OSError:
        return None, None, 0.0
    last_kind = None
    last_prompt = None
    last_ts = 0.0
    for line in reversed(chunk.splitlines()):
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        upd = (data.get("params") or {}).get("update") or {}
        kind = upd.get("sessionUpdate")
        if kind == "hook_execution":
            ev = str(upd.get("event_name") or "").lower()
            if ev in ("user_prompt_submit", "pre_tool_use", "post_tool_use"):
                kind = ev
            else:
                continue
        if kind in _BUSY_UPDATES or kind in _SETTLE_UPDATES or kind in (
            "user_prompt_submit",
            "pre_tool_use",
            "post_tool_use",
        ):
            last_kind = kind
            last_prompt = upd.get("prompt_id")
            last_ts = _event_ts(data, upd)
            break
    _UPD_CACHE[session_id] = (cache_key, last_kind, last_prompt, last_ts)
    return last_kind, last_prompt, last_ts


def session_in_flight(
    session_id: str | None,
    prompt_id: str | None = None,
    prompt_at: float = 0,
) -> bool:
    """True while the TUI would show Waiting for response / streaming / tools."""
    kind, done_prompt, done_at = last_session_update(session_id)
    if kind in _BUSY_UPDATES or kind in (
        "user_prompt_submit",
        "pre_tool_use",
        "post_tool_use",
    ):
        return True
    if kind in _SETTLE_UPDATES:
        # New prompt submitted after the last completed turn, still waiting.
        if (
            prompt_id
            and done_prompt
            and str(prompt_id) != str(done_prompt)
            and float(prompt_at or 0) > done_at
        ):
            return True
        return False
    return False


def refresh_workflows(roster: dict) -> None:
    sync_from_panes(roster)
    for slot in roster["slots"].values():
        sid = slot.get("session_id")
        looping = session_has_loop(sid)
        if slot.get("state") == "needs_you":
            kind, _, _ = last_session_update(sid)
            if kind == "post_tool_use":
                slot["state"] = "loop" if looping else "running"
            else:
                continue
        if slot.get("state") == "error":
            continue
        if looping:
            slot["state"] = "loop"
            title = short_title(sid)
            if title:
                slot["title"] = title
            continue
        label = workflow_label(sid)
        if label:
            slot["state"] = "running"
            slot["title"] = label
            slot["bg"] = True
            continue
        slot["bg"] = False
        if session_in_flight(sid, slot.get("prompt_id"), slot.get("prompt_at") or 0):
            if slot.get("state") in ("empty", "idle", "complete"):
                slot["state"] = "running"
            title = short_title(sid)
            if title:
                slot["title"] = title
            continue
        # Turn is over. Finished sessions stay green until the next prompt.
        if sid and slot.get("prompt_id") and slot.get("state") in ("running", "idle"):
            slot["state"] = "complete"
            title = short_title(sid)
            if title:
                slot["title"] = title


def session_has_loop(session_id: str | None) -> bool:
    """True when this session has an active /loop (scheduler) sitting on it."""
    root = session_dir(session_id)
    if root is None:
        return False
    sub_root = root / "subagents"
    if sub_root.is_dir():
        for meta in sub_root.glob("*/meta.json"):
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            desc = str(data.get("description") or "")
            prompt = str(data.get("prompt") or "")
            if desc.lower().startswith("loop:") or "Scheduled task" in prompt:
                return True
    return False


def short_title(session_id: str | None) -> str:
    root = session_dir(session_id)
    if root is None:
        return ""
    summary = root / "summary.json"
    if not summary.exists():
        return ""
    try:
        data = json.loads(summary.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    text = data.get("last_turn_summary") or data.get("generated_title") or ""
    return " ".join(str(text).split())[:24]


def empty_slot(name: str) -> dict:
    return {
        "name": name,
        "pid": None,
        "session_id": None,
        "state": "empty",
        "title": "",
        "prompt_id": None,
        "prompt_at": 0,
        "complete_until": 0,
    }


def pid_alive(pid) -> bool:
    try:
        pid = int(pid)
        if pid <= 1:
            return False
        os.kill(pid, 0)
        return True
    except (TypeError, ValueError, OSError, ProcessLookupError):
        return False


def load_roster() -> dict:
    data = load_json(ROSTER_PATH, {})
    slots = data.get("slots") or {}
    out = {"updated_at": data.get("updated_at", 0), "slots": {}}
    for name in SLOT_ORDER:
        slot = slots.get(name) or empty_slot(name)
        slot["name"] = name
        out["slots"][name] = slot
    for name, slot in slots.items():
        if name in SLOT_ORDER:
            continue
        if not str(name).startswith("p"):
            continue
        item = dict(slot)
        item["name"] = name
        out["slots"][name] = item
    return out


def ensure_pid_slot(roster: dict, pid: int | None, session_id: str | None = None) -> dict | None:
    if not pid or int(pid) <= 1:
        return None
    pid = int(pid)
    found = find_slot(roster, session_id=session_id) or find_slot(roster, pid=pid)
    if found:
        found["pid"] = pid
        if session_id:
            found["session_id"] = session_id
        return found
    name = f"p{pid}"
    slot = roster["slots"].get(name) or empty_slot(name)
    slot["name"] = name
    slot["pid"] = pid
    if session_id:
        slot["session_id"] = session_id
    if slot.get("state") in (None, "empty"):
        slot["state"] = "idle"
    roster["slots"][name] = slot
    return slot


def sync_from_panes(roster: dict) -> None:
    panes = (load_json(PANES_PATH, {}) or {}).get("panes") or []
    live: set[int] = set()
    for pane in panes:
        pid = pane.get("pid")
        if not pid:
            continue
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            continue
        live.add(pid)
        slot = ensure_pid_slot(roster, pid)
        if slot and slot.get("state") == "empty":
            slot["state"] = "idle"
        if slot:
            slot.pop("gone_at", None)
    now = time.time()
    for name in list(roster["slots"]):
        if not str(name).startswith("p"):
            continue
        slot = roster["slots"][name]
        pid = slot.get("pid")
        if pid and int(pid) in live:
            slot.pop("gone_at", None)
            continue
        if pid_alive(pid):
            slot.pop("gone_at", None)
            continue
        gone = float(slot.get("gone_at") or 0)
        if gone <= 0:
            slot["gone_at"] = now
            continue
        if now - gone < 8:
            continue
        del roster["slots"][name]


def find_slot(roster: dict, *, pid=None, session_id=None) -> dict | None:
    for slot in roster["slots"].values():
        if session_id and slot.get("session_id") == session_id:
            return slot
        if pid and slot.get("pid") == pid:
            return slot
    return None


def ppid_of(pid: int) -> int:
    try:
        import subprocess
        out = subprocess.check_output(["ps", "-o", "ppid=", "-p", str(pid)], text=True).strip()
        return int(out)
    except (OSError, ValueError, subprocess.CalledProcessError):
        return 0


def resolve_slot(roster: dict, pid: int, session_id: str | None) -> dict | None:
    slot = find_slot(roster, session_id=session_id) or find_slot(roster, pid=pid)
    if slot:
        return slot
    seen = set()
    cur = pid
    while cur and cur > 1 and cur not in seen:
        seen.add(cur)
        slot = find_slot(roster, pid=cur) or bind_from_slots_file(roster, cur, session_id)
        if slot:
            return slot
        cur = ppid_of(cur)
    return ensure_pid_slot(roster, pid, session_id) or bind_from_slots_file(roster, pid, session_id)


def background_live(event: dict) -> list:
    tasks = event.get("backgroundTasks") or event.get("background_tasks") or []
    live = []
    for task in tasks:
        status = str(task.get("status") or "running").lower()
        if status in ("", "running", "in_progress", "active", "pending"):
            live.append(task)
    return live


def tool_name(event: dict) -> str:
    return str(event.get("toolName") or event.get("tool_name") or "")


def tool_input(event: dict) -> dict:
    raw = event.get("toolInput") or event.get("tool_input") or {}
    return raw if isinstance(raw, dict) else {}


def bind_from_slots_file(roster: dict, pid: int, session_id: str | None) -> dict | None:
    slots_file = load_json(SLOTS_PATH, {"slots": {}})
    raw = slots_file.get("slots") or {}
    for name in SLOT_ORDER:
        entry = raw.get(name) or {}
        if entry.get("pid") != pid:
            continue
        slot = roster["slots"][name]
        slot["pid"] = pid
        if session_id:
            slot["session_id"] = session_id
        if slot.get("state") in (None, "empty"):
            slot["state"] = "idle"
        slot["title"] = short_title(slot.get("session_id")) or slot.get("title") or ""
        return slot
    return None


def apply_event(event: dict) -> None:
    name = event.get("hookEventName") or event.get("hook_event_name") or ""
    sub = event.get("subagentType") or event.get("subagent_type")
    session_id = event.get("sessionId") or event.get("session_id")
    prompt_id = event.get("promptId") or event.get("prompt_id")
    grok_pid = os.getppid()
    roster = load_roster()
    now = time.time()

    slot = resolve_slot(roster, grok_pid, session_id)
    if name in ("session_start", "SessionStart") and not sub:
        slot = (
            bind_from_slots_file(roster, grok_pid, session_id)
            or slot
            or ensure_pid_slot(roster, grok_pid, session_id)
        )
        if slot:
            slot["pid"] = grok_pid
            slot["session_id"] = session_id
            slot["state"] = "idle"
            slot["prompt_id"] = None
            slot["title"] = short_title(session_id)
        roster["updated_at"] = now
        save_json(ROSTER_PATH, roster)
        return

    if slot is None:
        slot = ensure_pid_slot(roster, grok_pid, session_id)
    if slot is None:
        return

    tool = tool_name(event).lower()
    inp = tool_input(event)

    if name in ("user_prompt_submit", "UserPromptSubmit") and not sub:
        slot["state"] = "running"
        slot["prompt_id"] = prompt_id
        slot["prompt_at"] = now
        slot["session_id"] = session_id or slot.get("session_id")
        slot["title"] = short_title(slot.get("session_id"))
    elif name in ("pre_tool_use", "PreToolUse"):
        slot["state"] = "running"
        if tool in ("workflow", "spawn_subagent"):
            label = inp.get("name") or inp.get("description") or inp.get("prompt") or tool
            slot["title"] = ("WF " + " ".join(str(label).split()))[:24]
            slot["bg"] = True
        else:
            slot["title"] = short_title(slot.get("session_id")) or slot.get("title") or ""
    elif name in ("post_tool_use", "PostToolUse"):
        slot["state"] = "running"
        if tool == "workflow":
            label = inp.get("name") or slot.get("title") or "workflow"
            slot["title"] = ("WF " + " ".join(str(label).split()))[:24]
            slot["bg"] = True
    elif name in ("subagent_start", "SubagentStart"):
        slot["state"] = "running"
        if not (slot.get("title") or "").startswith("WF "):
            slot["title"] = (str(sub or "agent") + " " + (slot.get("title") or ""))[:24]
    elif name in ("subagent_stop", "SubagentStop", "SubagentEnd"):
        # Child finished; parent may still have a workflow running.
        pass
    elif name in ("stop", "Stop"):
        # A turn ending is not the session going idle. Grok can still be
        # responding or running a workflow. Only idle_prompt / SessionEnd settle.
        if sub:
            pass
        elif background_live(event) or (event.get("sessionCrons") or event.get("session_crons")) or workflow_label(slot.get("session_id")):
            slot["state"] = "running"
            slot["bg"] = True
            live = background_live(event)
            if live:
                desc = live[0].get("description") or live[0].get("command") or live[0].get("type") or "bg"
                slot["title"] = ("WF " + " ".join(str(desc).split()))[:24]
        elif session_hot(slot.get("session_id"), now) or workflow_label(slot.get("session_id")):
            slot["state"] = "running"
        # Do not mark complete here. Tool rounds look idle for tens of seconds
        # while Grok is still responding. idle_prompt / SessionEnd settle.
    elif name in ("stop_failure", "StopFailure"):
        if not sub:
            slot["state"] = "error"
            slot["title"] = short_title(slot.get("session_id"))
    elif name in ("stop_cancelled", "StopCancelled"):
        if not sub:
            slot["state"] = "idle"
            slot["prompt_id"] = None
    elif name in ("notification", "Notification"):
        ntype = event.get("notificationType") or event.get("notification_type") or ""
        if ntype == "permission_prompt":
            slot["state"] = "needs_you"
        elif ntype in ("idle_prompt", "task_complete"):
            if workflow_label(slot.get("session_id")) or session_in_flight(
                slot.get("session_id"),
                slot.get("prompt_id"),
                slot.get("prompt_at") or 0,
            ):
                slot["state"] = "running"
            else:
                slot["state"] = "complete"
    elif name in ("session_end", "SessionEnd"):
        if not sub:
            slot.update(empty_slot(slot["name"]))

    refresh_workflows(roster)
    roster["updated_at"] = now
    save_json(ROSTER_PATH, roster)


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--refresh":
        roster = load_roster()
        refresh_workflows(roster)
        roster["updated_at"] = time.time()
        save_json(ROSTER_PATH, roster)
        return 0
    raw = sys.stdin.read()
    if not raw.strip():
        return 0
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        return 0
    apply_event(event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
