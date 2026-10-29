---
name: super-auto-web-toolpack
description: "分层网页与小程序数据抓取工具包。网页按成本递增自动升级反爬对抗等级：T0 curl_cffi（TLS/JA3 指纹直连）→ T1 patchright（补丁版 Playwright，默认起步层）→ T2 强指纹浏览器（cloakbrowser / camoufox）；硬拦截（Cloudflare/DataDome/Akamai/五秒盾）与软限制（内容降级、需 JS、响应过慢）均触发升级。微信小程序单列 W 层：W0 包盘点（只读）→ W1 官方 SDK miniprogram-automator → W2 MITM 抓包（mitmproxy，含凭据脱敏）→ W3 反编译适配与静态接口提取。内置反爬信号检测、站点画像记忆、同域限速、robots.txt 预检、耗时相对基线。当需要抓取网页数据、被反爬拦截、页面需 JS 渲染、被限流、返回内容不完整、或数据在微信小程序（wx.request 接口）里时使用。"
display_name: "Super auto-web toolpack"
display_name_en: "Super auto-web toolpack"
version: 2.0.0
agent_created: true
license: MIT
tags: [web-scraping, scraping, crawler, anti-bot, patchright, curl_cffi, cloakbrowser, camoufox, cloudflare, data-extraction, miniprogram, wechat, wxapkg, mitmproxy, 网页抓取, 爬虫, 反爬, 数据采集, 分层降级, 微信小程序, 抓包, 反编译, super-auto-web-toolpack]
---

# Super auto-web toolpack · 分层网页抓取工具包

**核心模型：成本递增的四级降级链。永远先试最轻的手段，被拦再升。**

| 层级 | 手段 | 能耗掉什么防护 | 成本 | 默认 |
|---|---|---|---|---|
| **T0** | `curl_cffi`（TLS/JA3 + HTTP2 指纹伪装） | 无防护站、JA3 指纹校验 | ~0.2–1s | 起跳层 |
| **T1** | `patchright`（补丁版 Playwright + 真实 Chrome） | JS 挑战、无头检测、动态渲染 | 5–8s | **默认起步层** |
| **T2** | `cloakbrowser` / `camoufox`（源码级指纹） | DataDome / 强指纹站点 | 5–20s | 按需 |
| **T3** | 人工介入（验证码 / 登录态 / 代理） | — | — | 报告给用户 |

> **设计要点**：T0 是"免费试探"，T1 是"主力"，T2 是"重武器"。不无脑上重武器，才是"不引起反爬"的工程解法。

### W 层：微信小程序（另一套技术栈）

小程序**不能复用 T0–T3**——`wx.request` 不走浏览器网络栈，请求由微信客户端
发起、带小程序专属签名，且在 WebView 之外。浏览器自动化与 TLS 指纹伪装在这里**全部失效**。

| 层级 | 手段 | 前提条件 | 合规 |
|---|---|---|---|
| **W0** | 包盘点 `mp_discover.py` | 装了 PC 微信 | 🟢 纯只读 |
| **W1** | 官方 SDK `mp_automator.py` | 微信开发者工具 + 源码项目 | 🟢 不逆向 |
| **W2** | MITM 抓包 `mp_capture.py` | mitmproxy + 证书 | 🟢 抓自己流量 |
| **W3** | 反编译 `mp_unpack.py` | 解密/解包工具 | 🟡 属逆向 |

> ⚠️ **最常见误解**：以为装了开发者工具 + `miniprogram-automator` 就能抓任意小程序。
> **不能。** 官方 SDK 只能操作「开发者工具里加载的项目」，线上小程序没有源码无法加载。
> **抓线上必须走 W2 或 W3。**
>
> 📖 详尽的路线选择、V1MMWX 加密现实、工具真伪核实见 `references/miniprogram.md`

### 两类失败，都要升级

| 类型 | 表现 | 例子 | 处理 |
|---|---|---|---|
| **硬拦截** | 明确拒绝 | 403 / 429 / Cloudflare 挑战 / 验证码 | 逐级升级 |
| **软限制** | 返回 200，看着成功，但"少给" | 内容降级、需登录才完整、需 JS、**响应过慢** | **同样逐级升级**——静默的不完整数据比报错更危险 |

> **例外（合规红线）**：软**付费墙**与软**登录墙**属于访问控制，**不升级**——换指纹解决不了，且越过了本工具的边界，直接报给用户。

---

## 何时使用

