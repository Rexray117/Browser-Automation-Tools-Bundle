# 后端安装、适配与实测数据

## 一、安装

> **务必使用虚拟环境隔离。** 本工具包依赖体积较大（浏览器二进制 150–200MB），
> 直接在系统/全局 Python 里安装可能与其他项目产生依赖冲突。

```bash
# 1) 创建并激活独立虚拟环境（推荐）
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate

# 2) 在虚拟环境内安装
# T0（必装，小）
pip install curl_cffi

# T1（必装，默认起步层）
pip install patchright
python -m patchright install chromium     # 下载 Chromium（约 150MB）
# 或直接复用系统 Chrome（更推荐，见下）

# 3) T2（按需）—— 分两步！装包 ≠ 能跑
pip install cloakbrowser
python -c "import cloakbrowser; cloakbrowser.ensure_binary()"   # 下载二进制 ~536MB

pip install camoufox
python -m camoufox fetch                                        # 下载 Firefox 构建 ~200MB
```

依赖全部来自 **PyPI 官方源**，不涉及任何第三方镜像或 git 源。

装完跑一次体检：

```bash
python scripts/doctor.py
```

---

## 二、T0 · curl_cffi

```python
from curl_cffi import requests as creq
r = creq.get("https://example.com", impersonate="chrome", timeout=30)
```

**要点**
- `impersonate="chrome"` 复刻 Chrome 的 TLS/JA3 + HTTP/2 指纹
- 不执行 JS，拿不到前端渲染内容
- 被 403/429 或内容为空 → 升级到 T1

**适用信号**：站点只有 UA/TLS 层校验；或站点本就无防护（多数文档站、博客、公开 API）。

---

## 三、T1 · patchright（默认起步层）

patchright 是**打过补丁的 Playwright**，用来消掉 Playwright 的自动化痕迹。它"换 import 即用"。

### 3.1 官方最佳实践（务必遵守）

```python
playwright.chromium.launch_persistent_context(
    user_data_dir="...",     # 持久化配置目录（保留登录态、避免每次都像新访客）
    channel="chrome",        # 用真实 Chrome，而非 Chromium
    headless=False,          # ← 关键：必须有头
    no_viewport=True,        # 不要固定 viewport（固定值是明显的自动化特征）
    # 绝对不要加自定义 user_agent / 额外 header
)
```

### 3.2 为什么必须"有头"——实测数据

在本机实测（`bot.sannysoft.com`，公开反爬指纹测试页）：

| 模式 | 结果 | 泄漏项 | UA |
|---|---|---|---|
| `headless=True` | **54 通过 / 3 失败** | `User Agent (Old)`、`HEADCHR_UA`、`CHR_MEMORY` | `...HeadlessChrome/154.0.0.0...` |
| `headless=False` | **57 通过 / 0 失败** | — | `...Chrome/154.0.0.0...` |

**结论**：无头模式的 UA 直接写着 `HeadlessChrome`，是最廉价也最有效的识别点。**有头是 T1 生效的前提**，不是可选项。

> 这也解释了为什么 patchright 官方把 `headless=False` 写进"best practice"。
> 但官方同时说"不要自定义 UA" —— 所以正确解法是**有头**，而不是手动改 UA 掩盖。

### 3.3 patchright 到底补了什么

| 泄漏点 | patchright 的处理 |
|---|---|
| `Runtime.enable` 泄漏（最大的一处） | 改为在隔离的 ExecutionContext 中执行 JS，不再调用 `Runtime.enable` |
| `Console.enable` 泄漏 | 直接禁用 Console API —— **代价：patchright 里 `console` 不可用** |
| 命令行开关泄漏 | 补 `--disable-blink-features=AutomationControlled`；去掉 `--enable-automation` |
| Closed Shadow DOM | 支持用普通 locator 穿透 |

**限制**：只支持 Chromium 系（Chromium / Chrome / Edge），**不支持 Firefox 和 WebKit**。

### 3.4 本项目实测通过的场景

```
T0 curl_cffi   → 85 字符（JS 空壳，判定内容为空）
T1 patchright  → 1567 字符（[chrome]，有头，~6s）✓
```

---

## 四、T2 · cloakbrowser

```python
from cloakbrowser import launch
browser = launch(humanize=True, proxy="socks5://user:pass@host:1080", geoip=True)
page = browser.new_page()
```

**要点**
- **不是 JS 注入，是 87 处 C++ 源码级补丁**（canvas / WebGL / audio / fonts / GPU / WebRTC / 网络时序 / CDP 输入行为）
- `humanize=True`：鼠标走贝塞尔曲线、逐字符输入、拟人滚动 —— 专治**行为检测**
- **二进制约 537MB**，需显式触发下载（`cloakbrowser.ensure_binary()`）；二进制带 **Ed25519 签名校验**，下载来源本身无法单独冒充
- 免费档可用（`binary_info()` 返回 `tier="free"`）；`launch()` 也接受 `license_key` 参数，存在付费档位。免费档提示"1 concurrent session"
- **会伪造硬件身份**：实测 cloakbrowser 报 `RTX 5070 Ti / 8 核`，而 patchright 报真实的 `RTX 3060 / 16 核` —— 这正是它的设计目的
- Python 与 JS 双语言，Playwright / Puppeteer 均可直接换 import
- 支持 `launch_persistent_context()`（保留 cookie/localStorage，规避隐身模式检测）
- **实测战绩**：bot.sannysoft.com **57 通过 / 0 失败**；首次启动约 32s（解包+初始化），后续更快

用 `binary_info()` 可随时查看状态而不触发下载：

