# 分层策略与反爬信号词表

## 一、各层能力边界

| 层级 | 后端 | 能过 | 过不了 | 单次成本 |
|---|---|---|---|---|
| T0 | `curl_cffi` | 无防护站、UA 黑名单、JA3/JA4 TLS 指纹校验、HTTP/2 指纹 | 任何 JS 挑战、验证码、动态渲染 | 0.2–1s |
| T1 | `patchright` | Cloudflare JS 挑战、WebDriver 检测、CDP 泄漏检测、SPA 渲染 | 行为生物识别验证码、强指纹（DataDome 极端配置） | 5–8s |
| T2 | `cloakbrowser` | DataDome / PerimeterX / Turnstile（部分）、行为检测 | 硬验证码（复选框式生物识别） | 5–20s |
| T2 | `camoufox` | 强指纹站点（Firefox 路线，C++ 层改指纹） | 依赖 Chrome 特征的站点 | 5–20s |
| T3 | 人工 | 验证码、登录态、代理 | — | — |

**选择 T2 后端的一条经验**：目标站按 Chrome 特征做风控 → 用 `cloakbrowser`；目标站风控对 Firefox 更宽松 → 用 `camoufox`。走不动就换另一个。

---

## 二、T0 为什么有效

`curl_cffi` 不伪装 UA，而是伪装 **TLS 握手指纹（JA3/JA4）与 HTTP/2 帧指纹**。

为什么这能过一部分站点？因为大量风控只做"这是不是 Python `requests` 发出的请求"这一层判断——`requests` 的 TLS 握手特征（加密套件顺序、扩展列表）与真实浏览器差异极大。`curl_cffi` 直接复刻 Chrome 的握手。

**判定建议**：站点若无 JS 渲染需求，先跑 `--only-tier 0`。过了就省 5–8 秒；不过再分层。

---

## 三、反爬厂商信号词表（`detect.py` 内置）

| 厂商 | 关键特征 |
|---|---|
| **Cloudflare** | `Just a moment...`、`Checking your browser before accessing`、`__cf_chl_`、`cdn-cgi/challenge-platform`、`cf-browser-verification` |
| **Cloudflare Turnstile** | `cf-turnstile`、`challenges.cloudflare.com/turnstile`、`Verify you are human` |
| **DataDome** | `datadome`、`dd_cookie_test`、`blocked by datadome` |
| **PerimeterX / HUMAN** | `perimeterx`、`_pxhd`、`px-captcha`、`Access to this page has been denied` |
| **Akamai** | `Reference #18.xxxx`、`akamai bot manager`、`ak_bmsc` |
| **Imperva / Incapsula** | `incapsula`、`_Incapsula_Resource`、`visid_incap` |
| **Kasada** | `kasada`、`kp_uidz`、`x-kpsdk` |
| **AWS WAF** | `aws-waf-token`、`awswaf` |
| **极验** | `geetest`、`gt_c_s` |
| **中文站点 WAF** | `五秒盾`、`请稍候…验证`、`安全检查中`、`正在验证您的浏览器`、`网站防火墙`、`人机验证`、`滑动验证`、`完成验证后即可继续访问` |

**通用验证码**：`recaptcha` / `hcaptcha` / `captcha` / `验证码` / `图形验证`
**登录墙**：`请先登录` / `登录后查看` / `sign in to continue` / `需要登录`

---

## 四、状态码语义

| 状态码 | 归类 | 说明 |
|---|---|---|
| 401 / 402 / 403 / 406 / 407 / 409 / 418 / 444 / 451 / 511 | 硬拦截 | 直接拒绝访问 |
| 429 / 503 | 限流 | `rate_limited=True`，升层级 + 降频 |
| 502 / 520–527 / 530 | 网关类 | 常见于 Cloudflare 回源失败或被盾 |

---

## 五、升级判定规则（`detect.should_escalate`）

| 检测结果 | 是否升级 | 理由 |
|---|---|---|
| 未拦截 | 否 | 已成功 |
| `captcha` / `cloudflare-turnstile` / `geetest` | **是**，但可能仍需人工 | 升到强指纹层也许能过 |
| `login-wall` | **否** | 换指纹解决不了登录问题，需提供登录态 |
| `rate_limited` | 是 | 升级 + 降频；必要时换代理 |
| 内容为空 / SPA 空壳 | 是 | 需要浏览器执行 JS |
| 其他厂商拦截 | 是 | 尝试更强指纹 |
| 已达 `--max-tier` | 否 | 报告给用户 |

---

## 五之二、软限制：比硬拦截更危险的一类

**硬拦截会报错，软限制会静默产出脏数据。** 站点返回 200、页面看着正常，但内容被降级、截断或只给了一部分——这类情况如果当成成功，下游拿到的就是错误结论。

因此本工具把软限制**与硬拦截同等对待**：`blocked=True` + `soft_limited=True`，同样触发逐级升级。

### 触发升级的软限制信号

| 类型 | 特征（`detect.py` 内置） | 强度 | 升级？ |
|---|---|---|---|
| `soft-paywall` | `订阅后可继续阅读`、`会员专享`、`subscribe to continue`、`premium content` | strong | ❌ **不升级**（访问控制） |
| `soft-login` | `登录后查看`、`注册后可见`、`log in to see more` | strong | ❌ **不升级**（访问控制） |
| `js-required` | `请开启 JavaScript`、`enable javascript to` | strong | ✅ 升级 |
| `degraded` | `仅显示部分结果`、`内容受限`、`partial content`、`limited view` | strong | ✅ 升级 |
| `short-content` | 有效文本 < `--min-text` | — | ✅ 升级 |
| `slow-response` | 耗时 > `--slow-ms`（**疑似被限速**） | — | ✅ 升级 |
| `truncated` | `展开全文`、`查看更多`、`read more` | **weak** | ❌ **只记录**——正常页面太常见，单独命中就升级会大量误报 |

