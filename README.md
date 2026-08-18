# Grok Command Watch

[中文](README.md) · [English](README.en.md)

M5Stack StopWatch **C152** 上的 **Ghostty + Grok** 看板。

手腕上看四扇正在跑的 grok：谁在想、谁做完、谁要你点一下。点数字切到那一格输入框。

作者 [hueyluox](https://github.com/hueyluox)。**不是 Codex Micro，也不是 ChatGPT Desktop 的壳。**

<p align="center">
  <img src="docs/images/face.svg" width="280" alt="表盘示意：1 蓝运行、2 绿完成、3 黄要你、4 空不画">
</p>

## 它做什么

| 你在表上看到 / 做的 | 实际发生的事 |
|---|---|
| 四个数字 | Ghostty 里四扇活着的 `grok`，按 tty 顺序 |
| 蓝呼吸 | 正在跑，或卡在 Waiting for response |
| 绿 | 这轮说完了 |
| 灰 | 开了，还没说话 |
| 黄 | 权限弹窗，要你 |
| 红 | 失败 |
| 空位不画 | 那扇窗没开 |
| 点 1–4 | 切到对应分屏的输入框 |
| 右蓝键 | Enter |
| 左黄键 | 闪电说（表麦，**还没验收**） |
| 外圈 | 电量，黄昏渐变，屏幕常亮 |

两标签、上下分屏时的启动器：

```text
g1   ⌘1 上     1L
g2   ⌘1 下     1R
g3   ⌘2 上     2L
g4   ⌘2 下     2R
```

表盘按活进程的 tty 排，不要求你必须用这四个名字。`g4` 实际是 `2R` 也会显示成第 4 点。

## 它不做什么

- 不接 Codex / ChatGPT Desktop，没有额度环、没有 Cmd+Enter 全屏那套。
- 不把对话、token、路径、账号写上手表。
- 第一期不在表上点「批准工具」。
- 表麦进闪电说还没跑通；键盘右 Command + DJI 麦是另一条路。

板级上电、AMOLED sleep 转发改编自 [digitsisyph/codex-micro-stopwatch](https://github.com/digitsisyph/codex-micro-stopwatch)（MIT）。出处见 [NOTICE.md](NOTICE.md)。

## 你需要

- **M5Stack StopWatch Dev Kit，SKU C152**。别的 M5 板子这仓库不支持。
- 带蓝牙的 Mac，macOS 14+。
- 能传数据的 USB-C（第一次刷机；日常可只走 BLE）。
- PlatformIO、Swift 5.10+（Xcode CLT）、Ghostty、本机 `grok`。

刷机口必须是 Espressif **USB JTAG/serial debug unit**。不要写 Anker 的 `/dev/cu.usbmodemSN…`。

## 安装

仓库故意不发预编译 app / DMG。在配对那台 Mac 上从源码编。

```bash
git clone https://github.com/hueyluox/grok-command-watch.git
cd grok-command-watch

bash host/install.sh
export PATH="$HOME/.grok/command-watch/bin:$PATH"

bash companion/wrap.sh
```

`install.sh` 会写出 LaunchAgent 模板，**不会**自动 bootstrap。确认之后：

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.grok-command-watch.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.grok-command-watch-keys.plist
```

keys daemon **只许一个**。叠多个会打乱输入法。

### 刷固件

1. USB-C 插上 C152。
2. `ls /dev/cu.usbmodem*`，确认是 JTAG 口，不是 Anker。
3. 先编后刷，刷之前看一眼口：

```bash
export PATH="$HOME/.grok/command-watch/bin:$PATH"
pio run -e m5stack-stopwatch
pio run -e m5stack-stopwatch --target upload --upload-port /dev/cu.usbmodemXXXX
```

刷的时候 companion 会占着串口。先：

```bash
launchctl bootout gui/$(id -u)/local.grok-command-watch
# 刷完
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.grok-command-watch.plist
```

系统设置里给 **Grok Command Watch** 开蓝牙；给 keys daemon 的 Python 开辅助功能。配对设备名 **GrokWatch**。

### 没硬件时

```bash
python3 host/sim_states.py
```

应看到 `32 passed`。

## 日常

Ghostty 里两标签各上下分一屏，分别跑 `g1` `g2` `g3` `g4`。  
源码改完：固件重新刷；companion 跑 `companion/wrap.sh` 再 kickstart；`roster.py` / `keys_daemon.py` 跑 `host/install.sh` 拷到 `~/.grok/command-watch`。

源码和运行时是两份，别只改运行时。见 [docs/LAYOUT.md](docs/LAYOUT.md)。

## 厂恢

https://docs.m5stack.com/zh_CN/guide/restore_factory/stopwatch

USB-C 插着，长按复位约 2 秒到绿灯，M5Burner 刷回厂固件。

## 文档

| 文件 | 内容 |
|------|------|
| [NOTICE.md](NOTICE.md) | 这是谁的、改编了什么 |
| [docs/LAYOUT.md](docs/LAYOUT.md) | 源码 vs `~/.grok/command-watch` |
| [docs/PROTOCOL.md](docs/PROTOCOL.md) | BLE GATT |
| [docs/C152-开源项目.md](docs/C152-开源项目.md) | 同板其它开源固件 |

## 许可

MIT。再分发时保留 `LICENSE` 里 hueyluox、imliubo、codex-micro-4-stopwatch contributors 三行版权。
