# 对分易自动签到工具（Duifene Auto Sign）

> ⚠️ **免责声明 / Disclaimer**
>
> 本项目**仅供学习与技术研究使用**，请严格遵守所在学校的规章制度与「对分易」平台的用户协议。
> 严禁用于任何违反校规校纪、考勤纪律或法律法规的用途。
> 因不当使用本项目造成的一切后果（包括但不限于：考勤违规、学籍处分、法律责任）由使用者本人承担，**项目作者不承担任何责任**。
>
> This project is **for learning and research purposes only**. Please comply with your school's rules and the Duifene platform's terms of service. **The author assumes no responsibility for any misuse or consequences arising from the use of this software.**

---

## 📖 项目简介

「对分易自动签到」是一个基于 Python + CustomTkinter 的桌面端自动签到工具，模拟登录对分易平台、轮询课程签到活动，并在合适的时机自动完成签到。

本项目在功能与流程上**部分参考了上游开源项目**：
👉 [liuzhijie443/duifene_auto_sign](https://github.com/liuzhijie443/duifene_auto_sign)

实际签到流程的请求、解析、调度逻辑为本项目独立实现.

> 📝 **关于签到日志**：运行过程中，日志可能会偶尔显示「过期签到」的提示信息，但实际上程序仍可正常检测到活跃的签到活动并完成签到。建议您根据实际情况判断签到状态，无需因该提示而过度担忧。

---

## 🆕 2026 年 5 月机制更新适配

对分易平台在 **2026 年 5 月** 对签到接口做了一次较大更新，本项目针对新机制做了如下适配：

| 适配点 | 说明 |
|--------|------|
| **双 UA 切换** | 同时维护桌面端 UA（`Chrome/148`）与微信端 UA（`MicroMessenger`），不同请求按接口要求分别携带 |
| **Session 静默过期修复** | 微信链接登录时偶发服务端不返回新 session 也不报错，程序会在心跳中自动识别 `未登录` 状态并停止监听，避免「以为登录成功、实际静默失效」导致漏签 |
| **空值/缺字段容错** | 5 月更新后部分接口会缺字段（如 `HFCheckCodeKey`、`HFRoomLongitude`），程序统一判空后跳过本轮，不再崩溃 |
| **布尔配置丢失修复** | 之前版本偶发将 `True/False` 写入 ini 后下次读回变成 `0/1` 导致逻辑错误，现已统一序列化方式 |
| **高刷监听节流** | 默认 1.0 – 3.0 秒随机间隔，避开 5 月新增的请求频率检测；提供「快速 / 标准 / 省电」三档预设 |
| **定时窗口** | 可设置「仅在 8:00 – 18:00 监听」，防止非上课时段被风控标记 |
| **定位签到** | 仅在课程为「定位签到」类型时触发，按你预设的「坐标1/坐标2」原样提交经纬度（签到日志会打印实际使用的坐标，便于核对是否生效）。坐标随机抖动为计划中功能，当前版本未启用 |
| **过半签到模式** | 监听全班签到人数，达到半数后才自动签到（🚧 正在编写中，暂未启用） |

> 💡 如果你在 5 月之前使用过本项目，**请直接拉取最新 `main.py` 覆盖即可**，无需改动 ini 配置。

---

## ✨ 功能特性

- ✅ **微信链接登录** / **账号密码登录**（双模式）
- ✅ **二维码签到**、**签到码签到**、**定位签到**（定位按预设坐标提交，日志打印实际坐标）
- ✅ **课程下拉选择**，自动加载本学期所有课程
- ✅ **异步高刷监听**（UI 不卡顿、session 线程安全）
- ✅ **倒计时阈值**：检测到签到后延迟自定义秒数再触发，避免抢签；在「延迟签到」输入框填秒数即可（空或 0 表示立即签）
- 🚧 **过半签到模式**：监听全班签到人数，达到半数后才自动签到（**正在编写中，当前版本未启用**）
- ✅ **定时窗口**：仅在指定时间段监听
- ✅ **后台静默运行**（`pythonw` + 互斥锁防多开）
- ✅ **配置 base64 混淆保存**（账号/密码/Cookie 不以明文落盘；注意 base64 非加密，仅防肉眼直读）
- ✅ **日志面板** + 实时状态条 + 24h 定时时间轴

---

!!!!!--提醒：日志有时候显示会和活跃签到有偏差，有时候会产生对过去签到的显示，但不影响实际活跃签到，请自行合理判断日志。

## 🛠️ 技术栈

- **语言**：Python 3.10+
- **GUI**：[`customtkinter`](https://github.com/TomSchimansky/CustomTkinter) 5.2+
- **HTTP**：[`requests`](https://requests.readthedocs.io/) 2.31+
- **解析**：[`beautifulsoup4`](https://www.crummy.com/software/BeautifulSoup/) + `lxml`
- **二维码解码**：[`Pillow`](https://python-pillow.org/) + [`pyzbar`](https://github.com/NaturalHistoryMuseum/pyzbar)（解析二维码签到图片）
- **打包**：[`PyInstaller`](https://www.pyinstaller.org/) + `upx`
- **平台**：Windows 10 / 11（依赖 `pywin32` 互斥体）

---

## 📦 安装

```bash
# 1. 克隆仓库
git clone https://github.com/wh520-wh/wh-duifene-auto-sign.git
cd wh-duifene-auto-sign

# 2. （可选）创建虚拟环境
python -m venv .venv
.venv\Scripts\activate

# 3. 安装依赖
pip install -r requirements.txt
```

> 💡 **二维码签到额外依赖**：`requirements.txt` 已包含 `pillow` 与 `pyzbar`。
> 其中 `pyzbar` 在 **Windows** 上还需安装微软的「**Visual C++ Redistributable for Visual Studio 2013**」运行库，
> 否则导入时会报 `FileNotFoundError: Could not find module 'libzbar-64.dll'`，导致二维码签到静默失效。

---

## 🚀 使用方法

### 方式一：源码运行

```bash
python main.py
```

### 方式二：后台静默运行（推荐日常使用）

双击 `启动.bat`，程序会以 `pythonw` 启动，**不显示命令行窗口**。

### 方式三：打包为单文件 EXE

```bash
pyinstaller 对分易自动签到.spec
# 产物在 dist/对分易自动签到.exe
```

---

## ⚙️ 配置文件

首次运行会在同目录生成 `duifenyi.ini`，包含账号、Cookie（很快过期）、监听参数等。**示例模板见 [`config.ini.example`](./config.ini.example)**。

```ini
[ACCOUNT]
login_mode = 微信登录   # 微信（链接）登录 / 账号登录
username =
password =

[SETTINGS]
interval_preset = 标准  # 快速 / 标准 / 省电
interval_min = 1.0
interval_max = 3.0
time_schedule = 0       # 是否启用定时窗口 0/1
start_time = 08:00
end_time = 18:00
log_mode = 精简         # 精简 / 详细 / 调试
active_coord = 1        # 当前生效坐标组 1/2
coord_jitter = 0        # 定位坐标随机抖动 0/1（开启后 ≤5 米随机偏移，默认关）
lon_1 = 113.123456      # 坐标1 经度
lat_1 = 23.654321       # 坐标1 纬度
lon_2 =                 # 坐标2 经度（可空）
lat_2 =                 # 坐标2 纬度（可空）
selected_course_id =    # 上次选中的课程ID（程序自动维护）
selected_course_name =  # 上次选中的课程名（程序自动维护）
```

---

## 🗂️ 目录结构

```
wh duifene-auto-sign/
├── main.py                  # 主程序（6.5.1-dark-ultimate-async）
├── requirements.txt         # 依赖列表
├── 启动.bat                 # Windows 后台启动脚本
├── 对分易自动签到.spec      # PyInstaller 打包配置
├── 对分易自动签到_Pro.spec  # PyInstaller 打包配置（Pro 版）
├── logo.ico                 # 程序图标
├── config.ini.example       # 配置模板（提交到仓库）
├── README.md                # 本文件             
└── _archive/                # 本地备份（不提交）
    ├── config.ini.bak
    ├── duifenyi.ini.bak
    └── secret.key.bak
```

---

## 🙏 致谢

- **微信链接来源**：[liuzhijie443/duifene_auto_sign](https://github.com/liuzhijie443/duifene_auto_sign) — 仅复用了其中的微信 OAuth 引导链接，实际签到流程的请求/解析/调度逻辑完全为本项目独立实现
- **GUI 框架**：[CustomTkinter](https://github.com/TomSchimansky/CustomTkinter)

---

## 📄 许可证

本项目采用 **MIT License** 发布。请在遵守当地法律、学校规定与平台用户协议的前提下使用。

```
MIT License

Copyright (c) 2026 wh520-wh

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

**再次提醒：本工具仅供学习参考，如非法使用后果自负。**
