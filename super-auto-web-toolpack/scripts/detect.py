# -*- coding: utf-8 -*-
"""反爬 / 拦截信号检测。

判定维度（任一命中即视为被拦，但给出置信度）：
  1. HTTP 状态码（403 / 429 / 451 / 5xx 网关类）
  2. 已知反爬厂商的页面指纹（Cloudflare / DataDome / Akamai / PerimeterX / 五秒盾 …）
  3. 挑战页 / 验证码特征（含中文站点用词）
  4. 内容为空（浏览器壳、SPA 空挂载点）
  5. 跳转到登录页

设计取舍：**宁可多报"疑似"，也不放过**——因为漏报会让上层把挑战页当数据落盘，
这正是既有技能里反复强调的坑。疑似命中时给出 vendor 与 reason，交由上层决定是否升级。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# 状态码语义
HARD_BLOCK_STATUS = {401, 402, 403, 406, 407, 409, 418, 444, 451, 511}
RATE_LIMIT_STATUS = {429, 503}
GATEWAY_STATUS = {502, 520, 521, 522, 523, 524, 525, 526, 527, 530}

# 厂商指纹表：(厂商, 正则列表, 说明)
VENDOR_SIGNATURES: list[tuple[str, list[str], str]] = [
    ("cloudflare", [
        r"just a moment\.\.\.",
        r"attention required!\s*\|\s*cloudflare",
        r"__cf_chl_",
        r"cf-browser-verification",
        r"cf_chl_opt",
        r"checking your browser before accessing",
        r"cdn-cgi/challenge-platform",
        r"enable javascript and cookies to continue",
    ], "Cloudflare 浏览器挑战"),
    ("cloudflare-turnstile", [
        r"cf-turnstile",
        r"challenges\.cloudflare\.com/turnstile",
        r"verify you are human",
    ], "Cloudflare Turnstile 人机验证"),
    ("datadome", [
        r"datadome",
        r"dd_cookie_test",
        r"blocked by datadome",
    ], "DataDome"),
    ("perimeterx", [
        r"perimeterx",
        r"_pxhd",
        r"px-captcha",
        r"access to this page has been denied",
    ], "PerimeterX / HUMAN"),
    ("akamai", [
        r"reference\s*#\s*\d+\.?[0-9a-f]*",
        r"akamai bot manager",
        r"ak_bmsc",
    ], "Akamai Bot Manager"),
    ("imperva", [
        r"incapsula",
        r"_incapsula_resource",
        r"visid_incap",
    ], "Imperva / Incapsula"),
    ("kasada", [
        r"kasada",
        r"kp_uidz",
        r"x-kpsdk",
    ], "Kasada"),
    ("aws-waf", [
        r"aws-waf-token",
        r"awswaf",
    ], "AWS WAF"),
    ("geetest", [
        r"geetest",
        r"gt_c_s",
        r"极验",
    ], "极验验证"),
    # 中文站点常见挑战
    ("cn-waf", [
        r"五秒盾",
        r"请稍候[，,].{0,10}(验证|检查)",
        r"安全检查中",
        r"正在验证您的浏览器",
        r"网站防火墙",
        r"访问验证",
        r"人机验证",
        r"滑动验证",
        r"完成验证后即可继续访问",
    ], "中文站点 WAF / 人机验证"),
]

# 通用验证码特征（无厂商归属）
GENERIC_CAPTCHA = [
    r"\brecaptcha\b", r"g-recaptcha", r"\bhcaptcha\b", r"h-captcha",
    r"\bcaptcha\b", r"验证码", r"图形验证",
]

# 登录墙特征
LOGIN_WALL = [
    r"请先登录", r"登录后查看", r"sign in to continue",
    r"log in to continue", r"please log in", r"需要登录",
]

# SPA 空壳挂载点（内容为空时的辅助证据，本身不算被拦）
SPA_SHELL = [
    r'<div\s+id="root"\s*>\s*</div>',
    r'<div\s+id="app"\s*>\s*</div>',
    r'<div\s+id="__next"\s*>\s*</div>',
    r'<div\s+id="__nuxt"\s*>\s*</div>',
]

# --------------------------------------------------------------------------- #
# 软限制特征：站点不直接拒绝，但"少给"——内容降级、部分隐藏、或明确提示需登录/订阅。
# 这类情况比硬拦截更危险：它返回 200、看着成功，却静默产出不完整的数据。
#
# strength="strong"：单独命中即可判定软限制并触发升级
# strength="weak"  ：**只记录不判定**——"查看更多"/"read more" 在正常页面里太常见，
#                    单独命中就升级会造成大量误报
# --------------------------------------------------------------------------- #
SOFT_SIGNATURES: list[tuple[str, str, list[str], str]] = [
    ("soft-paywall", "strong", [
        r"订阅后(可)?(继续|查看|阅读)",
        r"会员(专享|可见|可读)",
        r"付费后(查看|阅读)",
        r"开通(会员|订阅)后",
        r"订阅以(继续|查看)",
        r"subscribe to (continue|read|view)",
        r"this (article|content|story) is for (subscribers|members)",
        r"premium (content|article)",
        r"upgrade to (read|view) more",
    ], "软付费墙：内容部分隐藏，需订阅"),
    ("soft-login", "strong", [
        r"登录后(可)?(查看|可见|继续|阅读)",
        r"注册后(可见|查看)",
        r"请登录以(查看|继续)",
        r"log ?in to (see|view|continue|read) more",
        r"sign ?in to (see|view|continue|read) more",
    ], "软登录墙：部分内容需登录"),
    ("js-required", "strong", [
        r"请(开启|启用|打开)\s*javascript",
        r"enable javascript (to|and|in)",
        r"需要(开启|启用)\s*js",
        r"javascript is (required|disabled)",
        r"您的浏览器(未|不)支持\s*javascript",
    ], "页面要求启用 JavaScript"),
    ("degraded", "strong", [
        r"仅(显示|展示|提供)部分(内容|结果|数据)",
        r"内容(受限|已截断|不完整)",
        r"partial (content|results|data)",
        r"limited (view|content|results)",
        r"您(所在|当前)(地区|区域)无法查看完整",
    ], "站点明确提示内容被降级 / 仅部分下发"),
    ("truncated", "weak", [
        r"展开(阅读)?全文",
        r"阅读全文",
        r"继续阅读",
        r"加载更多",
        r"查看更多",
        r"read (more|full)",
        r"show more",
        r"load more",
    ], "存在展开/更多入口（内容可能未全部下发）"),
]


def detect_soft_limit(*, status: int | None, html: str | None, text: str | None,
                      title: str | None = None, elapsed_ms: int | None = None,
                      min_text_len: int = 80, slow_ms: int = 0) -> DetectResult:
    """软限制判定：站点不拒绝你，但"少给"。

    触发升级的三种情形：
      1. 命中 **strong** 级软限制特征（软付费墙 / 软登录墙 / 需 JS / 内容降级）
      2. 有效文本长度低于 ``min_text_len``（内容不足）
      3. 耗时超过 ``slow_ms``（响应过慢，疑似被限速）

    weak 级特征（"查看更多"等）只记录进 ``signals``，不参与判定。
    """
    res = DetectResult()
    probe = f"{title or ''}\n{(html or '')[:20000]}\n{(text or '')[:6000]}"

    strong_hits: list[tuple[str, str, str]] = []
    for kind, strength, pats, desc in SOFT_SIGNATURES:
        hit = _scan(pats, probe)
        if not hit:
            continue
        res.signals.append(f"soft:{kind}")
        if strength == "strong":
            strong_hits.append((kind, desc, hit))

    text_len = len((text or "").strip())

    if strong_hits:
        kind, desc, hit = strong_hits[0]
        res.soft = True
        res.blocked = True
        res.vendor = f"soft:{kind}"
        res.confidence = 0.80
        res.reason = f"{desc}（命中 {hit}）"
        res.soft_reasons.append(kind)

    if text_len < min_text_len:
        res.soft = True
        res.blocked = True
        if text_len == 0:
            res.empty_content = True
        res.vendor = res.vendor or "soft:short-content"
        res.confidence = max(res.confidence, 0.60)
        res.soft_reasons.append("short-content")
        if not res.reason:
            res.reason = (f"有效文本偏少（{text_len} < {min_text_len} 字符），"
                          f"疑似内容未完整下发")

    if slow_ms and elapsed_ms and elapsed_ms > slow_ms:
        res.soft = True
        res.blocked = True
        res.vendor = res.vendor or "soft:slow-response"
        res.confidence = max(res.confidence, 0.55)
        res.soft_reasons.append("slow-response")
        if not res.reason:
            res.reason = (f"响应过慢（{elapsed_ms}ms > {slow_ms}ms），"
                          f"疑似被限速——升级层级可能反而更快")

    if not res.soft:
        res.reason = "未检测到软限制特征"
    return res


@dataclass
class DetectResult:
    blocked: bool = False
    vendor: str | None = None
    reason: str = ""
    confidence: float = 0.0
    signals: list[str] = field(default_factory=list)
    rate_limited: bool = False
    login_wall: bool = False
    empty_content: bool = False
    soft: bool = False                      # 是否为"软限制"（拿到内容但不完整/被降级）
    soft_reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "blocked": self.blocked,
            "vendor": self.vendor,
            "block_reason": self.reason or None,
            "confidence": round(self.confidence, 2),
            "signals": self.signals,
            "rate_limited": self.rate_limited,
            "login_wall": self.login_wall,
            "empty_content": self.empty_content,
            "soft": self.soft,
            "soft_reasons": self.soft_reasons,
        }


def looks_like_challenge(*, html: str | None = None, title: str | None = None,
                         text: str | None = None) -> tuple[bool, str | None, str]:
    """只判断"是否像挑战页"，**不做长度判定**。

    与 detect_block 的区别：detect_block 是终局判定（决定升级/失败）；
    本函数用于**浏览器层的等待循环** —— 挑战页可能几秒后自动放行，
    此时不该急着报"被拦"，而应等它。
    """
    probe = f"{title or ''}\n{(html or '')[:20000]}"
    for vendor, pats, desc in VENDOR_SIGNATURES:
        hit = _scan(pats, probe)
        if hit:
            return True, vendor, f"{desc}（{hit}）"
    hit = _scan(GENERIC_CAPTCHA, probe)
    if hit:
        return True, "captcha", f"验证码特征：{hit}"
    return False, None, ""


def _scan(patterns: list[str], haystack: str) -> str | None:
    for p in patterns:
        if re.search(p, haystack, re.I):
            return p
    return None


def detect_block(*, status: int | None, html: str | None, text: str | None,
                 title: str | None = None, min_text_len: int = 80) -> DetectResult:
    """对一次抓取结果做拦截判定。html/text 传原始内容。"""
    res = DetectResult()
    head = (html or "")[:20000]                      # 只扫开头，挑战页特征都在前部
    ttl = (title or "")
    body = (text or "")
    probe = f"{ttl}\n{head}"

    # 1) 状态码
    if status in HARD_BLOCK_STATUS:
        res.blocked = True
        res.confidence = max(res.confidence, 0.7)
        res.signals.append(f"status={status}")
        res.reason = f"HTTP {status} 拒绝访问"
    if status in RATE_LIMIT_STATUS:
        res.rate_limited = True
        res.blocked = True
        res.confidence = max(res.confidence, 0.8)
        res.signals.append(f"status={status}")
        res.reason = f"HTTP {status} 限流/服务不可用"
    if status in GATEWAY_STATUS:
        res.blocked = True
        res.confidence = max(res.confidence, 0.5)
        res.signals.append(f"status={status}")
        res.reason = res.reason or f"HTTP {status} 网关类拦截"

    # 2) 厂商指纹
    for vendor, pats, desc in VENDOR_SIGNATURES:
        hit = _scan(pats, probe)
        if hit:
            res.blocked = True
            res.vendor = res.vendor or vendor
            res.confidence = max(res.confidence, 0.95)
            res.signals.append(f"vendor:{vendor}")
            res.reason = res.reason or f"{desc}：命中特征 {hit}"
            break

    # 3) 通用验证码
    if not res.vendor:
        hit = _scan(GENERIC_CAPTCHA, probe)
        if hit:
            res.blocked = True
            res.vendor = "captcha"
            res.confidence = max(res.confidence, 0.85)
            res.signals.append(f"captcha:{hit}")
            res.reason = res.reason or f"页面出现验证码特征：{hit}"

    # 4) 登录墙
    hit = _scan(LOGIN_WALL, f"{ttl}\n{body[:5000]}")
    if hit:
        res.login_wall = True
        res.blocked = True
        res.vendor = res.vendor or "login-wall"
        res.confidence = max(res.confidence, 0.75)
        res.signals.append(f"login:{hit}")
        res.reason = res.reason or f"疑似登录墙：{hit}"

    # 5) 内容为空（且非正常短页）
    text_len = len(body.strip())
    if not res.blocked and text_len < min_text_len:
        shell = _scan(SPA_SHELL, head)
        res.empty_content = True
        res.blocked = True
        res.confidence = max(res.confidence, 0.6 if shell else 0.4)
        res.signals.append(f"empty(text={text_len})" + (f" shell:{shell}" if shell else ""))
        res.reason = ("页面为空且命中 SPA 空挂载点：内容可能由 JS 渲染，需升级浏览器层"
                      if shell else f"有效文本过短（{text_len} 字符），疑似未取到内容")

    if not res.reason:
        res.reason = "未检测到拦截特征"
        res.confidence = 0.05

    return res


# 供上层决定"是否值得升级"：低置信度的 empty_content 优先升浏览器层
def should_escalate(d: DetectResult, *, current_tier: int, max_tier: int) -> tuple[bool, str]:
    if current_tier >= max_tier:
        return False, "已达最高层级上限"
    if not d.blocked:
        return False, "未被拦截"
    if d.vendor in {"captcha", "cloudflare-turnstile", "geetest"}:
        return True, "命中验证码类挑战——升到强指纹层可能仍失败，需人工介入"
    if d.vendor == "login-wall":
        return False, "疑似登录墙，升级不会解决；需提供登录态"
    # 软限制：站点没有拒绝你，只是"少给"。
    # 除了"受登录/订阅限制"这两类——它们是**访问控制**而非技术防护，
    # 升级既解决不了问题，也越过了本工具的合规红线，必须停在原地报给用户。
    if d.soft:
        if d.vendor in {"soft:soft-login", "soft:soft-paywall"}:
            return False, ("内容受登录/订阅限制，升级不会解决；"
                           "本工具不绕过访问控制，请自行确认是否有权访问")
        return True, (f"命中软限制（{'/'.join(d.soft_reasons) or d.vendor}）"
                      f"——内容不完整或响应受限，升级重取")
    if d.rate_limited:
        return True, "命中限流——升级 + 降低频率；必要时改代理"
    if d.empty_content:
        return True, "内容为空/未渲染——需要浏览器层执行 JS"
    return True, f"命中 {d.vendor or '未知拦截'}，尝试升级"


# --------------------------------------------------------------------------- #
# 最小 CLI：本文件原本是纯库（import 进来什么都跑不了），给个可用的自检入口
#   python detect.py --file page.html --status 403 --json   # 退出码 0=判定被拦
#   type page.html | python detect.py -                     # 从 stdin 读
# --------------------------------------------------------------------------- #
def _cli() -> int:
    import argparse
    import json
    import sys
    from pathlib import Path

    # 本文件是独立库（不 import _common），这里自备一份 UTF-8 输出保护：
    # 中文 Windows 下 stdout 被管道/重定向时默认 gbk，中文 reason 会直接
    # UnicodeEncodeError。与 _common._ensure_utf8_stdout 同口径。
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass

    ap = argparse.ArgumentParser(description="反爬/拦截信号检测（读本地 HTML 或 stdin）")
    ap.add_argument("--file", default="-", help="HTML 文件路径；默认 - 表示从 stdin 读")
    ap.add_argument("--status", type=int, default=None, help="HTTP 状态码")
    ap.add_argument("--title", default=None, help="页面标题（可选）")
    ap.add_argument("--min-text", type=int, default=80, help="内容下限字符（默认 80）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 画像")
    args = ap.parse_args()

    if args.file == "-":
        buf = getattr(sys.stdin, "buffer", None)
        raw = buf.read() if buf is not None else sys.stdin.read().encode("utf-8")
        try:
            html = raw.decode("utf-8")
        except UnicodeDecodeError:
            html = raw.decode("gbk", errors="replace")
    else:
        html = Path(args.file).read_text(encoding="utf-8", errors="replace")

    # 与 _common.html_to_text 的兜底分支同口径：去 script/style/注释后压空白
    body = re.sub(r"(?is)<(script|style|noscript|template|svg|head)\b[^>]*>.*?</\1>", " ", html)
    body = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()

    d = detect_block(status=args.status, html=html, text=body,
                     title=args.title, min_text_len=args.min_text)
    if args.json:
        print(json.dumps({
            "blocked": d.blocked, "vendor": d.vendor, "reason": d.reason,
            "confidence": round(d.confidence, 3), "rate_limited": d.rate_limited,
            "login_wall": d.login_wall, "empty_content": d.empty_content,
            "soft": d.soft, "soft_reasons": d.soft_reasons, "signals": d.signals,
        }, ensure_ascii=False, indent=2))
    else:
        print(f"blocked      : {d.blocked}")
        print(f"vendor       : {d.vendor or '-'}")
        print(f"reason       : {d.reason or '-'}")
        print(f"confidence   : {d.confidence:.2f}")
        print(f"rate_limited : {d.rate_limited}    empty_content: {d.empty_content}"
              f"    soft: {d.soft}")
        if d.signals:
            print("signals      : " + "; ".join(d.signals))
    return 0 if d.blocked else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
