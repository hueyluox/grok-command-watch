#!/usr/bin/env python3
"""Trusted key injector. Companion writes one line to the unix socket."""

from __future__ import annotations

import os
import socket
import time

import fcntl
import json
import sys
import threading
from pathlib import Path

from AppKit import NSPasteboard, NSPasteboardTypeString, NSRunningApplication, NSWorkspace
from ApplicationServices import (
    AXIsProcessTrusted,
    AXUIElementCreateApplication,
    AXUIElementCopyAttributeValue,
    AXUIElementPerformAction,
    AXUIElementSetAttributeValue,
)
from Quartz import (
    CGEventCreateKeyboardEvent,
    CGEventCreateMouseEvent,
    CGEventPost,
    CGEventSetFlags,
    CGEventSetType,
    CGEventSourceCreate,
    kCGEventFlagMaskAlternate,
    kCGEventFlagMaskCommand,
    kCGEventFlagsChanged,
    kCGEventLeftMouseDown,
    kCGEventLeftMouseUp,
    kCGEventSourceStatePrivate,
    kCGHIDEventTap,
    kCGMouseButtonLeft,
)

SLOTS_PATH = Path.home() / ".grok" / "command-watch" / "slots.json"
PANES_PATH = Path.home() / ".grok" / "command-watch" / "panes.json"

SOCK = os.path.expanduser("~/.grok/command-watch/keys.sock")
LOG = os.path.expanduser("~/.grok/command-watch/keys.log")
PID = os.path.expanduser("~/.grok/command-watch/keys.pid")
LOCK = os.path.expanduser("~/.grok/command-watch/keys.lock")
FOCUS_PATH = Path.home() / ".grok" / "command-watch" / "focus.json"


def log(msg: str) -> None:
    line = time.strftime("%H:%M:%S ") + msg + "\n"
    with open(LOG, "a") as fh:
        fh.write(line)


RIGHT_CMD = 54
talking = False


def tap(key: int, flags: int = 0) -> None:
    src = CGEventSourceCreate(kCGEventSourceStatePrivate)
    down = CGEventCreateKeyboardEvent(src, key, True)
    up = CGEventCreateKeyboardEvent(src, key, False)
    CGEventSetFlags(down, flags)
    CGEventSetFlags(up, 0 if flags == 0 else flags)
    CGEventPost(kCGHIDEventTap, down)
    time.sleep(0.02)
    CGEventPost(kCGHIDEventTap, up)


def release_mods() -> None:
    # Only lift Command / Option. Never Shift — WeType uses Shift to flip 中/英.
    src = CGEventSourceCreate(kCGEventSourceStatePrivate)
    for key in (54, 55, 58, 61):
        event = CGEventCreateKeyboardEvent(src, key, False)
        CGEventSetType(event, kCGEventFlagsChanged)
        CGEventSetFlags(event, 0)
        CGEventPost(kCGHIDEventTap, event)


def click_xy(x: float, y: float) -> None:
    down = CGEventCreateMouseEvent(None, kCGEventLeftMouseDown, (x, y), kCGMouseButtonLeft)
    up = CGEventCreateMouseEvent(None, kCGEventLeftMouseUp, (x, y), kCGMouseButtonLeft)
    CGEventPost(kCGHIDEventTap, down)
    time.sleep(0.02)
    CGEventPost(kCGHIDEventTap, up)


def rcmd_flags(down: bool) -> None:
    event = CGEventCreateKeyboardEvent(None, RIGHT_CMD, down)
    CGEventSetType(event, kCGEventFlagsChanged)
    CGEventSetFlags(event, kCGEventFlagMaskCommand if down else 0)
    CGEventPost(kCGHIDEventTap, event)


def rcmd_short_toggle() -> None:
    # 闪电说 listens for FlagsChanged on RCmd.
    # ~85ms = short press = Toggle on/off. Regular keyDown looks like a hold.
    rcmd_flags(False)
    time.sleep(0.025)
    rcmd_flags(True)
    time.sleep(0.08)
    rcmd_flags(False)