- 需要抓取网页数据 / 采集内容 / 监控页面变化
- 直接请求被拦截（403 / 429 / 验证码 / 空白页）
- 目标页面依赖 JavaScript 渲染，静态抓取拿不到内容
- 需要在不引起封禁的前提下采集（个人研究、低频取数）
- **数据在微信小程序里**（`wx.request` 接口）→ 走 W 层

**不适用**：需要绕过硬付费墙；需要破解验证码；需要提取凭据 / cookie / token。

---

## 执行流程

### Step 0 · 安装依赖（首次使用）

**务必在虚拟环境中安装**，避免污染全局 Python 环境：

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate

pip install curl_cffi patchright      # T0 + T1（必备）
python -m patchright install chromium # 或复用系统 Chrome 免下载

# 或一键安装全部分层依赖（版本组合已实测固化，含版本张力说明）：
pip install -r <SKILL_DIR>/requirements.txt
```

依赖均来自 **PyPI 官方源**。详细的备份、许可与体积对照见 `references/adapters.md`。

#### T2 安装（可选，重武器）

T2 分**两步**，且第二步常被忽略——**装了 pip 包 ≠ 能跑**：

```bash
# 第一步：装 Python 包
pip install cloakbrowser              # 或 pip install camoufox

# 第二步：下载各自改造过的浏览器二进制（关键，且体积很大）
python -c "import cloakbrowser; cloakbrowser.ensure_binary()"   # ~536 MB
python -m camoufox fetch                                        # ~200 MB
```

| 后端 | 二进制体积 | 下载源 |
|---|---|---|
| cloakbrowser | **约 536 MB** | `cloakbrowser.dev` → GitHub Release CDN |
| camoufox | 约 200 MB | GitHub Release CDN |

> ⚠️ **网络受限环境注意**：二进制走 GitHub Release 资产 CDN（`release-assets.githubusercontent.com`）。
> 若该域名被代理/防火墙拦截，下载会**卡住不动**（表现为速度几百 B/s）。
> 此时请换网络、配置代理，或把 `--max-tier` 降到 1。
> 用 `python scripts/doctor.py` 可精确看到二进制是否就绪。

### Step 0b · 环境体检（首次使用，或报错时）

```bash
python <SKILL_DIR>/scripts/doctor.py
```

逐项报告依赖状态并给出**编号修复建议**。按编号处理，处理完重跑。

### Step 1 · 自测（可选，确认断言逻辑没坏）

```bash
python <SKILL_DIR>/scripts/selftest.py          # 17 项检测断言
python <SKILL_DIR>/scripts/stealth_check.py     # 浏览器隐身指纹自检
```

### Step 2 · 抓取（主命令）

```bash
python <SKILL_DIR>/scripts/fetch.py <URL> [选项]
python <SKILL_DIR>/scripts/fetch.py --urls-file urls.txt [选项]
```

**常用组合：**

| 目的 | 命令 |
|---|---|
| 默认：自动分层升级 | `fetch.py "<URL>"` |
| 只要纯文本 | `fetch.py "<URL>" --text` |
| 只到 T1，不上 T2 | `fetch.py "<URL>" --max-tier 1` |
| 已知是静态站，省时间 | `fetch.py "<URL>" --only-tier 0` |
| 强制从 T1 起步 | `fetch.py "<URL>" --start-tier 1` |
| 内容必须含某关键词 | `fetch.py "<URL>" --require "关键词"` |
| 内容下限（低于即升级） | `fetch.py "<URL>" --min-text 2000` |
| 响应过慢即升级（固定阈值） | `fetch.py "<URL>" --slow-ms 8000` |
| 调松/收紧判慢倍数 | `fetch.py "<URL>" --slow-factor 5` |
| 查看耗时基线 | `fetch.py --baselines` |
| 挑战页等待时长 | `fetch.py "<URL>" --challenge-wait 25000` |
| 站点横向对比 | `benchmark.py --urls-file sites.txt --md out.md` |
| 走代理 | `fetch.py "<URL>" --proxy http://127.0.0.1:7890` |
| 机器可读输出 | `fetch.py "<URL>" --json` |
| 看后端可用性 | `fetch.py --backends` |

**退出码**：`0` 成功 ｜ `1` 一般失败 ｜ `2` 被拦且已达最高层级（需人工/代理）｜ `3` 依赖缺失

### Step 3 · 阅读结果