```python
import cloakbrowser, json
print(json.dumps(cloakbrowser.binary_info(), indent=2, ensure_ascii=False))
# → {"version": "146.0.7680.177.5", "tier": "free", "installed": false,
#    "binary_path": "...\\.cloakbrowser\\chromium-146.0.7680.177.5\\chrome.exe",
#    "download_url": "https://cloakbrowser.dev/chromium-v146.../cloakbrowser-windows-x64.zip"}
```

---

## 五、T2 · camoufox

```python
from camoufox.sync_api import Camoufox
with Camoufox(headless=True) as browser:
    page = browser.new_page()
```

**要点**
- **Firefox fork**，指纹在 **C++ / Juggler 引擎层**修改，而不是注入 JS
- 为什么这重要：多数反检测方案靠 JS 覆写 `navigator.webdriver` 等属性，而**"属性被改过"本身就是信号**（风控会检测原生属性是否被篡改）。Camoufox 从 C++ 层改，JS 侧看不到痕迹
- **每次启动随机化身份**：实测两次运行分别报为 `Linux x86_64 / Firefox 156` 与 `Windows NT 10.0 / Firefox 156`，WebGL 厂商也随之变化
- **没有 `window.chrome`** —— 这是正常的（它是 Firefox，不是 Chromium）。用 `stealth_check.py` 自检时该项会自动跳过
- 需先 `python -m camoufox fetch` 下载，**实测体积约 2.5GB**（含 Firefox 构建 + UBO 广告拦截插件 + fpgen 指纹模型）
- ⚠️ 作者自标"**开发中，可能不适合稳定生产使用**"；许可为 **MPL-2.0**（弱 copyleft）

**实测战绩**：bot.sannysoft.com **56 通过 / 1 失败**——唯一失败项是 "Chrome (New) missing"，属 Chrome 专属项，对 Firefox 不构成实际问题。

---

## 六、选择 T2 后端

| 情况 | 选 |
|---|---|
| 目标站按 Chrome 特征风控 | `cloakbrowser` |
| 目标站对 Firefox 更宽松 | `camoufox` |
| 需要行为检测通过（鼠标/键盘/滚动） | `cloakbrowser`（`humanize=True`） |
| 需要极低内存 / 高并发 | 两者都不合适 → 考虑 Obscura（Rust，30MB/实例）或直接起 T1 多进程 |

命令行切换：

```bash
python fetch.py "<URL>" --tier2 cloakbrowser
python fetch.py "<URL>" --tier2 camoufox
python fetch.py "<URL>" --tier2 camoufox,cloakbrowser    # 按序尝试
```

---

## 七、隐身自检

改完任何后端配置，都应该跑一次：

```bash
python scripts/stealth_check.py                    # 本地指纹探针
python scripts/stealth_check.py --sannysoft        # 额外实测 bot.sannysoft.com
python scripts/stealth_check.py --headless         # 对比无头模式的暴露情况
```

本地探针检查 7 项：`navigator.webdriver`、`window.chrome`、`plugins`、`mimeTypes`、
`cdc_` 属性、Playwright 注入变量、iframe 内 `webdriver`。

---

## 八、浏览器二进制体积参考

| 后端 | 需下载 | 来源 | 备注 |
|---|---|---|---|
| curl_cffi | 无（纯 Python wheel） | PyPI | ~10MB |
| patchright + chromium | ~150MB | Playwright CDN | 或用系统 Chrome 免下载 |
| **cloakbrowser** | **537MB**（实测） | `cloakbrowser.dev` → GitHub Release CDN | 免费档 tier=free 可用；二进制带 Ed25519 签名校验；实测下载 57s |
| **camoufox** | **约 2.5GB**（实测，远不止浏览器本体） | GitHub Release CDN | 含 Firefox 构建 + UBO 广告拦截插件 + fpgen 指纹模型 |

### ⚠️ 下载源可能被拦截 —— 用代理解决

cloakbrowser 与 camoufox 的二进制最终都落在 **`release-assets.githubusercontent.com`**（GitHub Release 资产 CDN）。

在代理/防火墙拦截该域名的网络环境下，下载会**卡住而非报错**——表现为速度降到几百 B/s、长时间无进展。

**实测记录**：

| 场景 | cloakbrowser zip (537MB) | github.com 网页 |
|---|---|---|
| 直连（被拦截） | 162 bytes → 停滞（188 B/s） | 85 KB/s 正常 |
| **经代理 `127.0.0.1:7897`** | **568 KB/s，57 秒完成** | 正常 |

→ 结论：拦截发生在 release 资产 CDN，而非 github.com 本身。**配好代理即可全速下载。**

### 怎么让下载走代理

二进制下载器走标准代理环境变量，**导出后执行即可**：

```bash
# Linux / macOS / Git Bash
export HTTPS_PROXY=http://127.0.0.1:7897
export HTTP_PROXY=http://127.0.0.1:7897

# Windows PowerShell
$env:HTTPS_PROXY="http://127.0.0.1:7897"; $env:HTTP_PROXY="http://127.0.0.1:7897"

# 然后再下载
python -c "import cloakbrowser; cloakbrowser.ensure_binary()"
python -m camoufox fetch
```

> 注意：若环境里已存在指向**别的**代理的 `http_proxy`（例如沙箱自带的），
> 必须显式覆盖，否则会继续走被拦截的那条链路。

用 `python scripts/doctor.py` 可随时查看二进制就绪状态（会调用 `cloakbrowser.binary_info()` 精确报告）。

低带宽环境下，**优先复用系统已装的 Chrome**（`channel="chrome"`），可免掉 patchright 那 150MB。