def ghostty_app():
    apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_("com.mitchellh.ghostty")
    if apps:
        return apps[0]
    url = NSWorkspace.sharedWorkspace().URLForApplicationWithBundleIdentifier_(
        "com.mitchellh.ghostty"
    )
    if url is not None:
        NSWorkspace.sharedWorkspace().openURL_(url)
        time.sleep(0.3)
        apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_("com.mitchellh.ghostty")
        return apps[0] if apps else None
    return None


def all_surfaces():
    app = ghostty_app()
    if app is None:
        return None, []
    el = AXUIElementCreateApplication(int(app.processIdentifier()))
    _, windows = AXUIElementCopyAttributeValue(el, "AXWindows", None)
    found = []
    for window in windows or []:
        _, kids = AXUIElementCopyAttributeValue(window, "AXChildren", None)
        tabs_here = []
        for child in kids or []:
            _, role = AXUIElementCopyAttributeValue(child, "AXRole", None)
            if role != "AXTabGroup":
                continue
            _, tabs = AXUIElementCopyAttributeValue(child, "AXTabs", None)
            for tab in tabs or []:
                tabs_here.append((window, tab))
        if tabs_here:
            found.extend(tabs_here)
        else:
            found.append((window, None))
    return app, found


def pid_alive(pid) -> bool:
    try:
        pid = int(pid)
        if pid <= 1:
            return False
        os.kill(pid, 0)
        return True
    except (TypeError, ValueError, OSError, ProcessLookupError):
        return False


TAB_KEYS = {1: 18, 2: 19, 3: 20, 4: 21, 5: 23, 6: 22, 7: 26, 8: 28, 9: 25}


def tty_sort_key(tty: str) -> tuple:
    import re
    text = (tty or "").replace("/dev/", "")
    match = re.search(r"(\d+)$", text)
    return (0, int(match.group(1))) if match else (1, text)


def is_grok_command(command: str) -> bool:
    text = (command or "").strip()
    if not text:
        return False
    low = text.lower()
    if "command-watch" in low or "grok bot" in low or "grok-command" in low:
        return False
    first = text.split()[0]
    return os.path.basename(first) == "grok"


def list_grok_procs() -> list[dict]:
    import subprocess
    try:
        out = subprocess.check_output(["ps", "-ax", "-o", "pid=,tty=,command="], text=True)
    except OSError:
        return []
    found = []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        if not is_grok_command(parts[2]):
            continue
        found.append({"pid": pid, "tty": parts[1].replace("/dev/", "")})
    found.sort(key=lambda item: (tty_sort_key(item["tty"]), item["pid"]))
    return found


def load_panes() -> dict:
    try:
        return json.loads(PANES_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {"page": 0, "panes": []}


def write_panes(page: int | None = None) -> dict:
    groks = list_grok_procs()
    _, surfaces = all_surfaces()
    n_tabs = len(surfaces)
    n = len(groks)
    panes = []
    for index, grok in enumerate(groks):
        tab = None
        pane = None
        if n_tabs and n == n_tabs:
            tab = index + 1
        elif n_tabs and n == 2 * n_tabs:
            tab = index // 2 + 1
            pane = "up" if index % 2 == 0 else "down"
        else:
            tab = index // 2 + 1
            pane = "up" if index % 2 == 0 else "down"
        panes.append({
            "index": index,
            "pid": grok["pid"],
            "tty": grok["tty"],
            "tab": tab,
            "pane": pane,
        })
    old = load_panes()
    if page is None:
        page = int(old.get("page") or 0)
    max_page = max(0, (len(panes) - 1) // 10)
    page = max(0, min(int(page), max_page))
    data = {"updated_at": time.time(), "page": page, "panes": panes}
    prev = {"page": old.get("page"), "panes": old.get("panes")}
    now = {"page": data["page"], "panes": data["panes"]}
    if prev != now:
        try:
            PANES_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = PANES_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2) + "\n")
            tmp.replace(PANES_PATH)
            log(f"panes n={len(panes)} page={page} pids={[p['pid'] for p in panes]}")
        except OSError as exc:
            log(f"panes write {exc}")
    return data


