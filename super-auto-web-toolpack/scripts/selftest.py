#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归自测：用已知的拦截页样本验证 detect.py 不漏报、不误报。

    python selftest.py

样本覆盖：Cloudflare 挑战 / Turnstile / DataDome / Akamai / 中文五秒盾 /
429 限流 / 403 / 登录墙 / SPA 空壳 / 正常页面（必须不误报）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import detect as D  # noqa: E402

NORMAL = """<!doctype html><html><head><title>Quotes to Scrape</title></head><body>
<h1>Quotes to Scrape</h1><div class="quote"><span class="text">"The world as we have
created it is a process of our thinking."</span><span>by Albert Einstein</span></div>
<div class="quote"><span class="text">"It is our choices that show what we truly are."</span>
<span>by J.K. Rowling</span></div><p>%s</p></body></html>""" % ("lorem ipsum dolor sit amet " * 30)

CF_CHALLENGE = """<!doctype html><html><head><title>Just a moment...</title></head>
<body><div id="cf-challenge-running"><h1>Checking your browser before accessing example.com</h1>
<p>This process is automatic. Your browser will redirect shortly.</p>
<script src="/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1"></script>
<form id="challenge-form" action="/cdn-cgi/challenge-platform/x"></form></div></body></html>"""

TURNSTILE = """<html><head><title>Verify you are human</title></head><body>
<div class="cf-turnstile" data-sitekey="0x4AAA"></div>
<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>
<p>Verify you are human by completing the action below.</p></body></html>"""

DATADOME = """<html><head><title>Access Denied</title></head><body>
<p>Your request has been blocked by datadome.</p>
<script>window.dd_cookie_test = true;</script></body></html>"""

AKAMAI = """<html><head><title>Access Denied</title></head><body>
<h1>Access Denied</h1><p>You don't have permission to access this resource.</p>
<p>Reference #18.abcd1234.1699999999.1a2b3c4d</p></body></html>"""

CN_WAF = """<html><head><title>安全检查</title></head><body>
<div>正在验证您的浏览器，请稍候…五秒盾防护中</div>
<p>网站防火墙已拦截可疑请求，完成验证后即可继续访问。</p></body></html>"""

LOGIN_WALL = """<html><head><title>请先登录</title></head><body>
<div class="login-box"><h2>请先登录</h2><p>登录后查看完整内容</p></div></body></html>"""

SPA_SHELL = """<!doctype html><html><head><title>App</title></head><body>
<div id="root"></div><script src="/assets/index-abc123.js"></script>
<script src="/assets/vendor-def456.js"></script></body></html>"""

CASES = [
    ("正常页面（不得误报）", NORMAL, 200, False, None),
    ("Cloudflare 挑战", CF_CHALLENGE, 403, True, "cloudflare"),
    ("Cloudflare Turnstile", TURNSTILE, 200, True, "cloudflare-turnstile"),
    ("DataDome", DATADOME, 403, True, "datadome"),
    ("Akamai", AKAMAI, 403, True, "akamai"),
    ("中文五秒盾", CN_WAF, 200, True, "cn-waf"),
    ("登录墙", LOGIN_WALL, 200, True, "login-wall"),
    ("SPA 空壳", SPA_SHELL, 200, True, None),
]

STATUS_CASES = [
    ("429 限流", 429, True, True),
    ("503", 503, True, True),
    ("403", 403, True, False),
    ("200", 200, False, False),
]

# 软限制样本：(名称, html/text 片段, 期望 soft, 期望 vendor, 期望升级)
SOFT_CASES = [
    ("软付费墙",
     """<html><title>文章</title><body><p>%s</p>
      <div class="gate">订阅后可继续阅读全文</div></body></html>""" % ("正文内容 " * 60),
     True, "soft:soft-paywall", False),
    ("软登录墙",
     """<html><title>帖子</title><body><p>%s</p>
      <p>登录后查看完整内容</p></body></html>""" % ("正文内容 " * 60),
     True, "soft:soft-login", False),
    ("需启用 JS",
     """<html><title>App</title><body><p>%s</p>
      <noscript>请开启 JavaScript 后继续</noscript></body></html>""" % ("正文内容 " * 60),
     True, "soft:js-required", True),
    ("内容降级",
     """<html><title>列表</title><body><p>%s</p>
      <div>仅显示部分结果，其余内容未下发</div></body></html>""" % ("正文内容 " * 60),
     True, "soft:degraded", True),
    ("内容偏少",
     """<html><title>页</title><body><p>很短</p></body></html>""",
     True, "soft:short-content", True),
    ("正常页面（不得误报）",
     """<html><title>文章</title><body><p>%s</p>
      <a href="/p2">查看更多</a></body></html>""" % ("正文内容 " * 60),
     False, None, False),
]


