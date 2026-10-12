# 微信小程序抓取（W 层）

> 网页抓取走 T0–T3（见 `tiers.md`）。小程序是**另一套技术栈**，因此单列 W 层。
> 为什么不能复用网页那套：小程序的 `wx.request` 不走浏览器网络栈，
> 请求由微信客户端发起、带小程序专属签名，且在 WebView 之外——
> 浏览器自动化、TLS 指纹伪装这类手段**在这里全部失效**。

---

## 一、先想清楚：你要抓的是哪种小程序？

这是**最容易走错路**的地方。四条路线前提条件完全不同：

| 你的情况 | 该走 | 为什么 |
|---|---|---|
| 是你自己的小程序 / 你有源码 | **W1 官方 SDK** | 唯一不做逆向的路线 |
| 你要抓**别人线上跑着**的小程序 | **W2 抓包** 或 **W3 反编译** | 官方 SDK 根本碰不到线上小程序 |
| 只需要接口的真实响应数据 | **W2 抓包** | 直接拿到真实请求/响应 |
| 想知道签名怎么算、参数怎么拼 | **W3 反编译** | 源码里能看到算法 |
| 小程序有强反代理 / 证书固定 | **W2 + Proxifier** 或 **W3** | 见下文各自章节 |

> ⚠️ **最常见误解**：以为装了微信开发者工具 + `miniprogram-automator`
> 就能抓任意小程序。**不能。** 官方 SDK 操作的是「开发者工具里加载的项目」，
> 而线上小程序没有源码，无法加载。抓线上必须走 W2/W3。

---

## 二、W0 · 包盘点（本 skill 已有，纯只读）

```bash
python scripts/mp_discover.py                    # 盘点本机缓存的小程序
python scripts/mp_discover.py --md report.md     # 导出报告
```

**这一层没有任何风险**——只读文件元信息和文件头，不解密、不联网、不写盘。

它回答的是"我手上有什么牌"：

- 本机缓存了哪些小程序（appid / 版本 / 主包 / 分包 / 体积）
- 每个包是**加密（V1MMWX）**还是**明文**
- 微信开发者工具装了没
- 由此判定哪条路线可行

### 关于 V1MMWX：一个必须知道的现实

新版微信（4.x）的小程序包**全部加密**，文件头是 `V1MMWX`：

```
V1MMWX + 加密载荷
```

实测本机 18 个小程序 / 36 个包，**加密率 100%**。

这意味着：**老教程里"直接解包"的做法在新版微信上已经失效**。
2023 年前后停更的 `wxappUnpacker` 类工具对 V1MMWX 无效，必须先解密。

---

## 三、W1 · 官方 SDK 自动化（最合规）

**前提**（缺一不可）：
1. 安装微信开发者工具
2. 开发者工具 → 设置 → 安全设置 → 打开**「服务端口」**
3. 有目标小程序的**源码项目目录**

```bash
python scripts/mp_automator.py check                    # 环境检测
python scripts/mp_automator.py init --project "D:/mp-x" # 生成 Node 脚本
cd <工作区> && npm install && node mp_auto.js
```

**为什么它最值得优先考虑**：
- 唯一**不逆源码、不注入进程、不破解签名**的路线
- 小程序页面自带 `data`，取数比解析 DOM 更准
- 官方 SDK 输出 ref-annotated 结构，**token 开销低**

**关于 SDK 本身的一个事实**：
`miniprogram-automator` 只能从 npm 装（最新 0.12.1，2023-11）。
它的 npm 元数据显示仓库地址是 `git@git.code.oa.com:devtools/automator.git`——
**腾讯内网 Git，不在 GitHub 上开源**。所以它没有公开源码可审计。
这一点与 W2/W3 用的开源工具不同，知情后自行权衡。

---

## 四、W2 · MITM 抓包（前提最少）

**原理**：小程序的 `wx.request` 本质还是 HTTPS，中间人代理就能看到真实 API。

**这是四条里前提条件最少的**——只要微信客户端 + 能配代理/证书。

```bash
python scripts/mp_capture.py init                  # 生成抓包工作区（addon + 目录）
pip install mitmproxy
python scripts/mp_capture.py run --dir <工作区>      # 启动 mitmdump
# ...在微信里操作目标小程序...
python scripts/mp_capture.py report --dir <工作区>  # 汇总 API 清单
```

### 识别小程序的可靠依据

按域名黑名单过滤是**不可靠的**——小程序可以请求任意自有域名。
可靠依据是 **Referer**：

```
Referer: https://servicewechat.com/<appid>/<version>/page-frame.html
```

`mp_capture` 生成的 addon 就用这个规则过滤（正则抓 appid + 版本号），
比猜域名准得多。

### 反代理怎么办

