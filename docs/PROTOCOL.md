# Grok Command Watch BLE

自有协议，不是 Codex Micro，也不是上游额度 GATT。

设备名：`GrokWatch`

| 项 | UUID |
|---|---|
| Service | `A1C8E240-6F31-4B2A-9C11-0D8F1A7C0020` |
| Snapshot 写入（Mac → 表） | `A1C8E240-6F31-4B2A-9C11-0D8F1A7C0021` |
| Event 通知（表 → Mac） | `A1C8E240-6F31-4B2A-9C11-0D8F1A7C0022` |
| Audio 通知（手表麦 PCM 16k/16bit/mono） | `A1C8E240-6F31-4B2A-9C11-0D8F1A7C0023` |

加密绑定后的 Write / Notify。单帧 UTF-8 JSON，≤ 512 字节。不上对话全文、token、路径、账号。

## Snapshot

```json
{
  "v": 1,
  "sel": 0,
  "fg": 0,
  "link": 2,
  "s": [1, 2, 0, 0, 0, 0, 0, 0],
  "t": ["短标题", "", "", ""]
}
```

- `sel` / `fg`：0–7 选中格 / 前台格；-1 表示无。
- `link`：0 离线，1 仅 BLE，2 Mac 在线。
- `s[0..3]`：表盘 1–4（companion 按活 grok 的 tty 排序，常见 1L/2L/3L/2R）。`s[4..7]` 不用。
- 值：`0` 空（不画）`1` 闲灰 `2` 跑蓝 `3` 要你黄 `4` 完成绿 `5` 出错红。
- `t[4]`：四个点的短标题。

## Event

```json
{"op":"select","slot":0}
{"op":"focus","slot":0}
{"op":"jump_need"}
{"op":"voice_start","slot":0}
{"op":"voice_stop","slot":0}
{"op":"send","slot":0}
{"op":"answer","n":2}
```

`slot` 0–7 同上。`n` 为 1–4。
