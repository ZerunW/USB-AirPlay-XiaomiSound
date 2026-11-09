# USB-AirPlay-XiaomiSound

[English](README.md) | 简体中文

用一块 Raspberry Pi Zero 2 W，把两台小米 Sound 音箱（型号 L16B）变成一套立体声输出：

- 对电脑（macOS、Windows、Ubuntu）来说，它是一块名为 "Xiaomi Sound Bridge" 的 **USB 声卡**；
- 对 iPhone 或 Mac 来说，它是一个 **AirPlay 2** 接收器；
- 它通过**蓝牙**把左声道发给一台音箱、右声道发给另一台，并使用音箱自身的（绝对）音量，各处音量保持同步。

## 验证过的方案

```
  iPhone / Mac ---- AirPlay 2 (Wi-Fi) ----+
                                          v
                                +--------------------+   蓝牙 A2DP，AAC 单声道 112 kbps
                                | Raspberry Pi       |   每台音箱一条连接
                                | Zero 2 W           +-----------+--------------+
                                |                    |           |              |
                                | shairport-sync     |   +-------v------+ +-----v--------+
                                | xsb-usb-forward    |   | 左音箱       | | 右音箱       |
                                | xsb-bt-stream@L/R  |   | 放左声道     | | 放右声道     |
                                +--------------------+   +--------------+ +--------------+
                                          ^
  电脑 ---- USB（UAC2 声卡）--------------+
```

Pi 上运行的内容和原因：