def stamp_title(name: str, tty: str | None) -> None:
    if not tty:
        return
    path = tty if tty.startswith("/") else f"/dev/{tty}"
    if not os.path.exists(path):
        return
    try:
        with open(path, "w") as fh:
            fh.write(f"\033]0;Grok-{name}\007")
    except OSError:
        pass


def focus_input(window, pane: str = "down") -> None:
    if window is None:
        return
    AXUIElementPerformAction(window, "AXRaise")
    AXUIElementSetAttributeValue(window, "AXFocused", True)
    # Do not click the terminal — a click commits WeType as raw pinyin.


def front_window():
    app = ghostty_app()
    if app is None:
        return None
    el = AXUIElementCreateApplication(int(app.processIdentifier()))
    _, windows = AXUIElementCopyAttributeValue(el, "AXWindows", None)
    return windows[0] if windows else None


def focus_pane(index: int) -> bool:
    data = write_panes()
    panes = data.get("panes") or []
    if index < 0 or index >= len(panes):
        log(f"focus {index} out of range have={len(panes)}")
        return False
    pane = panes[index]
    app, surfaces = all_surfaces()
    if app is None:
        return False
    if pane.get("tty"):
        stamp_title(str(index + 1), pane.get("tty"))
    app.activateWithOptions_(1 << 1)
    time.sleep(0.06)
    release_mods()
    tab_i = int(pane.get("tab") or 1) - 1
    if 0 <= tab_i < len(surfaces):
        window, tab = surfaces[tab_i]
        AXUIElementPerformAction(window, "AXRaise")
        if tab is not None:
            AXUIElementPerformAction(tab, "AXPress")
            time.sleep(0.12)
    elif pane.get("tab") in TAB_KEYS:
        tap(TAB_KEYS[int(pane["tab"])], kCGEventFlagMaskCommand)
        time.sleep(0.15)
    side = pane.get("pane")
    if side in ("up", "down"):
        release_mods()
        tap(126 if side == "up" else 125, kCGEventFlagMaskCommand | kCGEventFlagMaskAlternate)
        time.sleep(0.08)
    release_mods()
    focus_input(front_window(), side or "up")
    log(f"focus pane={index} pid={pane.get('pid')} tab={pane.get('tab')} {side}")
    return True


