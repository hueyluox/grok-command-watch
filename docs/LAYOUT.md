# 目录

## 两处，不要混

```
<this-repo>/                               项目：改这里
~/.grok/command-watch/                     运行时：表连着 Mac 读这里
~/.grok/command-watch/src  →  项目（符号链接）
```

改固件 / companion / roster，改项目再拷到运行时。不要只改 `~/.grok/command-watch` 里的副本。

## 项目

```
src/main.cpp                 固件
include/                     表盘 UI / BLE / 手势
platformio.ini
scripts/                     AMOLED sleep patch、串口探测
companion/Sources/…/main.swift   Mac companion
companion/wrap.sh            编好装进 ~/.grok/…/Grok Command Watch.app
host/roster.py               状态机（权威）
host/keys_daemon.py          点表切窗 / 回车 / 闪电说
host/launch.sh               可选包装，现在只 exec grok
host/sim_states.py           状态模拟
host/roster.py               状态；panes.json 是活窗口清单
host/install.sh              拷到运行时
host/hooks/                  Grok hooks 模板
host/launchd/                LaunchAgent 模板
docs/
```

`.pio/` 是 PlatformIO 编译缓存（约 400MB），不要当源码。

## 运行时（不要当仓库）

```
Grok Command Watch.app/      companion
bin/g1 g2 g3 g4              启动器
bin/keys_daemon.py
roster.py                    companion 每 2.5s --refresh
roster.json / slots.json / panes.json
device.json                  已配对的表 UUID
*.log                        日志
.venv-pio/                   本机 pio
```

## 谁在跑

| 进程 | launchd |
|------|---------|
| command-watch-companion --watch | **本机** `local.oscar.grok-watch`（仓模板是 `local.grok-command-watch`） |
| keys_daemon.py（只能一个） | **本机** `local.oscar.grok-keys` |

刷固件前先 bootout **本机那个 Label**，刷完再 bootstrap，否则占着 USB JTAG。别把两套 Label 同时拉起来。
