# Browser Automation Tools Bundle

[![Skill](https://img.shields.io/badge/skill-super--auto--web--toolpack%20v2.0.0-4a7dff)](./super-auto-web-toolpack/SKILL.md)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](./super-auto-web-toolpack/requirements.txt)
[![license](https://img.shields.io/badge/license-MIT-green)](./LICENSE)

> 基于开源浏览器自动化工具整合的个人使用版 SKILL。当前收录 **`super-auto-web-toolpack` v2.0.0**：
> 分层网页抓取 + 微信小程序单列工具链，按成本递增自动升级反爬对抗等级。

## 分层策略

**网页层**（越高越重，命中拦截才升级）

| 层 | 后端 | 定位 |
| --- | --- | --- |
| T0 | `curl_cffi` | TLS/JA3 指纹直连，最省资源 |
| T1 | `patchright` | 补丁版 Playwright，默认起步层 |
| T2 | `cloakbrowser` / `camoufox` | 强指纹浏览器，硬拦截时的选项 |

触发升级的信号：Cloudflare / DataDome / Akamai / 中文五秒盾等硬拦截，以及内容降级、需 JS 渲染、响应过慢等软限制。

**小程序层（W 系列）**

| 层 | 方式 |
| --- | --- |
| W0 | 包盘点（只读） |
| W1 | 官方 SDK `miniprogram-automator` |
| W2 | MITM 抓包（mitmproxy，含凭据脱敏） |
| W3 | 反编译适配与静态接口提取 |

内置反爬信号检测、站点画像记忆、同域限速、robots.txt 预检与耗时相对基线。

## 目录结构

```
super-auto-web-toolpack/
  SKILL.md  requirements.txt
  scripts/     # detect / fetch / tiers / benchmark / stealth_check / selftest / doctor / mp_*
  references/  # tiers · adapters · miniprogram · compliance · benchmark-findings
  assets/      # site_profile 示例
```

## 安装

```bash
python -m venv .venv && .venv/Scripts/activate       # Windows
pip install -r super-auto-web-toolpack/requirements.txt

# T2 浏览器二进制（体积大，按需）
python -c "import cloakbrowser; cloakbrowser.ensure_binary()"   # ~536 MB
python -m camoufox fetch                                        # ~2.5 GB

# 安装为 agent 技能
git clone https://github.com/Rexray117/Browser-Automation-Tools-Bundle.git /tmp/batb
cp -r /tmp/batb/super-auto-web-toolpack ~/.dsh/skills/super-auto-web-toolpack
```

> 版本张力说明：`camoufox` 会把 `playwright` 钉在 1.62.0，`cloakbrowser` 默认 1.63.0。
> 同环境共存时按 `requirements.txt` 的顺序安装（先 curl_cffi/patchright → cloakbrowser → camoufox）。

## 常用命令

```bash
python super-auto-web-toolpack/scripts/fetch.py https://example.com
python super-auto-web-toolpack/scripts/fetch.py --urls-file urls.txt --max-tier 2 --proxy http://127.0.0.1:7890
python super-auto-web-toolpack/scripts/detect.py           # 反爬信号判定
python super-auto-web-toolpack/scripts/selftest.py         # 拦截页回归自测（不漏报、不误报）
python super-auto-web-toolpack/scripts/doctor.py           # 环境自检
```

## 合规提示

仅用于学习研究与合规范围内的数据采集：遵守目标站点服务条款与 robots.txt，控制频率，不绕过登录与付费墙，不采集个人隐私数据。

## 版本

`super-auto-web-toolpack` **v2.0.0**（本次同步：`SKILL.md`、`references/miniprogram.md`、`scripts/mp_automator.py` 等，并清理了误提交的 `__pycache__`）

## License

MIT © 2026 Rexray117（见 `LICENSE`）