| 部分 | 作用 |
|---|---|
| 双蓝牙 | Pi 分别和两台音箱配对，自己拆分左右声道。两台音箱**不能**组成米家立体声组合（原因见[测试结果](#测试结果)）。 |
| AAC 单声道，每台 112 kbps | 自编译的 BlueALSA 4.3.1 + fdk-aac（`setup/build-bluealsa-aac.sh`）。同样的空中占用下，音质比 SBC 好。 |
| 绝对音量并保持同步 | 原生 AVRCP 音量（`--a2dp-volume`）。`xsb-volume-sync` 让两台音箱、米家 app、音箱按键和语音、电脑音量、AirPlay 音量条保持一致。 |
| 左右对齐 | 用 `LEFT_LATENCY_MS` / `RIGHT_LATENCY_MS` 推迟其中一台，让两台同时出声。按房间校准一次即可（见[校准](#校准左右对齐)）。 |
| 只在有声音时推流 | 音源停止 15 秒后停掉蓝牙音频流（`BT_IDLE_STOP_S`）。蓝牙连接保持，音量照常可调。 |
| sniff 省电并在起步前唤醒 | 空闲的蓝牙链路进入 sniff 省电模式（`xsb-bt-sniff`），把 Zero 2 W 上和 Wi-Fi 共用的 2.4 GHz 芯片让出来。开始推流前，Pi 主动唤醒链路，保证每次起步表现一致。 |
| AirPlay 优先 | AirPlay 播放时暂停 USB 通道，AirPlay 停止 5 秒后恢复。 |
| 自动恢复 | 带退避的自动重连、蓝牙控制器看门狗、Wi-Fi 看门狗和 USB 网络救援。 |

## 测试结果

2026-11-09 在参考环境中测得（两台 L16B、Pi Zero 2 W、Mac 通过 USB 播放）。延迟的测法：从 Mac 发出一个"嘀"开始，到放在听音位置的麦克风听到为止，两端都用 Mac 的时钟计时。

| 测试 | 结果 |
|---|---|
| 总延迟，电脑到音箱，AAC（连续 2 分钟） | 左 389-433 ms，右 403-429 ms |
| 总延迟，AAC，6 次停播再播 | 377-419 ms（约 13 次起步中有 1 次落在约 750 ms，下一次起步又恢复正常） |
| 总延迟，SBC（对照） | 左 359-369 ms，右 335-359 ms |
| 其中 Pi 占用 | 约 150 ms（USB 转发 50 ms + 蓝牙缓冲 80-100 ms） |
| 校准后的左右对齐 | 相差约 10-30 ms |
| 声道分离 | 每台音箱只放自己的声道 |
| 断音（20 秒连续纯音） | AAC：左 2 次、右 7 次，每次 10-40 ms。SBC：5 次和 10 次。常在两台同一时刻发生：Wi-Fi 和蓝牙共用一颗芯片。 |
| Pi 侧欠载（连续 2 分钟） | 0 |
| 音量同步 | 调一台音箱的音量，另一台 4 秒内跟上 |
| 重启 | 开机约 1 分钟后两台自动连上，编码、音量和所有服务都恢复 |
| CPU（BlueALSA，两路 AAC） | 5-6 % |
| 推流前 sniff 唤醒 | 两条链路 20-50 ms 内回到正常模式 |

为什么不用米家立体声组合：通过蓝牙输入时，组合的副音箱播放的是单声道混音 (L+R)/2，而且组合自带的同步缓冲让总延迟达到 765-1700 ms，播放过程中还会越来越大。小米官方也公开说过，组合会留缓冲来保证两台同步。

局限：

- 约 400 ms 的延迟对严格的音画同步来说太大，看视频请使用带"音频延迟"设置的播放器。
- 推流空闲超过 15 秒后，下一段声音的开头可能在蓝牙推流启动期间被截掉一点。
- Zero 2 W 的 Wi-Fi 和蓝牙共用一颗 2.4 GHz 芯片，可能出现短暂断音。
- 同一时间只播放一个音源（AirPlay 优先于 USB）。只支持播放。

## 安装

需要：Raspberry Pi Zero 2 W、microSD 卡、带供电的 USB 扩展坞（连接电脑和 Pi）、micro-USB 数据线、Pi 的独立电源、两台小米 Sound 音箱。

接线：把 Pi 上标着 **USB** 的 micro-USB 口（不是 **PWR**）接到扩展坞，扩展坞接电脑；Pi 通过 **PWR** 口供电。

以下命令都通过 SSH 在 Pi 上执行（`ssh <用户名>@<主机名>.local`）。

### 1. 烧录系统

用 Raspberry Pi Imager 烧录 Raspberry Pi OS（trixie，32 位）。在系统自定义设置里填好主机名、用户名和密码、Wi-Fi，并开启 SSH。如果用的是桌面版镜像，改为无头运行：

```bash
git clone https://github.com/ZerunW/USB-AirPlay-XiaomiSound.git ~/USB-AirPlay-XiaomiSound
cd ~/USB-AirPlay-XiaomiSound
sudo bash setup/headless.sh
sudo reboot
```

（Lite 版镜像只需克隆仓库。）

### 2. 安装依赖

```bash
sudo apt update
sudo apt install -y bluez-alsa-utils libasound2-plugin-bluez libasound2-plugins rfkill iw dkms
sudo apt install -y build-essential git autoconf automake libtool libpopt-dev \
  libconfig-dev libasound2-dev avahi-daemon libavahi-client-dev libssl-dev \
  libsoxr-dev libplist-dev libsodium-dev uuid-dev libgcrypt20-dev xxd \
  libplist-utils libavutil-dev libavcodec-dev libavformat-dev systemd-dev
```

### 3. 编译 NQPTP 和 shairport-sync（AirPlay 2）

```bash
cd ~
git clone https://github.com/mikebrady/nqptp.git
cd nqptp && git checkout 1.2.9
autoreconf -fi && ./configure --with-systemd-startup && make && sudo make install
sudo systemctl enable --now nqptp
cd ~
git clone https://github.com/mikebrady/shairport-sync.git
cd shairport-sync && git checkout 5.6.2
autoreconf -fi
./configure --sysconfdir=/etc --with-alsa --with-soxr --with-avahi \
  --with-ssl=openssl --with-systemd-startup --with-airplay-2 \
  --with-dbus-interface --with-metadata
make && sudo make install
```

D-Bus 接口用于让 AirPlay 的音量条跟随音箱音量。

### 4. 编译带 AAC 的 BlueALSA

```bash
cd ~/USB-AirPlay-XiaomiSound
sudo bash setup/build-bluealsa-aac.sh
```

脚本会把 fdk-aac 2.0.3 和 BlueALSA 4.3.1 编译安装到 `/usr/local`，并创建存储目录的软链接 `/usr/local/var/lib/bluealsa -> /var/lib/bluealsa`。

### 5. 安装项目文件

```bash
cd ~/USB-AirPlay-XiaomiSound
sudo install -m 0755 -o root -g root pi-files/usr/local/sbin/xsb-*.sh pi-files/usr/local/sbin/xsb-volume-sync /usr/local/sbin/
sudo install -m 0644 pi-files/etc/systemd/system/xsb-*.service /etc/systemd/system/
sudo install -d /etc/systemd/system/bluealsa.service.d /etc/systemd/system/shairport-sync.service.d /etc/systemd/journald.conf.d
sudo install -m 0644 pi-files/etc/systemd/system/bluealsa.service.d/override.conf /etc/systemd/system/bluealsa.service.d/
sudo install -m 0644 pi-files/etc/systemd/system/shairport-sync.service.d/override.conf /etc/systemd/system/shairport-sync.service.d/
sudo install -m 0644 pi-files/etc/systemd/journald.conf.d/xsb-persistent.conf /etc/systemd/journald.conf.d/
sudo install -m 0644 pi-files/etc/shairport-sync.conf /etc/shairport-sync.conf
sudo install -m 0644 pi-files/etc/dbus-1/system.d/shairport-sync-dbus.conf /etc/dbus-1/system.d/
sudo install -m 0644 pi-files/etc/alsa/conf.d/60-xsb.conf /etc/alsa/conf.d/
sudo install -m 0644 pi-files/etc/modules-load.d/xsb-aloop.conf /etc/modules-load.d/
sudo install -m 0644 pi-files/etc/modprobe.d/xsb-aloop.conf /etc/modprobe.d/
sudo install -m 0644 pi-files/etc/NetworkManager/conf.d/60-xsb-wifi-powersave.conf /etc/NetworkManager/conf.d/
sudo install -m 0600 pi-files/etc/NetworkManager/system-connections/xsb-usb-rescue.nmconnection /etc/NetworkManager/system-connections/
visudo -cf pi-files/etc/sudoers.d/xsb-shairport && \
  sudo install -m 0440 -o root -g root pi-files/etc/sudoers.d/xsb-shairport /etc/sudoers.d/
sudo systemctl daemon-reload
```

### 6. 开启 USB 设备模式和 UAC2 名称修正

在 `/boot/firmware/config.txt` 末尾（`[all]` 段）加一行：

```
dtoverlay=dwc2,dr_mode=peripheral
```

安装 DKMS 模块，让 macOS 显示 "Xiaomi Sound Bridge" 这个名字（不装的话设备名会是 "Playback Inactive"）：

```bash
cd ~/USB-AirPlay-XiaomiSound
sudo mkdir -p /usr/src/xsb-uac2-1.0
sudo cp dkms/xsb-uac2/* /usr/src/xsb-uac2-1.0/
sudo dkms add xsb-uac2/1.0
sudo dkms install xsb-uac2/1.0 -k "$(uname -r)"
```

### 7. 配对音箱

如果两台音箱组成了米家立体声组合，先在米家 app 里解除组合。让每台音箱进入蓝牙配对模式，然后：

```bash
sudo rfkill unblock bluetooth
sudo bluetoothctl
```

```
agent NoInputNoOutput
default-agent
scan on
# 记下两台音箱的 MAC 地址，然后：
pair LEFT_MAC
trust LEFT_MAC
pair RIGHT_MAC
trust RIGHT_MAC
scan off
exit
```

### 8. 配置

```bash
sudo install -d /etc/xsb
sudo install -m 0644 pi-files/etc/xsb/xsb.conf.example /etc/xsb/xsb.conf
sudo nano /etc/xsb/xsb.conf     # 填写 LEFT_MAC= 和 RIGHT_MAC=
```

模板里的其他取值就是上面测试用的值。

### 9. 启用服务并重启

```bash
sudo systemctl enable bluealsa nqptp shairport-sync \
  xsb-gadget xsb-usb-forward xsb-bt-reconnect xsb-volume-sync \
  xsb-bt-stream@left xsb-bt-stream@right xsb-bt-sniff \
  xsb-bt-watchdog xsb-rescue
sudo reboot
```

### 10. 检查

开机约一分钟后，两台音箱会自动连上。然后执行：

```bash
bluealsa-cli list-pcms                 # 每台音箱一行 .../a2dpsrc/sink
for p in $(bluealsa-cli list-pcms | grep a2dpsrc); do bluealsa-cli codec "$p" -v | tail -1; done
```

两行都应以 `Selected codec: AAC` 开头。如果某台显示 SBC，在没有播放时切换一次即可（BlueZ 会记住这个选择）：

```bash
bluealsa-cli codec /org/bluealsa/hci0/dev_XX_XX_XX_XX_XX_XX/a2dpsrc/sink AAC
```

在电脑上选择 "Xiaomi Sound Bridge" 作为输出（喇叭图标的那个，不是 AirPlay 图标的那个），放点声音。`journalctl -u xsb-bt-stream@left -n 5` 应该能看到 "woke left link" 和 "source playing: starting the left stream"。

### 校准左右对齐

两台音箱各自缓冲，开始出声的时刻可能不一样。把麦克风放在听音位置，只在左声道放一个短促的"嘀"、再只在右声道放一个，比较两者到达的时间。哪台早，就把它的 `LEFT_LATENCY_MS` 或 `RIGHT_LATENCY_MS` 加上这个差值（参考环境用的是 80 和 100），然后：

```bash
sudo systemctl restart xsb-bt-stream@left xsb-bt-stream@right
```

## 配置项

`/etc/xsb/xsb.conf`（模板：`pi-files/etc/xsb/xsb.conf.example`）：

| 配置项 | 使用的值 | 含义 |
|---|---|---|
| `LEFT_MAC`、`RIGHT_MAC` | 你的音箱 | 蓝牙地址 |
| `BLUEALSA_OPTS` | `-c AAC --aac-bitrate=112000 --sbc-quality=low --a2dp-force-mono --a2dp-volume --initial-volume=50 --keep-alive=5` | AAC 单声道 112 kbps，绝对音量 |
| `LEFT_LATENCY_MS`、`RIGHT_LATENCY_MS` | `80`、`100` | 每台音箱的缓冲，用于对齐 |
| `AUDIO_LATENCY_MS` | `50` | USB 转发缓冲 |
| `BT_IDLE_STOP_S` | `15` | 音源停止多久后停掉蓝牙推流 |
| `BT_IDLE_SNIFF_MS` | `2000` | 链路空闲多久后进入 sniff 省电 |

修改后重启推流服务；修改 `BLUEALSA_OPTS` 后还要重启 `bluealsa`。

## 致谢与许可证

GPL-3.0，见 `LICENSE`。基于 [shairport-sync](https://github.com/mikebrady/shairport-sync)、[NQPTP](https://github.com/mikebrady/nqptp)、[BlueALSA](https://github.com/arkq/bluez-alsa) 和 [fdk-aac](https://github.com/mstorsjo/fdk-aac)。USB gadget 脚本改编自 [batubozkan/pods-head-tracker](https://github.com/batubozkan/pods-head-tracker)（GPL-3.0）。`dkms/xsb-uac2` 在编译时修改内核的 `f_uac2.c`（GPL-2.0），仓库中不包含内核源码。


## 实验性 AirPlay 2 Realtime 发送路径

新增独立、可切换的 USB → AirPlay 2 Realtime Wi-Fi 立体声方案；原蓝牙方案不变。
使用现有 USB Gadget 和 `alsaloop -S 1` 时钟补偿，新增有界 PCM 队列和
`cliairplay` 实时发送端。**300 ms 是发送端请求的队列深度，并非已实测的声学端到端延迟。**
需要兼容 CPU/系统架构的 `cliairplay`，并验证小米原生立体声组支持 Realtime type 96。
安装、切换与测试详见 [ap2-realtime/README.md](ap2-realtime/README.md)。