### 为什么 `truncated` 不触发升级

"查看更多"/"read more" 出现在几乎每个带分页的正常页面上。**把它当软限制会导致大量误报升级**，既慢又没必要。

所以它被降级为 **weak**：只写进 `signals` 供你判断，不参与升级决策。如果某个站点的"查看更多"确实代表内容被截断，用 `--require` 精确断言，或调高 `--min-text`。

### 两套阈值必须分开（否则误报）

| 阈值 | 参数 | 语义 |
|---|---|---|
| `empty_floor`（硬） | 固定 200 | 低于此值 = **根本没取到内容**（空壳页） |
| `min_text_len`（软） | `--min-text`，默认 200 | 低于此值 = **取到了，但不够多** |

早期实现把两者混用，导致 `--min-text 99999` 时报出"疑似未取到内容"——可实际取到了 1796 字符。
现在硬检测用固定低阈值，软检测用用户阈值，标注各自准确。

### 速度也是软限制信号：相对基线

**问题**：怎么判断"这次变慢了"？

- 纯 GET 正常 <2s，固定 10s 阈值够用 —— 但**浏览器层本身就要 5–8s**，任何固定阈值都会全员误报
- 若因此对浏览器层关掉速度检测，又恰好给"**只针对浏览器层限速**"留了空子

**解法：拿站点自己的历史中位数当对照。**
> "这次比它平时慢了 3 倍以上" 才是可靠信号，且天然适配每个站点的常态。

#### 工作机制

1. 每次**成功且未被拦截**的抓取，把耗时记进 `~/.super-auto-web-toolpack/latency_baseline.json`（按 `域名|层级` 分组，只留最近 N 条）
2. 下次抓取前，取该组合的**中位数 × `--slow-factor`**（默认 3.0）作为判慢阈值
3. 样本不足（默认 <3 条）时**回退到固定阈值**，避免冷启动误判

#### 阈值优先级

```
命令行 --slow-ms  >  相对基线（中位数×倍数）  >  固定默认  >  不启用
```

#### 实测示例

```
T1 · 相对基线：quotes.toscrape.com T1 中位数 6592ms ×3 = 19774ms （样本 6）
T0 · 固定默认（冷启动，基线样本不足）
```

#### 为什么用中位数而不是平均值

平均值会被单次异常值（一次超时、一次 GC 停顿）拉偏。中位数对离群值不敏感，更能代表"常态"。

#### 相关命令

```bash
python fetch.py --baselines              # 查看所有基线
python fetch.py "<URL>" --slow-factor 5  # 放宽到 5 倍才判慢
python fetch.py "<URL>" --baseline-n 20  # 保留 20 个样本
python fetch.py "<URL>" --no-baseline    # 关闭相对基线
python fetch.py "<URL>" --slow-ms 8000   # 固定阈值，覆盖基线
```

#### 反直觉的一点

**被限速时升级层级可能反而更快**——限速常针对"HTTP 客户端指纹"这一层做，换成浏览器指纹后反而通畅。所以判慢后的动作是**升级**，不是降速等待。

### 结果里的标记

```json
{
  "blocked": true,
  "block_vendor": "soft:degraded",
  "soft_limited": true,
  "soft_reasons": ["degraded", "short-content"],
  "text": "...(拿到的不完整内容仍在)..."
}
```

**注意**：`ok=false` 但 `text` 仍有内容。这是故意的——不完整数据保留下来供你人眼核对，但**状态绝不标成成功**。

### 用 `--min-text` 按站点设定内容下限

```bash
# 列表页正常应有 2000+ 字符，低于此说明只渲染了首屏
python fetch.py "https://example.com/list" --min-text 2000

# 配合 --require 做精确断言（更推荐）
python fetch.py "https://example.com/list" --require "共\s*\d+\s*条"
```

---

## 六、内容断言（`--require`）

**长度检测有一个固有盲区**：页面可能"取到了内容，但取不全"（例如列表页只渲染了首屏、评论区懒加载未触发）。

此时 `blocked=False`、文本长度也够，工具不会升级 —— 这是**静默的数据质量事故**。

`--require "<正则>"` 用来兜住这个盲区：断言正文中必须出现某个字符串，否则视为未取到并**触发升级**。

```bash
# 断言必须出现"评论"区块的某个特征串
python fetch.py "https://example.com/product/1" --require "用户评价"
```

**建议**：对结构已知的关键页面，始终加 `--require`。

---

## 七、站点画像机制

`~/.super-auto-web-toolpack/site_profiles.json` 记录每个域名：

```json
{
  "example.com": {
    "domain": "example.com",
    "preferred_tier": 1,
    "preferred_backend": "patchright[chrome]",
    "success": 12,
    "fail": 0,
    "vendors_seen": ["cloudflare"],
    "blocks": 3,
    "updated_at": "2026-10-12T09:56:50+08:00"
  }
}
```

**行为**：
- 抓取前查画像 → 已知该站需要 T1，就**直接从 T1 起步**，不再白白试一次 T0
- 成功后把起跳层抬到本次成功的层级
- 失败累计写入 `blocks` / `fail`

**何时重置**：某站突然开始拦截 → 删掉该域名条目，让它回到从 T0 起跳重新探测。

---

## 八、限速与退避

- **同域最小间隔**：默认 1.0s（`--min-interval` 可调）
- **抖动**：实际等待 ×0.8–1.2 随机 —— **防"规律性请求"被识别**
- **指数退避**：连续失败时按 `base × 2^n` 放大，上限 30s

调参建议：
- 正常站点：1s
- 被限流过：3–5s
- 站点公开声明抓取频率：按声明来