结果同时：① 打印到终端；② 按 `输出目录/<域名>/<日期>/data-<时间>.json|csv` 落盘。
失败时终端会打印**层级尝试记录**，直接告诉你每一层失败在哪。

---

## 铁律（每次抓取必守）

1. **默认有头模式。** 实测：无头模式的 UA 会暴露 `HeadlessChrome`，在 bot.sannysoft.com 上多失败 3 项（54/57）；有头模式 57/57 满分。**只有目标站无防护时，才用 `--headless` 换速度。**
2. **不要自定义 User-Agent。** patchright 官方明确要求：额外 UA / header 会**增加**指纹熵，反而更易被识别。
3. **T2 是重武器，按需才上。** 能 T1 过就不要 T2——慢且会下载大体积浏览器二进制。
4. **不要跳过 robots 预检。** 默认开启；确实需要跳过时用 `--no-robots` 并自行确认站点条款。
5. **命中验证码不要硬刚。** 检测到 `captcha` / `turnstile` 类，工具会提示需人工介入——交给人处理，不要重试轰炸。
6. **登录墙升级无效。** 检测到 `login-wall` 时工具不会升级，因为换指纹解决不了登录问题。
7. **批量抓取保留限速。** 默认同域 1s 间隔 + ±20% 抖动；调低 `--min-interval` 前想清楚封禁风险。
8. **软限制必须当失败处理，不能当成功。** 返回 200 但内容降级/偏少/响应过慢，工具会标记 `soft_limited` 并升级。**没升级到顶就把 `soft_limited=true` 的结果当"完成"用，是会出事的。**
9. **软付费墙/软登录墙不升级。** 那是访问控制，不是技术防护——工具会停在原地报给你，需要你自己确认是否有权访问。
10. **判慢用相对基线，不要设固定阈值给浏览器层。** 浏览器层本身就要 5–8 秒，固定阈值必然误报。工具默认按「本站该层历史中位数 × 3」判断，冷启动才回退固定值。用 `--baselines` 可查看当前基线。
11. **命中挑战页要先等，别急着报"被拦"。** 实测 Cloudflare Turnstile 的非交互挑战**约 9 秒后自动放行**。工具默认最多等 15 秒（`--challenge-wait`）。等太短会把"慢放行"误报成"被拦"，导致白白升级到更重的层级——这类误判比漏报更贵。
12. **T2 不一定比 T1 强。** 实测 GSMArena + DXOMARK：T1 patchright 成功率并列最高、**快 2–4 倍**、内容量并列最高。T2 是"T1 失败时的备选"，不是"更强的 T1"。**顺序永远是 T1 → T2。**

---

## 负向规则表（禁止做的事）

| 禁止 | 后果 | 正确做法 |
|---|---|---|
| 一上来就跑 T2 | 慢、需下载大二进制、过度杀伤 | 从 T0/T1 起步，让工具自动升级 |
| 无头模式 + 强反爬站 | UA 暴露 HeadlessChrome，直接被识破 | 保持默认有头；或改用 `--headless` 仅限无防护站 |
| 自定义 UA / 加 header | 指纹熵升高，反而更易被识别 | 交给 patchright 默认配置 |
| 忽略 `blocked` 标记 | 把验证码页/空壳页当数据落盘 | 检查结果里的 `blocked` / `block_vendor` |
| 用 `--only-tier 0` 抓 SPA | 拿到空壳，误以为"没数据" | 用默认分层，或 `--require` 断言内容 |
| 命中验证码后循环重试 | 加速封禁 | 停下来，交人工，或换代理/降频 |
| 用 `--min-interval 0` 高频轰炸 | 被封 IP | 保持 ≥1s；批量任务调大 |

---

## 故障速查

