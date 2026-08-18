#!/usr/bin/env python3
"""Inject Ghostty tab / pane / enter / paste. Uses this interpreter's Accessibility grant."""

from __future__ import annotations

import sys
import time

from AppKit import NSPasteboard, NSRunningApplication, NSWorkspace, NSPasteboardTypeString
from ApplicationServices import (
    AXUIElementCreateApplication,
    AXUIElementCopyAttributeValue,
    AXUIElementPerformAction,
)
from Quartz import (
    CGEventCreateKeyboardEvent,
    CGEventPost,
    CGEventSetFlags,
    kCGEventFlagMaskAlternate,
    kCGEventFlagMaskCommand,
    kCGHIDEventTap,
)

SLOT_NAMES = ("1L", "1R", "2L", "2R", "3L", "3R", "4L", "4R")


def tap(key: int, flags: int = 0) -> None:
    down = CGEventCreateKeyboardEvent(None, key, True)
    up = CGEventCreateKeyboardEvent(None, key, False)
    if flags:
        CGEventSetFlags(down, flags)
        CGEventSetFlags(up, flags)
    CGEventPost(kCGHIDEventTap, down)
    time.sleep(0.015)
    CGEventPost(kCGHIDEventTap, up)


def activate() -> None:
    apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_("com.mitchellh.ghostty")
    if apps:
        apps[0].activateWithOptions_(1 << 1)
        time.sleep(0.12)
        return
    url = NSWorkspace.sharedWorkspace().URLForApplicationWithBundleIdentifier_(
        "com.mitchellh.ghostty"
    )
    if url is not None:
        NSWorkspace.sharedWorkspace().openURL_(url)
        time.sleep(0.25)


def raise_titled(*needles: str) -> bool:
    apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_("com.mitchellh.ghostty")
    if not apps:
        return False
    app = apps[0]
    app.activateWithOptions_(1 << 1)
    el = AXUIElementCreateApplication(int(app.processIdentifier()))
    err, windows = AXUIElementCopyAttributeValue(el, "AXWindows", None)
    if err != 0 or not windows:
        return False
    lowered = [n.lower() for n in needles if n]
    for window in windows:
        werr, title = AXUIElementCopyAttributeValue(window, "AXTitle", None)
        if werr != 0 or not title:
            continue
        text = str(title).lower()
        if any(n in text for n in lowered):
            AXUIElementPerformAction(window, "AXRaise")
            time.sleep(0.08)
            return True
    return False


def focus(slot: int) -> None:
    command = slot // 2 + 1
    pane_right = slot % 2 == 1
    keys = {1: 18, 2: 19, 3: 20, 4: 21}
    name = SLOT_NAMES[slot] if 0 <= slot < len(SLOT_NAMES) else f"{command}"
    if raise_titled(f"Grok-{name}", f"Grok-{command}"):
        return
    activate()
    if command in keys:
        tap(keys[command], kCGEventFlagMaskCommand)
        time.sleep(0.06)
    tap(124 if pane_right else 123, kCGEventFlagMaskCommand | kCGEventFlagMaskAlternate)


def send_enter() -> None:
    activate()
    tap(36)


def paste(text: str) -> None:
    if not text:
        return
    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    pb.setString_forType_(text, NSPasteboardTypeString)
    activate()
    tap(9, kCGEventFlagMaskCommand)


def shandianshuo() -> None:
    tap(54, kCGEventFlagMaskCommand)  # right Command


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: ghostty_keys.py focus N|enter|paste TEXT|activate|shandianshuo", file=sys.stderr)
        return 2
    cmd = sys.argv[1]
    if cmd == "focus":
        focus(int(sys.argv[2]))
    elif cmd == "enter":
        send_enter()
    elif cmd == "paste":
        paste(" ".join(sys.argv[2:]))
    elif cmd == "activate":
        activate()
    elif cmd == "shandianshuo":
        activate()
        time.sleep(0.08)
        shandianshuo()
    else:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
