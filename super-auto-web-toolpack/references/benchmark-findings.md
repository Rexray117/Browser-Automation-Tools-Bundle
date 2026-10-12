# 真实站点实测结论（选型前必读）

> 来源：GSMArena + DXOMARK 三后端横向实测（2026-10-12）
> 完整报告见项目工作区 `GSMArena_DXOMARK_分层抓取实测报告_v1.0.md`

---

## 一、一句话结论

**T1 `patchright` 在真实站点上全面优于 T2。T2 是"T1 失败时的备选"，不是"更强的 T1"。**

---

## 二、实测数据

目标：GSMArena（Cloudflare Turnstile 保护）+ DXOMARK（无防护）

| 目标 | patchright T1 | cloakbrowser T2 | camoufox T2 |
|---|---|---|---|
| GSMArena 手机页 | ✅ **7915** / 22.5s | ✅ 7813 / 41.6s | ❌ 0 字符 |
| GSMArena 首页 | ✅ **6226** / 21.3s | ✅ 6194 / 24.2s | ✅ 6205 / 30.7s |
| DXOMARK 手机列表 | ✅ 4851 / **10.7s** | ✅ 4851 / 25.4s | ✅ 4851 / 35.9s |
| DXOMARK 首页 | ✅ **4062** / 21.8s | ✅ 3680 / 39.1s | ✅ 4062 / 24.8s |

- T1 平均 ~19s；cloakbrowser ~33s（**慢 1.7 倍**）；camoufox ~31s（**慢 1.6 倍**）
- T1 成功率并列最高、内容量并列最高、耗时最低

---

## 三、五条经验

### 1. Cloudflare Turnstile 约 9 秒后自动放行

```
0.0s  title='GSMArena Turnstile check'
9.0s  title='Apple iPhone 15 Pro - Full phone specifications'
```

**不要等 2–3 秒就判"被拦"**。误报比漏报更贵——会让工具白白升级到慢 2–4 倍的层级。
`--challenge-wait` 默认 15s。

### 2. 通用指纹测试页不能预测真实站点表现

| 后端 | bot.sannysoft.com | GSMArena 真实站 |
|---|---|---|
| patchright | 57/57 | ✅ 全部通过 |
| cloakbrowser | 57/57 | ✅ 3/4 |
| camoufox | 56/57 | ❌ 内容页挑战循环 |

**sannysoft 分数好看 ≠ 真实站点能过。选型必须跑目标站实测**——这正是 `benchmark.py` 存在的理由。

### 3. camoufox 在严格 Cloudflare 站点会陷入挑战循环

给足 45 秒仍停在挑战页（`challenge_wait=45514ms`，48 字符）。观察到的过程：挑战页 → 导航中 → 又被重新挑战 → 循环。

推测机制：camoufox 走 Firefox 路线且**每次启动随机化身份**，而 patchright 用**真实系统 Chrome + 真实硬件**。Cloudflare 对"高伪装但内部信号不一致"的环境更敏感。

> 遇到这种情况换 `--tier2 cloakbrowser`（Chromium 系，与 patchright 同源）。

### 4. 结果是概率性的

同一后端同一 URL，短时间内两次运行结果可能不同（cloakbrowser 在 GSMArena 手机页：一次成功、一次被拦）。

**单次结果不能作为选型结论。** 工具靠**站点画像**记住"哪个层级曾走通"，失败时按序重试。

### 5. 出口 IP 不是决定因素

| 测试 | 直连 | 经代理 |
|---|---|---|
| patchright | ✅ 6221 | ✅ 6221 |
| cloakbrowser | 🚫 被拦 | 🚫 被拦 |

换 IP 不改变结论——**决定因素是浏览器环境，不是 IP 信誉**。

---

## 四、对各站点的建议

| 站点 | 建议 |
|---|---|
| **DXOMARK** | **无反爬**，三后端内容完全一致（4851 字符）。用 T0/T1 即可，不必上 T2 |
| **GSMArena** | 有 Turnstile，但 **T1 足够**（首页 6226 / 详情页 7915）。T2 慢 2–4 倍且无内容增益 |

---

## 五、怎么复现

```bash
python benchmark.py --urls-file sites.txt \
  --backends patchright,cloakbrowser,camoufox \
  --challenge-wait 25000 --md report.md
```

**建议流程**：接入一个新站点前，先跑一次三后端对比，用真实数据决定该站的最低可用层级，再交给 `fetch.py` 日常抓取（画像会记住结论）。
