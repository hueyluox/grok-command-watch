# Grok Command Watch

[中文](README.md) · [English](README.en.md)

戴在手腕上，看 Ghostty 里那几扇 grok 谁在跑、谁跑完了。点一下数字，电脑就切到那一格。

硬件是 M5Stack StopWatch **C152**。

<p align="center">
  <img src="docs/images/face.svg" width="220" alt="4 个 grok：1 2 3 4 在上下左右">
  &nbsp;
  <img src="docs/images/face-5.svg" width="220" alt="5 个 grok：从 12 点均分，2 3 4 都挪了">
</p>

Ghostty 里直接打 `grok`，几个窗口就几个球。1 永远在正上，其余按圈均分——5 个时 2、3、4 不再钉在右/下/左。同大小最多 8 个，10 个还能点，再多才翻页。

颜色：蓝 = 在跑，绿 = 做完了，紫 = `/loop` 定时在挂着，灰 = 开着闲着，黄 = 要你点一下，红 = 挂了。  
外圈是电量，剩多少圈走多远，顶上有百分比。

左黄键语音（表上的麦还没验收），右蓝键回车。屏幕不自动灭。

## 你要有这些

- 这块表：C152，别的 M5 板子别刷
- 一台带蓝牙的 Mac（14 及以上）
- 能传数据的 USB-C（第一次刷机用，平时可以只靠蓝牙）
- PlatformIO、Xcode 命令行工具、Ghostty、本机 `grok`

刷机口认 Espressif 的 **USB JTAG**。Anker 那个 `SN…` 口不要用。

## 装

在配对这台 Mac 上从源码编，不提供现成安装包。

```bash
git clone https://github.com/hueyluox/grok-command-watch.git
cd grok-command-watch

bash host/install.sh
export PATH="$HOME/.grok/command-watch/bin:$PATH"

bash companion/wrap.sh
```

然后自己挂上开机任务（脚本不会偷偷帮你挂）：

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.grok-command-watch.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.grok-command-watch-keys.plist
```

按键那个进程只能留一个，多开会弄坏输入法。

### 刷表

1. USB-C 插上表
2. `ls /dev/cu.usbmodem*`，挑 JTAG 那个口
3. 刷之前先把电脑上的 companion 停掉，不然串口被占着

```bash
export PATH="$HOME/.grok/command-watch/bin:$PATH"
launchctl bootout gui/$(id -u)/local.grok-command-watch

pio run -e m5stack-stopwatch --target upload --upload-port /dev/cu.usbmodemXXXX

launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.grok-command-watch.plist
```

系统设置：给 **Grok Command Watch** 开蓝牙，给 keys 那个 Python 开辅助功能。蓝牙里找 **GrokWatch**。

没表也能先跑状态测试：

```bash
python3 host/sim_states.py
```

过了会打印 `37 passed`。

## 平时

Ghostty 里开几扇就打几次 `grok`。不用 `g1` `g2`。旧的 `g1`–`g4` 现在也只是启动 `grok`。

改完代码：固件再刷一次；电脑端跑 `companion/wrap.sh`；`roster.py` / `keys_daemon.py` 用 `host/install.sh` 拷到 `~/.grok/command-watch`。源码和运行时是两份，别只改运行时那边。[docs/LAYOUT.md](docs/LAYOUT.md)

## 刷回出厂

https://docs.m5stack.com/zh_CN/guide/restore_factory/stopwatch

插着 USB-C，长按复位大约两秒等到绿灯，用 M5Burner 刷厂固件。

## 还有这些文件

- [NOTICE.md](NOTICE.md) — 改编自哪
- [docs/LAYOUT.md](docs/LAYOUT.md) — 目录怎么分
- [docs/PROTOCOL.md](docs/PROTOCOL.md) — 表和电脑怎么通信
- [docs/C152-开源项目.md](docs/C152-开源项目.md) — 同板别的开源

## 许可

MIT。拷走时把 `LICENSE` 里那三行版权一起带着。