def bump_page(delta: int) -> None:
    data = write_panes()
    n = len(data.get("panes") or [])
    max_page = max(0, (n - 1) // 10)
    page = max(0, min(int(data.get("page") or 0) + int(delta), max_page))
    write_panes(page=page)
    log(f"page -> {page} / {max_page} n={n}")


def press_tab(n: int) -> bool:
    app, found = all_surfaces()
    if app is None:
        return False
    if n < 1 or n > len(found):
        titles = []
        for window, tab in found:
            el = tab or window
            _, title = AXUIElementCopyAttributeValue(el, "AXTitle", None)
            titles.append(str(title or "?"))
        log(f"focus {n} out of range have={len(found)} {titles}")
        return False
    window, tab = found[n - 1]
    app.activateWithOptions_(1 << 1)
    AXUIElementPerformAction(window, "AXRaise")
    if tab is None:
        log(f"focus {n}/{len(found)} window")
        return True
    err = AXUIElementPerformAction(tab, "AXPress")
    _, title = AXUIElementCopyAttributeValue(tab, "AXTitle", None)
    log(f"focus {n}/{len(found)} press={err} {title}")
    return err == 0


def handle(line: str) -> None:
    parts = line.strip().split()
    if not parts:
        return
    cmd = parts[0]
    if cmd == "focus":
        n = int(parts[1]) if len(parts) > 1 else 0
        focus_pane(n)
        return
    if cmd == "page":
        delta = int(parts[1]) if len(parts) > 1 else 1
        bump_page(delta)
        return
    if cmd == "shandianshuo":
        release_mods()
        rcmd_flags(True)
        time.sleep(0.08)
        rcmd_flags(False)
        log("shandianshuo pulse")
        return
    if cmd == "enter":
        release_mods()
        app = ghostty_app()
        if app:
            app.activateWithOptions_(1 << 1)
            time.sleep(0.06)
            el = AXUIElementCreateApplication(int(app.processIdentifier()))
            _, windows = AXUIElementCopyAttributeValue(el, "AXWindows", None)
            if windows:
                focus_input(windows[0])
        time.sleep(0.05)
        release_mods()
        tap(36, 0)
        log("enter")
        return
    if cmd == "paste":
        text = line.strip()[6:] if line.strip().startswith("paste ") else ""
        if not text:
            return
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.setString_forType_(text, NSPasteboardTypeString)
        app = ghostty_app()
        if app:
            app.activateWithOptions_(1 << 1)
            time.sleep(0.08)
        tap(9, kCGEventFlagMaskCommand)
        log(f"paste chars={len(text)}")


def infer_watch_slot() -> int | None:
    app = ghostty_app()
    if app is None or not app.isActive():
        return None
    el = AXUIElementCreateApplication(int(app.processIdentifier()))
    _, windows = AXUIElementCopyAttributeValue(el, "AXWindows", None)
    if not windows:
        return None
    win = windows[0]
    tab_n = 1
    _, kids = AXUIElementCopyAttributeValue(win, "AXChildren", None)
    for child in kids or []:
        _, role = AXUIElementCopyAttributeValue(child, "AXRole", None)
        if role != "AXTabGroup":
            continue
        _, tabs = AXUIElementCopyAttributeValue(child, "AXTabs", None)
        for i, tab in enumerate(tabs or []):
            _, val = AXUIElementCopyAttributeValue(tab, "AXValue", None)
            if val:
                tab_n = i + 1
                break
    pane = "up"
    _, focused = AXUIElementCopyAttributeValue(el, "AXFocusedUIElement", None)
    if focused is not None:
        _, pos = AXUIElementCopyAttributeValue(focused, "AXPosition", None)
        _, wpos = AXUIElementCopyAttributeValue(win, "AXPosition", None)
        _, wsize = AXUIElementCopyAttributeValue(win, "AXSize", None)
        try:
            fy = float(pos.y)
            mid = float(wpos.y) + float(wsize.height) * 0.5
            pane = "up" if fy < mid else "down"
        except Exception:
            pane = "up"
    data = write_panes()
    panes = data.get("panes") or []
    hit = None
    for item in panes:
        if int(item.get("tab") or 0) != tab_n:
            continue
        side = item.get("pane")
        if side in (None, "", pane):
            hit = item
            if side == pane:
                break
    if hit is None:
        return None
    idx = int(hit["index"])
    page = idx // 10
    if page != int(data.get("page") or 0):
        write_panes(page=page)
    return idx - page * 10


def poll_mac_focus() -> None:
    last = None
    while True:
        time.sleep(1.0)
        try:
            write_panes()
            slot = infer_watch_slot()
        except Exception as exc:
            log(f"focus-poll {exc}")
            continue
        if slot is None or slot == last:
            continue
        last = slot
        try:
            FOCUS_PATH.write_text(json.dumps({"slot": slot, "at": time.time()}) + "\n")
        except OSError:
            pass
        log(f"mac-focus slot={slot}")


def exclusive_or_exit() -> int:
    os.makedirs(os.path.dirname(LOCK), exist_ok=True)
    fd = os.open(LOCK, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("already running, exit")
        sys.exit(0)
    os.ftruncate(fd, 0)
    os.write(fd, str(os.getpid()).encode())
    return fd


def main() -> int:
    exclusive_or_exit()
    os.makedirs(os.path.dirname(SOCK), exist_ok=True)
    with open(PID, "w") as fh:
        fh.write(str(os.getpid()))
    if os.path.exists(SOCK):
        os.remove(SOCK)
    threading.Thread(target=poll_mac_focus, daemon=True).start()
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK)
    os.chmod(SOCK, 0o600)
    srv.listen(8)
    rcmd_flags(False)
    log(f"listen ax={AXIsProcessTrusted()} released RCmd")
    while True:
        conn, _ = srv.accept()
        with conn:
            data = b""
            while True:
                chunk = conn.recv(256)
                if not chunk:
                    break
                data += chunk
                if b"\n" in data:
                    break
        for raw in data.decode("utf-8", "replace").splitlines():
            try:
                handle(raw)
            except Exception as exc:
                log(f"err {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
