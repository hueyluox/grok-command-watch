# 进展（2026-08-18）

公开仓：https://github.com/hueyluox/grok-command-watch  
本机运行时：`~/.grok/command-watch`  
本机 launchd：`local.oscar.grok-watch` + `local.oscar.grok-keys`（不要按仓库模板名再 bootstrap 一份）

## 已经定下来的行为

- Ghostty 里直接 `grok`，不用 g1/g2。活窗口按 tty 排成一圈球。
- 1 在 12 点，其余均分。≤10 个不翻页。点按只打到球上。
- `/loop` 紫。做完绿。在跑蓝。权限黄。失败红。刚开灰。
- 插 USB 只走串口 SNAP，不申请蓝牙。拔表芯片会复位，靠 BLE 补画。
- 状态机：`host/roster.py`；切窗：`host/keys_daemon.py`。

## 检查

```bash
python3 host/sim_states.py          # 42 passed
```

## 没做完

- 表麦 → 闪电说
- companion 仍是单文件
- 本机 Label 和仓库模板不一致