微信小程序**可能绕过系统代理**。这时用 **Proxifier**（商业软件）强制劫持
`Weixin.exe` / `WeChatAppEx.exe` 进程的流量到 mitmproxy。

> 注意：`Proxifier` 是**商业闭源软件**，不是开源项目。
> 该领域开源替代可看 `InterceptSuite/ProxyBridge`。

### 版本陷阱：mitmproxy 10+ 入口变了

`python -m mitmdump` 在新版 mitmproxy 上**会报 `No module named mitmdump`**。
mitmproxy 10 起只保留 console_scripts 入口：

```
mitmdump   → mitmproxy.tools.main:mitmdump
```

`mp_capture.py run` 已处理这点——它用 `shutil.which("mitmdump")` 定位可执行文件，
回退到当前解释器同目录的 `Scripts/mitmdump.exe`。

### 凭据脱敏（本 skill 内建）

抓包最大的风险是**把凭据留成明文文件**。`mp_capture` 的 addon 在落盘前会：

- 把 `Cookie / Authorization / token / sign / nonce / openid` 等的**值**
  替换为 `<redacted:长度>`，**保留字段名**
- 默认**不落请求体正文**（`MP_CAP_KEEP_BODY=1` 才落）

保留字段名是关键：**签名参数的结构才是你要分析的，凭据本身不需要**。

**实测（端到端，非构造样例）**——启动 mitmdump 12.2.3，打入两条请求：

| 输入 | 结果 |
|---|---|
| 带 `Referer: .../wx4c400f06c348405b/350/page-frame.html` | ✓ 记录，appid/版本解析正确 |
| 带普通 Referer（非小程序） | ✓ **被过滤**，未落盘 |
| 参数 `sign=a1b2c3d4` | → `<redacted:8>` |
| 参数 `ts=1760000000` | → `<redacted:10>` |
| 响应头 `Set-Cookie`（209 字节） | → `<redacted:209>` |
| `Referer` / `User-Agent` | 保留明文（分析需要，非凭据） |

---

## 五、W3 · 反编译（信息最全，但属逆向）

**本 skill 不自己实现解密**——那属逆向范畴。只做两件事：

```bash
python scripts/mp_unpack.py tools     # 检测本机解包/解密工具
python scripts/mp_unpack.py guide     # 完整指引 + 合规提示
python scripts/mp_unpack.py scan --dir <解包输出>   # 静态提取（无风险）
```

### 社区工具（均经 GitHub 核实）

| 工具 | ★ | 语言 | 状态 | 说明 |
|---|---|---|---|---|
| `Ackites/KillWxapkg` | **6011** | Go | 2024-09 | 自动解密+解包+还原目录，**推荐** |
| `wux1an/wxapkg` | **4245** | Python/Rust | 2026-04 | 跨平台 GUI，扫描+解密+解包 |
| `xdmjun/wxappUnpacker` | 2426 | JS | **2023-04** | ⚠ 停更，**不支持 V1MMWX** |
| `BlackTrace/pc_wxapkg_decrypt` | 752 | Go | **2020-08** | ⚠ 可能不支持 V1MMWX |
| `superdashu/pc_wxapkg_decrypt_python` | 208 | Python | **2020-08** | ⚠ 同上 |

> **星数≠可用**。`wxappUnpacker` 星数不低但 2023 年停更；
> 面对 4.x 的 V1MMWX 加密，**要用 2024 年后仍在维护的工具**。

### 静态提取做什么

`scan` 对**已解包**的源码做纯文本挖掘（不执行任何代码）：

| 提取项 | 用途 |
|---|---|
| 域名 / CDN 域名 | 找出真实 API 域 |
| `wx.request` / `uploadFile` / `connectSocket` 调用点 | 弄清有哪些接口 |
| baseURL 常量 | 拼出完整 URL |
| **加密函数调用**（`md5` / `hmac` / `CryptoJS.*`） | 定位签名算法 |
| **疑似密钥/盐常量** | 定位签名入参 |

### 与 W2 的分工

- **W3 告诉你「接口长什么样、签名怎么算」**
- **W2 告诉你「真实响应数据长什么样」**

两者互补：先用 W3 读懂签名参数，再用 W2 验证。

---

## 六、路线选择的判定流程

```
mp_discover.py  →  本机有包吗？
                    │
        ┌───────────┴───────────┐
       有                        无
        │                         │
   包加密吗？              只能走 W2（抓包）
        │
  ┌─────┴─────┐
 明文        加密
  │           │
W3 直接解包  有解包工具吗？
              │
      ┌───────┴────────┐
     有                无
      │                 │
   W3 解密+解包      走 W2，或先装 KillWxapkg
```

**另外**：如果目标就是你自己有源码的小程序，**直接走 W1**，别折腾逆向。

---

## 七、合规边界（必须读）

