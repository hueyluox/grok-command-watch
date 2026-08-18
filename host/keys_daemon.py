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


# Watch 1–4 = g1–g4 sitting in two Ghostty tabs:
#   1 g1 → ⌘1 上    2 g2 → ⌘1 下
#   3 g3 → ⌘2 上    4 g4 → ⌘2 下
WATCH_MAP = {
    1: ("1L", 1, "up"),
    2: ("1R", 1, "down"),
    3: ("2L", 2, "up"),
    4: ("2R", 2, "down"),
}


def bind_for(n: int):
    spec = WATCH_MAP.get(n)
    if not spec:
        return None
    name, tab, pane = spec
    try:
        data = json.loads(SLOTS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return name, {}, tab, pane
    ent = (data.get("slots") or {}).get(name) or {}
    return name, ent, tab, pane


def stamp_title(name: str, tty: str | None) -> None:
    if not tty or not os.path.exists(tty):
        return
    try:
        with open(tty, "w") as fh:
            fh.write(f"\033]0;Grok-{name}\007")
    except OSError:
        pass


def ax_titles(el) -> str:
    _, title = AXUIElementCopyAttributeValue(el, "AXTitle", None)
    return str(title or "")


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


def focus_g(n: int) -> bool:
    app = ghostty_app()
    if app is None:
        return False
    spec = bind_for(n)
    if not spec:
        return False
    name, ent, tab_n, pane = spec
    if ent.get("tty"):
        stamp_title(name, ent.get("tty"))
    app.activateWithOptions_(1 << 1)
    time.sleep(0.06)
    release_mods()
    tap({1: 18, 2: 19}.get(tab_n, 18), kCGEventFlagMaskCommand)
    time.sleep(0.18)
    release_mods()
    split_key = 126 if pane == "up" else 125
    tap(split_key, kCGEventFlagMaskCommand | kCGEventFlagMaskAlternate)
    time.sleep(0.08)
    release_mods()
    focus_input(front_window(), pane)
    log(f"focus watch{n} g{n} -> ghostty cmd{tab_n} {pane}")
    return True


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
        n = int(parts[1]) if len(parts) > 1 else 1
        focus_g(n)
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
    if tab_n == 1:
        return 0 if pane == "up" else 2
    if tab_n == 2:
        return 4 if pane == "up" else 6
    return None


def poll_mac_focus() -> None:
    last = None
    while True:
        time.sleep(1.0)
        try:
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