| 现象 | 原因 | 处理 |
|---|---|---|
| `退出码 3` / `no_backend_available` | 该层依赖未装 | `pip install curl_cffi patchright` 后 `python -m patchright install chromium` |
| T1 报 `Executable doesn't exist` | patchright 浏览器未下载 | `python -m patchright install chromium` |
| 一直停在 T0 拿不到内容 | 页面靠 JS 渲染 | 用默认分层（会自动升 T1），或 `--start-tier 1` |
| T1 也失败，报 Cloudflare/DataDome | 指纹强度不够 | 升 T2：`pip install cloakbrowser` 或 `camoufox`，然后 `--tier2 cloakbrowser` |
| **T2 报「二进制缺失」** | **pip 装了包，但没下浏览器二进制** | 运行 `python -c "import cloakbrowser; cloakbrowser.ensure_binary()"`（536MB）或 `python -m camoufox fetch` |
| **二进制下载卡住（速度仅几百 B/s）** | 下载源 `release-assets.githubusercontent.com` 被网络拦截 | 换网络 / 配 `--proxy` / 把 `--max-tier` 降到 1；跑 `doctor.py` 确认 |
| 抓取成功但标记 `soft_limited` | 命中软限制，内容可能不完整 | 看 `soft_reasons`；`--require` 精确断言；或确认已升到最高层 |
| **停在 Turnstile 挑战页（48 字符）** | 挑战未放行，或该后端被持续挑战 | 先加大 `--challenge-wait`；若某后端**反复**落在挑战页（如 camoufox 遇严格 Cloudflare），换 `--tier2 cloakbrowser` |
| 全层级失败但各层拿到过内容 | 没有一层完全达标 | 工具会**兜底返回内容最多的一次**（标记 `best_effort`）——不完整数据胜过空手而归 |
| 报 `connection failed` / CONNECT 502 | 本机代理拦截该域名 | 用 `--proxy` 指定可用代理；或确认域名可达 |
| `robots.txt 拒绝` | 站点规定禁止 | 尊重它；确有正当理由再 `--no-robots` |
| 结果 `blocked=true` 但内容看着正常 | 页面含验证码控件等特征 | 看 `block_vendor` 判断；必要时提高 `--min-interval` |
| 反复被限流 | 请求过快 | 调大 `--min-interval`；考虑换代理 |

---

## 参考资料（按需读取）

| 文件 | 何时读 |
|---|---|
| `references/tiers.md` | 需要了解各层能过什么、反爬信号词表、升级判定规则、相对基线 |
| `references/adapters.md` | 需要安装/配置 T1、T2 后端；patchright 最佳实践与实测数据 |
| `references/compliance.md` | 抓取前确认合规边界与自我约束 |
| `references/benchmark-findings.md` | **选型前必读**：GSMArena/DXOMARK 真实站点实测结论（含"T2 不一定比 T1 强"的证据） |
| `references/miniprogram.md` | **小程序抓取必读**：W 层四条路线的选择、V1MMWX 加密现实、反代理对策、工具真伪核实表 |

---

## W 层命令速查（微信小程序）

| 目标 | 命令 |
|---|---|
| 盘点本机小程序包（**纯只读**） | `python scripts/mp_discover.py` |
| W1 环境检测 | `python scripts/mp_automator.py check` |
| W1 生成测试项目（TDesign，游客模式） | `python scripts/mp_automator.py scaffold` |
| W1 手动构建 miniprogram_npm（免 IDE 点击） | `python scripts/mp_automator.py build-npm --project <目录>` |
| W1 生成自动化脚本 | `python scripts/mp_automator.py init --project <目录>` |
| W1 操作指引 | `python scripts/mp_automator.py guide` |
| W1 一键断言（装好工具+扫码后） | `node ~/.super-auto-web-toolpack/mp_automator/demo_test.js` |
| W2 生成抓包工作区 | `python scripts/mp_capture.py init` |
| W2 启动抓包 | `python scripts/mp_capture.py run --dir <工作区>` |
| W2 汇总 API 清单 | `python scripts/mp_capture.py report --dir <工作区>` |
| W3 检测解包工具 | `python scripts/mp_unpack.py tools` |
| W3 工具链指引 | `python scripts/mp_unpack.py guide` |
| W3 静态提取接口 | `python scripts/mp_unpack.py scan --dir <解包目录>` |

---

## 状态文件

默认位于 `~/.super-auto-web-toolpack/`（可用 `SAWT_STATE_DIR` 覆盖）：

| 状态文件 | 内容 |
|---|---|
| `site_profiles.json` | **站点画像**——每个域名"上次走通的层级"，下次直接从该层起步 |
| `latency_baseline.json` | **耗时基线**——按 `域名\|层级` 记录历史耗时，用于判断"相对变慢" |
| `fetch_history.jsonl` | 抓取历史（不含正文），便于回溯 |
| `profiles/patchright/` | patchright 持久化配置目录（保留登录态） |
| `mp_capture/<时间戳>/` | W2 抓包工作区（addon + 脱敏后的 `api.jsonl`） |
| `mp_automator/` | W1 自动化脚本工作区（Node 项目） |

> 站点画像让重复抓取免去"每次从 T0 重试"。若某站突然开始拦截，删除对应域名条目即可回到从 T0 起跳。
