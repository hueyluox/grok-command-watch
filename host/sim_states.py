#!/usr/bin/env python3
"""Simulate watch pad colors. Isolated — does not touch live roster.json."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ROSTER_SRC = ROOT / "roster.py"

WATCH = ["1L", "2L", "3L", "4L"]
COLOR = {
    "empty": "隐",
    "idle": "灰",
    "running": "蓝RUN",
    "needs_you": "黄WAIT",
    "complete": "绿DONE",
    "error": "红ERR",
}
SNAP = {
    "empty": 0,
    "idle": 1,
    "running": 2,
    "needs_you": 3,
    "complete": 4,
    "error": 5,
}


def load_roster_mod(tmp: Path):
    spec = importlib.util.spec_from_file_location("roster_sim", ROSTER_SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.HOME = tmp
    mod.ROSTER_PATH = tmp / "roster.json"
    mod.SLOTS_PATH = tmp / "slots.json"
    mod.PANES_PATH = tmp / "panes.json"
    mod.SESSION_DIR = tmp / "sessions"
    mod.SESSION_DIR.mkdir()
    return mod


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def append_updates(root: Path, rows: list[dict]) -> None:
    path = root / "updates.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def upd(kind: str, prompt: str | None = None, **extra) -> dict:
    update = {"sessionUpdate": kind, **extra}
    if prompt:
        update["prompt_id"] = prompt
    return {"timestamp": 1.0, "params": {"update": update}}


def hook(event: str, prompt: str | None = None) -> dict:
    extra = {"event_name": event}
    return upd("hook_execution", prompt, **extra)


def session_root(mod, sid: str) -> Path:
    root = mod.SESSION_DIR / sid
    root.mkdir(parents=True, exist_ok=True)
    return root


def seed_session(mod, sid: str, title: str = "demo title") -> Path:
    root = session_root(mod, sid)
    write_json(
        root / "summary.json",
        {
            "last_turn_summary": title,
            "generated_title": title,
            "last_active_at": "2020-01-01T00:00:00+00:00",
        },
    )
    return root


def seed_workflow(root: Path, status: str, name: str = "deep-research") -> None:
    write_json(
        root / "workflows" / "wf_sim" / "state.json",
        {"state": {"status": status, "name": name, "current_phase": "Research"}},
    )


def empty_roster(mod) -> dict:
    return {
        "updated_at": 0,
        "slots": {name: mod.empty_slot(name) for name in mod.SLOT_ORDER},
    }


def bind(mod, roster, name: str, sid: str | None, pid: int = 1) -> dict:
    slot = roster["slots"][name]
    slot["pid"] = pid
    slot["session_id"] = sid
    return slot


def apply(mod, roster, event: dict, pid: int) -> dict:
    old_ppid = os.getppid
    os.getppid = lambda: pid  # type: ignore[method-assign]
    try:
        write_json(mod.ROSTER_PATH, roster)
        mod.apply_event(event)
        return json.loads(mod.ROSTER_PATH.read_text())
    finally:
        os.getppid = old_ppid  # type: ignore[method-assign]


def refresh(mod, roster) -> dict:
    write_json(mod.ROSTER_PATH, roster)
    rc = mod.main.__wrapped__ if hasattr(mod.main, "__wrapped__") else None
    roster2 = json.loads(mod.ROSTER_PATH.read_text()) if False else roster
    mod.refresh_workflows(roster)
    return roster


def snap(roster: dict, alive: set[int]) -> list[str]:
    out = []
    for name in WATCH:
        slot = roster["slots"][name]
        pid = slot.get("pid")
        if not pid or pid not in alive:
            out.append("empty")
            continue
        raw = slot.get("state") or "empty"
        if raw == "empty":
            out.append("idle")
        else:
            out.append(raw)
    return out


def face(states: list[str]) -> str:
    parts = []
    for i, st in enumerate(states, 1):
        parts.append(f"{i}={COLOR[st]}")
    return "  ".join(parts)


class Suite:
    def __init__(self):
        self.ok = 0
        self.fail = 0
        self.rows: list[str] = []

    def check(self, name: str, got, expect, extra: str = "") -> None:
        if got == expect:
            self.ok += 1
            mark = "OK"
        else:
            self.fail += 1
            mark = "FAIL"
        self.rows.append(f"{mark:4} {name:36} expect={expect} got={got} {extra}".rstrip())

    def check_pad(self, name: str, roster, alive, expect: list[str]) -> None:
        states = snap(roster, alive)
        self.check(name, states, expect, face(states))


def run() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="cw-sim-"))
    mod = load_roster_mod(tmp)
    s = Suite()
    alive = {11, 22, 33, 44}

    # 1. 刚开会话，还没说话
    r = empty_roster(mod)
    seed_session(mod, "s-idle")
    bind(mod, r, "1L", "s-idle", 11)
    r["slots"]["1L"]["state"] = "idle"
    refresh(mod, r)
    s.check_pad("新开未说话", r, alive, ["idle", "empty", "empty", "empty"])

    # 2. 空格隐藏
    r = empty_roster(mod)
    s.check_pad("四格都空", r, alive, ["empty", "empty", "empty", "empty"])

    # 3. 提交后、首 token 前：旧 turn_completed + 新 prompt_id
    r = empty_roster(mod)
    root = seed_session(mod, "s-wait")
    append_updates(root, [upd("turn_completed", "p1")])
    bind(mod, r, "1L", "s-wait", 11)
    r["slots"]["1L"]["state"] = "complete"
    r["slots"]["1L"]["prompt_id"] = "p1"
    refresh(mod, r)
    s.check("完成后刷新仍绿", r["slots"]["1L"]["state"], "complete")
    r["slots"]["1L"]["prompt_id"] = "p2"
    r["slots"]["1L"]["prompt_at"] = 100.0
    r["slots"]["1L"]["state"] = "running"
    refresh(mod, r)
    s.check("等回复(新prompt旧completed)", r["slots"]["1L"]["state"], "running")

    # 4. user_message_chunk / 思考 / 工具
    r = empty_roster(mod)
    root = seed_session(mod, "s-run")
    append_updates(root, [upd("user_message_chunk", "p3"), upd("agent_thought_chunk", "p3")])
    bind(mod, r, "1L", "s-run", 11)
    r["slots"]["1L"].update(state="idle", prompt_id="p3")
    refresh(mod, r)
    s.check("思考中", r["slots"]["1L"]["state"], "running")
    append_updates(root, [upd("tool_call", "p3"), upd("tool_call_update", "p3")])
    r["slots"]["1L"]["state"] = "idle"
    refresh(mod, r)
    s.check("工具中", r["slots"]["1L"]["state"], "running")

    # 5. Stop 夹在工具之间（没有 turn_completed）
    r = empty_roster(mod)
    root = seed_session(mod, "s-midstop")
    append_updates(root, [upd("tool_call_update", "p4"), hook("stop", "p4")])
    bind(mod, r, "1L", "s-midstop", 11)
    r["slots"]["1L"].update(state="running", prompt_id="p4")
    write_json(mod.SLOTS_PATH, {"slots": {"1L": {"pid": 11}}})
    r = apply(
        mod,
        r,
        {"hookEventName": "Stop", "sessionId": "s-midstop", "promptId": "p4"},
        11,
    )
    s.check("Stop夹在工具间", r["slots"]["1L"]["state"], "running")

    # 6. 回合结束 → 绿
    r = empty_roster(mod)
    root = seed_session(mod, "s-done")
    append_updates(root, [upd("agent_message_chunk", "p5"), upd("turn_completed", "p5")])
    bind(mod, r, "1L", "s-done", 11)
    r["slots"]["1L"].update(state="running", prompt_id="p5", bg=True)
    refresh(mod, r)
    s.check("回合结束去绿", r["slots"]["1L"]["state"], "complete")
    s.check("清掉残留bg", r["slots"]["1L"].get("bg"), False)

    # 7. idle_prompt / task_complete 都绿
    r = empty_roster(mod)
    root = seed_session(mod, "s-idleprompt")
    append_updates(root, [upd("turn_completed", "p6")])
    bind(mod, r, "1L", "s-idleprompt", 11)
    r["slots"]["1L"].update(state="running", prompt_id="p6")
    write_json(mod.SLOTS_PATH, {"slots": {"1L": {"pid": 11}}})
    r = apply(
        mod,
        r,
        {
            "hookEventName": "Notification",
            "notificationType": "idle_prompt",
            "sessionId": "s-idleprompt",
            "promptId": "p6",
        },
        11,
    )
    s.check("idle_prompt→绿", r["slots"]["1L"]["state"], "complete")
    r["slots"]["1L"]["state"] = "running"
    write_json(mod.ROSTER_PATH, r)
    r = apply(
        mod,
        r,
        {
            "hookEventName": "Notification",
            "notificationType": "task_complete",
            "sessionId": "s-idleprompt",
            "promptId": "p6",
        },
        11,
    )
    s.check("task_complete→绿", r["slots"]["1L"]["state"], "complete")

    # 8. 完成后 recap/flush 不能拉回 RUN
    r = empty_roster(mod)
    root = seed_session(mod, "s-recap")
    append_updates(
        root,
        [
            upd("turn_completed", "p7"),
            upd("memory_flush_started"),
            upd("memory_flush_completed"),
            upd("session_recap"),
        ],
    )
    bind(mod, r, "1L", "s-recap", 11)
    r["slots"]["1L"].update(state="complete", prompt_id="p7")
    refresh(mod, r)
    s.check("recap后仍绿", r["slots"]["1L"]["state"], "complete")

    # 9. 活动 workflow → RUN；完成后绿
    r = empty_roster(mod)
    root = seed_session(mod, "s-wf")
    seed_workflow(root, "running")
    append_updates(root, [upd("turn_completed", "p8")])
    bind(mod, r, "1L", "s-wf", 11)
    r["slots"]["1L"].update(state="complete", prompt_id="p8")
    refresh(mod, r)
    s.check("workflow进行中", r["slots"]["1L"]["state"], "running")
    seed_workflow(root, "complete")
    refresh(mod, r)
    s.check("workflow结束", r["slots"]["1L"]["state"], "complete")

    # 10. 权限黄，refresh 不能冲掉
    r = empty_roster(mod)
    root = seed_session(mod, "s-perm")
    append_updates(root, [upd("tool_call", "p9")])
    bind(mod, r, "1L", "s-perm", 11)
    r["slots"]["1L"].update(state="running", prompt_id="p9")
    write_json(mod.SLOTS_PATH, {"slots": {"1L": {"pid": 11}}})
    r = apply(
        mod,
        r,
        {
            "hookEventName": "Notification",
            "notificationType": "permission_prompt",
            "sessionId": "s-perm",
            "promptId": "p9",
        },
        11,
    )
    s.check("权限黄", r["slots"]["1L"]["state"], "needs_you")
    refresh(mod, r)
    s.check("权限黄不被refresh冲掉", r["slots"]["1L"]["state"], "needs_you")

    # 11. 失败红，refresh 不能冲成绿
    r = empty_roster(mod)
    root = seed_session(mod, "s-err")
    append_updates(root, [upd("turn_completed", "p10")])
    bind(mod, r, "1L", "s-err", 11)
    r["slots"]["1L"].update(state="running", prompt_id="p10")
    write_json(mod.SLOTS_PATH, {"slots": {"1L": {"pid": 11}}})
    r = apply(
        mod,
        r,
        {"hookEventName": "StopFailure", "sessionId": "s-err", "promptId": "p10"},
        11,
    )
    s.check("失败红", r["slots"]["1L"]["state"], "error")
    refresh(mod, r)
    s.check("失败红不被刷绿", r["slots"]["1L"]["state"], "error")

    # 12. 取消 → 灰
    r = empty_roster(mod)
    root = seed_session(mod, "s-cancel")
    bind(mod, r, "1L", "s-cancel", 11)
    r["slots"]["1L"].update(state="running", prompt_id="p11")
    write_json(mod.SLOTS_PATH, {"slots": {"1L": {"pid": 11}}})
    r = apply(
        mod,
        r,
        {"hookEventName": "StopCancelled", "sessionId": "s-cancel", "promptId": "p11"},
        11,
    )
    s.check("取消灰", r["slots"]["1L"]["state"], "idle")
    s.check("取消清prompt", r["slots"]["1L"].get("prompt_id"), None)

    # 13. SessionEnd → 隐
    r = empty_roster(mod)
    seed_session(mod, "s-end")
    bind(mod, r, "1L", "s-end", 11)
    r["slots"]["1L"].update(state="complete", prompt_id="p12")
    write_json(mod.SLOTS_PATH, {"slots": {"1L": {"pid": 11}}})
    r = apply(
        mod,
        r,
        {"hookEventName": "SessionEnd", "sessionId": "s-end"},
        11,
    )
    s.check("结束隐", r["slots"]["1L"]["state"], "empty")
    s.check_pad("结束后表盘", r, alive, ["empty", "empty", "empty", "empty"])

    # 14. 进程死了：即使 complete 也隐（companion livePid）
    r = empty_roster(mod)
    bind(mod, r, "2L", "s-dead", 99)
    r["slots"]["2L"].update(state="complete", prompt_id="px")
    s.check_pad("进程死了隐藏", r, alive, ["empty", "empty", "empty", "empty"])

    # 15. 四格同时：跑 / 完成 / 完成 / 空
    r = empty_roster(mod)
    for sid, name, pid, kind, prompt, st in (
        ("s-a", "1L", 11, "tool_call", "pa", "running"),
        ("s-b", "2L", 22, "turn_completed", "pb", "complete"),
        ("s-c", "3L", 33, "turn_completed", "pc", "idle"),
        (None, "4L", None, None, None, "empty"),
    ):
        if sid:
            root = seed_session(mod, sid)
            append_updates(root, [upd(kind, prompt)])
            bind(mod, r, name, sid, pid)
            r["slots"][name].update(state=st, prompt_id=prompt)
    refresh(mod, r)
    s.check_pad("四格同时", r, alive, ["running", "complete", "complete", "empty"])
    s.check("2和3都绿", [r["slots"]["2L"]["state"], r["slots"]["3L"]["state"]], ["complete", "complete"])

    # 16. 新提交覆盖完成
    r = empty_roster(mod)
    root = seed_session(mod, "s-next")
    append_updates(root, [upd("turn_completed", "old")])
    bind(mod, r, "1L", "s-next", 11)
    r["slots"]["1L"].update(state="complete", prompt_id="old")
    write_json(mod.SLOTS_PATH, {"slots": {"1L": {"pid": 11}}})
    r = apply(
        mod,
        r,
        {
            "hookEventName": "UserPromptSubmit",
            "sessionId": "s-next",
            "promptId": "new",
        },
        11,
    )
    s.check("新提问变蓝", r["slots"]["1L"]["state"], "running")
    s.check("新prompt_id", r["slots"]["1L"]["prompt_id"], "new")

    # 17. SessionStart 保持灰，不因旧 updates 变绿
    r = empty_roster(mod)
    write_json(mod.SLOTS_PATH, {"slots": {"1L": {"pid": 11, "slot": "1L"}}})
    seed_session(mod, "s-start")
    r = apply(
        mod,
        empty_roster(mod),
        {"hookEventName": "SessionStart", "sessionId": "s-start"},
        11,
    )
    s.check("SessionStart灰", r["slots"]["1L"]["state"], "idle")

    # 20. 同一 pid 新开会话：旧完成应变灰
    r = empty_roster(mod)
    seed_session(mod, "s-old")
    seed_session(mod, "s-new")
    bind(mod, r, "1L", "s-old", 11)
    r["slots"]["1L"].update(state="complete", prompt_id="old")
    write_json(mod.SLOTS_PATH, {"slots": {"1L": {"pid": 11, "slot": "1L"}}})
    r = apply(
        mod,
        r,
        {"hookEventName": "SessionStart", "sessionId": "s-new"},
        11,
    )
    s.check("同pid新开会话变灰", r["slots"]["1L"]["state"], "idle")
    s.check("换新session", r["slots"]["1L"].get("session_id"), "s-new")

    # 18. snapshot 下标 = 表盘 1234
    r = empty_roster(mod)
    for name, pid, st in (("1L", 11, "running"), ("2L", 22, "complete"), ("3L", 33, "needs_you"), ("4L", 44, "error")):
        bind(mod, r, name, f"s-{name}", pid)
        r["slots"][name]["state"] = st
        r["slots"][name]["prompt_id"] = "x"
    states = snap(r, alive)
    s.check("表盘下标1234", [SNAP[x] for x in states], [2, 4, 3, 5], face(states))

    # 19. 旧 1L/1R/2L/2R 映射会把 2 显示成空
    old = ["1L", "1R", "2L", "2R"]
    old_states = []
    for name in old:
        slot = r["slots"][name]
        old_states.append(slot.get("state") or "empty")
    s.check("旧映射会错(对照)", old_states, ["running", "empty", "complete", "empty"])

    r = empty_roster(mod)
    slot = mod.ensure_pid_slot(r, 4242, "s-auto")
    s.check("直接grok按pid建格", slot["name"] if slot else None, "p4242")
    s.check("新格默认idle", slot["state"] if slot else None, "idle")
    write_json(mod.PANES_PATH, {"page": 0, "panes": [{"pid": 4242, "index": 0}]})
    refresh(mod, r)
    s.check("panes同步还在", "p4242" in r["slots"], True)
    write_json(mod.PANES_PATH, {"page": 1, "panes": [
        {"pid": 11, "index": 0},
        {"pid": 22, "index": 1},
        {"pid": 33, "index": 2},
        {"pid": 44, "index": 3},
        {"pid": 55, "index": 4},
    ]})
    for pid in (11, 22, 33, 44, 55):
        mod.ensure_pid_slot(r, pid)
    refresh(mod, r)
    s.check("五扇都建了", all(f"p{p}" in r["slots"] or any(x.get("pid")==p for x in r["slots"].values()) for p in (11, 22, 33, 44, 55)), True)
    write_json(mod.PANES_PATH, {"page": 0, "panes": []})
    refresh(mod, r)
    s.check("刚死的pid先留着", "p4242" in r["slots"], True)
    if "p4242" in r["slots"]:
        r["slots"]["p4242"]["gone_at"] = 1
    refresh(mod, r)
    s.check("关掉的pid清掉", "p4242" in r["slots"], False)

    r = empty_roster(mod)
    seed_session(mod, "s-loop", title="hold upgrade batch")
    write_json(
        session_root(mod, "s-loop") / "subagents" / "x" / "meta.json",
        {"description": "loop: hold upgrade (every 1 hour)"},
    )
    bind(mod, r, "2L", "s-loop", 22)
    r["slots"]["2L"].update(state="complete", prompt_id="px")
    refresh(mod, r)
    s.check("loop单独成色", r["slots"]["2L"]["state"], "loop")

    r = empty_roster(mod)
    seed_session(mod, "s-mention", title="talk about loop in the copy")
    bind(mod, r, "1L", "s-mention", 11)
    r["slots"]["1L"].update(state="complete", prompt_id="p1")
    refresh(mod, r)
    s.check("标题带loop不算", r["slots"]["1L"]["state"], "complete")

    def pad_radius(n):
        if n <= 1:
            return 38
        import math
        r = int(168 * math.sin(math.pi / n) - 8)
        return max(22, min(38, r))

    s.check("4球半径仍38", pad_radius(4), 38)
    s.check("半径随n单调不增", pad_radius(10) <= pad_radius(4), True)

    print(f"tmp={tmp}")
    for row in s.rows:
        print(row)
    print(f"\n{s.ok} passed, {s.fail} failed")
    return 0 if s.fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(run())
