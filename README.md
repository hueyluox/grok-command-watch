# Grok Command Watch

Author: [hueyluox](https://github.com/hueyluox)

A **466px round AMOLED dashboard** for **Ghostty + Grok** on the M5Stack
StopWatch Dev Kit (**SKU C152**). Four live Grok panes, tap-to-focus, Enter,
and optional watch-mic voice.

This is **not Codex Micro** and **not** a reskin of ChatGPT Desktop. It is a
separate open-source project. Board bring-up (power rail, AMOLED sleep
forwarding) is adapted from
[digitsisyph/codex-micro-stopwatch](https://github.com/digitsisyph/codex-micro-stopwatch)
(MIT). See `NOTICE.md` and `LICENSE`.

中文：Ghostty 里四扇 grok 的手表看板。作者 hueyluox。不是 Codex Micro。

## Watch face

Four pads = four live `grok` processes, ordered by TTY
(typically ⌘1 top / ⌘1 bottom / ⌘2 top / ⌘2 bottom).

| Color | Meaning |
|-------|---------|
| Hidden | No session |
| Gray | Opened, no prompt yet |
| Blue (breathe) | Running / waiting for response |
| Amber | Needs you (permission) |
| Green | Turn finished |
| Red | Error |

Left yellow = 闪电说 (watch mic, still unverified). Right blue = Enter.
Tap a pad to focus that Ghostty pane. Display stays on.

Launchers (two Ghostty tabs, each split):

```bash
g1   # ⌘1 top     1L
g2   # ⌘1 bottom  1R
g3   # ⌘2 top     2L
g4   # ⌘2 bottom  2R
```

## Layout

| Role | Path |
|------|------|
| This repo | wherever you clone it |
| Runtime | `~/.grok/command-watch` (app, roster, logs) |
| Link | `~/.grok/command-watch/src` → this repo |

Details: `docs/LAYOUT.md`

## Install

```bash
git clone https://github.com/hueyluox/grok-command-watch.git
cd grok-command-watch

bash host/install.sh
# new shell, or:
export PATH="$HOME/.grok/command-watch/bin:$PATH"

bash companion/wrap.sh
# then load LaunchAgents once (see host/install.sh output)

# firmware — port must be Espressif USB JTAG, not an Anker serial gadget
export PATH="$HOME/.grok/command-watch/bin:$PATH"
pio run -e m5stack-stopwatch --target upload --upload-port /dev/cu.usbmodem2301
```

State-machine tests (no hardware):

```bash
python3 host/sim_states.py
```

Factory restore: https://docs.m5stack.com/zh_CN/guide/restore_factory/stopwatch  
USB-C in, hold reset ~2s to green LED, flash the factory bin with M5Burner.

## Docs

- `NOTICE.md` — this is mine; what was adapted
- `docs/PROTOCOL.md` — BLE GATT
- `docs/LAYOUT.md` — source vs runtime
- `docs/C152-开源项目.md` — other C152 firmware
- `docs/00-方案讨论稿.md` — early design notes

## License

MIT. Keep the copyright lines for hueyluox, imliubo, and
codex-micro-4-stopwatch contributors.