def main() -> int:
    fails = 0
    print("=" * 70)
    print("detect.py 回归自测")
    print("=" * 70)

    for name, html, status, want_blocked, want_vendor in CASES:
        from _common import html_to_text
        d = D.detect_block(status=status, html=html, text=html_to_text(html),
                           title=None, min_text_len=200)
        ok = (d.blocked == want_blocked)
        vendor_ok = (want_vendor is None) or (d.vendor == want_vendor)
        flag = "✓" if (ok and vendor_ok) else "✗"
        if not (ok and vendor_ok):
            fails += 1
        print(f"  [{flag}] {name:<24} blocked={d.blocked!s:<5} "
              f"vendor={str(d.vendor):<22} conf={d.confidence}")
        if not vendor_ok:
            print(f"        ↳ 期望 vendor={want_vendor}，实际={d.vendor}")

    print("-" * 70)
    for name, status, want_blocked, want_rate in STATUS_CASES:
        d = D.detect_block(status=status, html=NORMAL, text="x" * 400)
        ok = (d.blocked == want_blocked) and (d.rate_limited == want_rate)
        if not ok:
            fails += 1
        print(f"  [{'✓' if ok else '✗'}] {name:<24} blocked={d.blocked!s:<5} "
              f"rate_limited={d.rate_limited}")

    print("-" * 70)
    print("软限制检测（不拒绝你，但'少给'）")
    from _common import html_to_text as _h2t
    for name, html, want_soft, want_vendor, want_esc in SOFT_CASES:
        txt = _h2t(html)
        d = D.detect_soft_limit(status=200, html=html, text=txt,
                                title=None, elapsed_ms=500,
                                min_text_len=200, slow_ms=10_000)
        esc, _why = D.should_escalate(d, current_tier=0, max_tier=2)
        ok = (d.soft == want_soft)
        vok = (want_vendor is None) or (d.vendor == want_vendor)
        eok = (esc == want_esc)
        if not (ok and vok and eok):
            fails += 1
        print(f"  [{'✓' if ok and vok and eok else '✗'}] {name:<22} "
              f"soft={d.soft!s:<5} vendor={str(d.vendor):<20} 升级={esc}")
        if not eok:
            print(f"        ↳ 期望升级={want_esc}，实际={esc}")

    print("-" * 70)
    print("限速判定")
    d = D.detect_soft_limit(status=200, html=NORMAL, text=_h2t(NORMAL),
                            elapsed_ms=25_000, min_text_len=200, slow_ms=10_000)
    ok = d.soft and "slow-response" in d.soft_reasons
    if not ok:
        fails += 1
    print(f"  [{'✓' if ok else '✗'}] {'T0 响应 25s → 判定限速':<24} "
          f"soft={d.soft} vendor={d.vendor}")
    d = D.detect_soft_limit(status=200, html=NORMAL, text=_h2t(NORMAL),
                            elapsed_ms=3000, min_text_len=200, slow_ms=10_000)
    ok = not d.soft
    if not ok:
        fails += 1
    print(f"  [{'✓' if ok else '✗'}] {'T0 响应 3s → 不误报':<24} soft={d.soft}")

    print("-" * 70)
    esc_cases = [
        ("验证码类 → 提示需人工", D.DetectResult(blocked=True, vendor="captcha"), 1, 2, True),
        ("登录墙 → 不升级", D.DetectResult(blocked=True, vendor="login-wall"), 1, 2, False),
        ("已达上限 → 不升级", D.DetectResult(blocked=True, vendor="cloudflare"), 2, 2, False),
        ("未拦截 → 不升级", D.DetectResult(blocked=False), 0, 2, False),
        ("空内容 → 升级", D.DetectResult(blocked=True, empty_content=True), 0, 2, True),
        ("软付费墙 → 不升级（合规红线）",
         D.DetectResult(blocked=True, soft=True, vendor="soft:soft-paywall",
                        soft_reasons=["soft-paywall"]), 0, 2, False),
        ("软登录墙 → 不升级",
         D.DetectResult(blocked=True, soft=True, vendor="soft:soft-login",
                        soft_reasons=["soft-login"]), 0, 2, False),
        ("软 JS 必需 → 升级",
         D.DetectResult(blocked=True, soft=True, vendor="soft:js-required",
                        soft_reasons=["js-required"]), 0, 2, True),
        ("软限速 → 升级",
         D.DetectResult(blocked=True, soft=True, vendor="soft:slow-response",
                        soft_reasons=["slow-response"]), 0, 2, True),
    ]
    for name, dr, cur, mx, want in esc_cases:
        got, why = D.should_escalate(dr, current_tier=cur, max_tier=mx)
        ok = (got == want)
        if not ok:
            fails += 1
        print(f"  [{'✓' if ok else '✗'}] {name:<24} escalate={got!s:<5} ({why[:40]})")

    print("=" * 70)
    print("全部通过 ✓" if fails == 0 else f"{fails} 项未通过 ✗")
    print("=" * 70)
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