本 skill 的设计立场：**技术手段按"是否越过访问控制"分级**，而不是按"能不能做"。

| 区域 | 行为 | 立场 |
|---|---|---|
| 🟢 **安全区** | 抓自己账号的流量（W2）；分析自己有权访问的小程序（W1/W0）；对已解包源码做静态阅读 | 工具默认支持 |
| 🟡 **需自行判断** | 解密/反编译他人小程序包（W3）；用一个账号的数据复现接口 | 工具提供适配，**不代做**，风险自负 |
| 🔴 **不做** | 绕过付费墙/权限；批量注册账号；抓取他人隐私数据；高频重放压测 | **不提供能力** |

**共同的底线**：
- 只抓**你自己有权访问**的数据
- 控制频率（抓包不产生额外请求，但**复现接口时**要限速，见 `tiers.md` 同域限速）
- 不要用抓到的凭据去登录/冒用身份

> 小程序抓取的免责声明比网页更重：
> 反编译与流量篡改在中国法下可能触及《反不正当竞争法》《数据安全法》等。
> **个人研究用途**与**商业用途**的边界，需要你自己判断，本文档不构成法律意见。

---

## 八、命令速查

| 目标 | 命令 |
|---|---|
| 盘点本机小程序包 | `python scripts/mp_discover.py` |
| 体检（含 W 层） | `python scripts/doctor.py` |
| W1 环境检测 | `python scripts/mp_automator.py check` |
| W1 生成自动化脚本 | `python scripts/mp_automator.py init --project <目录>` |
| W1 操作指引 | `python scripts/mp_automator.py guide` |
| W2 生成抓包工作区 | `python scripts/mp_capture.py init` |
| W2 启动抓包 | `python scripts/mp_capture.py run --dir <工作区>` |
| W2 汇总 API | `python scripts/mp_capture.py report --dir <工作区>` |
| W3 检测解包工具 | `python scripts/mp_unpack.py tools` |
| W3 工具链指引 | `python scripts/mp_unpack.py guide` |
| W3 静态提取 | `python scripts/mp_unpack.py scan --dir <解包目录>` |

---

## 九、清单打假（本层工具的真伪核实结果）

社区流传的小程序抓取工具清单里，**混着若干不存在或张冠李戴的名字**。实测核实：

| 清单声称 | 核实结论 |
|---|---|
| `weapp-dev-mcp` | ✅ **真实** `yfmeii/weapp-dev-mcp` ★174 —— FastMCP 封装开发者工具自动化 |
| `agent-miniprogram` | ⚠️ **未找到该名**；相近的是 `Difficult-Burger/miniprogram-agent-bridge` ★19 |
| `weixin-miniprogram-cli` | ⚠️ 官方那个是 `wechat-miniprogram/miniprogram-cli` ★72，**且已归档**；另有同名 ★4 小项目 |
| `miniapp-cdp` | ❌ **未找到** —— 疑似臆造 |
| `wx-mp-mcp` | ❌ **未找到** —— 疑似臆造 |
| `Burpy` | ✅ 真实 `mr-m0nst3r/Burpy` ★435 MIT —— BurpSuite 插件，跑 Python 脚本；**但不是"小程序专用"** |
| `Universal Extractor` | ❌ **张冠李戴** —— 匹配到 `UniExtract2` ★4440，那是**解压缩软件**，与爬虫无关 |
| `miniprogram-automator` | ⚠️ 说是"官方 SDK"**没错**，但它**不在 GitHub**（腾讯内网仓库），只能 npm 安装 |
| `Airtest` | ✅ 真实 `AirtestProject/Airtest` ★9598 Apache-2.0 —— 但**与小程序抓取无关**，是通用 UI 自动化 |
| `mitmproxy` | ✅ 真实 `mitmproxy/mitmproxy` ★45426 MIT |
| `Proxifier` | ❌ 不是开源项目（商业闭源软件） |
| `影刀 RPA` | ❌ 主产品商业闭源（搜到的是第三方 MCP 封装） |

### 清单遗漏、但确实存在的重量级工具

| 工具 | ★ | 说明 |
|---|---|---|
| `Ackites/KillWxapkg` | **6011** | 自动解密+解包+还原目录，支持 Hook |
| `wux1an/wxapkg` | **4245** | 跨平台 GUI，扫描+解密+解包 |
| `frida/frida` | 22195 | 动态插桩（可用于绕过证书固定） |
| `BlackTrace/pc_wxapkg_decrypt` | 752 | PC 端 wxapkg 解密 |

**结论**：清单里 12 项中 **2 项完全不存在**、**2 项张冠李戴/用途错位**、
**1 项被误认为开源**（官方 SDK 不在 GitHub）。
真正值得用的反编译工具，清单**一个都没收录**。
