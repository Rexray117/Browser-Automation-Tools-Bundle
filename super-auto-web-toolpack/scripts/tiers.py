# -*- coding: utf-8 -*-
"""分层后端实现。每个后端填满统一返回契约；不可用时优雅降级为 ok=False。

层级与后端
----------
T0  curl_cffi                     TLS/JA3 + HTTP2 指纹伪装，无浏览器，~200ms
T1  patchright                    补丁版 Playwright（Chromium），默认起步层
T2  cloakbrowser / camoufox       源码级指纹浏览器（C++ 补丁 / Firefox fork）
T3  manual                        需人工介入（验证码、登录态、代理）
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from _common import (STATE_DIR, TIER_NAMES, html_to_text, extract_title,
                     new_result, truncate)
import detect as _detect

DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# chrome 可执行文件的常见位置（用于 patchright 的 channel=chrome 探测）
_CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
]


def chrome_present() -> bool:
    return any(Path(p).exists() for p in _CHROME_CANDIDATES if p)


def _camoufox_ready() -> bool:
    """camoufox 的 Firefox 构建是否已下载。"""
    cands = []
    if os.environ.get("LOCALAPPDATA"):
        cands.append(Path(os.environ["LOCALAPPDATA"]) / "camoufox")
    cands += [Path.home() / ".cache" / "camoufox", Path.home() / ".camoufox"]
    return any(p.exists() and any(p.iterdir()) for p in cands)


def backend_available(backend: str) -> tuple[bool, str]:
    """探测后端是否**真正可用**（含浏览器二进制），返回 (可用, 说明)。

    注意：pip 装上包 ≠ 能跑。cloakbrowser / camoufox 还需各自下载
    改造过的浏览器二进制（几百 MB），缺失时这里直接判为不可用，
    让上层优雅跳过并给出可执行的修复指令，而不是等到运行时报错。
    """
    try:
        if backend == "curl_cffi":
            import curl_cffi  # noqa: F401
            return True, "curl_cffi 已安装"
        if backend == "patchright":
            import patchright  # noqa: F401
            ch = "含系统 Chrome" if chrome_present() else "仅 Chromium"
            return True, f"patchright 已安装；{ch}"
        if backend == "cloakbrowser":
            import cloakbrowser  # noqa: F401
            try:
                info = cloakbrowser.binary_info()
            except Exception:
                return True, "cloakbrowser 已安装（二进制状态未知）"
            if not info.get("installed"):
                return False, (
                    f"cloakbrowser 二进制缺失（约 536MB，version={info.get('version')}）"
                    f"—— 运行 python -c \"import cloakbrowser; cloakbrowser.ensure_binary()\"")
            return True, f"cloakbrowser 已就绪（tier={info.get('tier')}）"
        if backend == "camoufox":
            import camoufox  # noqa: F401
            if not _camoufox_ready():
                return False, "camoufox 的 Firefox 构建缺失 —— 运行 python -m camoufox fetch"
            return True, "camoufox 已就绪"
    except Exception as e:
        return False, f"未安装或导入失败：{type(e).__name__}: {e}"
    return False, f"未知后端 {backend}"


def _finalize(res: dict, *, status, html, title=None, text=None,
              min_text_len: int = 200, start_ts: float | None = None,
              slow_ms: int = 0, empty_floor: int = 200) -> dict:
    """统一收尾：抽文本 → 抽标题 → 跑硬拦截检测 → 跑软限制检测。

    注意两套阈值的语义区别（混用会导致误报）：
      * ``empty_floor``（硬）：低于此值 = "根本没取到内容"，固定低阈值
      * ``min_text_len``（软，对应用户的 --min-text）：低于此值 = "取到了但不够多"
    """
    res["status"] = status
    res["html"] = truncate(html)
    res["text"] = truncate(text if text is not None else html_to_text(html))
    res["title"] = title or extract_title(html)

    elapsed = int((time.time() - start_ts) * 1000) if start_ts else None
    if elapsed is not None:
        res["elapsed_ms"] = elapsed

    hard = _detect.detect_block(status=status, html=html, text=res["text"],
                                title=res["title"], min_text_len=empty_floor)

    # 软限制只在硬拦截未命中时判定 —— 否则原因已经明确，不必再猜
    soft = _detect.DetectResult()
    if not hard.blocked:
        soft = _detect.detect_soft_limit(
            status=status, html=html, text=res["text"], title=res["title"],
            elapsed_ms=elapsed, min_text_len=min_text_len, slow_ms=slow_ms)

    d = hard if hard.blocked else soft
    res["blocked"] = d.blocked
    res["block_vendor"] = d.vendor
    res["block_reason"] = d.reason
    res["soft_limited"] = bool(d.soft)
    res["soft_reasons"] = list(d.soft_reasons)
    res["detect"] = {**hard.as_dict(), "soft_detail": soft.as_dict()}
    res["ok"] = (not d.blocked) and status is not None and 200 <= status < 400
    return res


def _await_challenge_pass(page, *, timeout_ms: int = 15_000,
                          poll_ms: int = 1_000) -> tuple[bool, int, str | None]:
    """若落在挑战页上，等待它自动放行。

    **为什么必须有这一步**（实测教训）
    ----------------------------------
    GSMArena 的 Cloudflare Turnstile 挑战页实测在 **约 9 秒后自动放行**：
        0.0s  title='GSMArena Turnstile check'
        9.0s  title='Apple iPhone 15 Pro - Full phone specifications'

    若只等固定的 2–3 秒就取内容，会把"慢放行"误判成"被拦"——
    下游于是白白升级到更重的层级（T2 反而更慢、且未必更好）。

    返回 ``(是否已通过, 实际等待毫秒, 若仍未通过则给出厂商名)``。
    """
    if timeout_ms <= 0:
        return True, 0, None
    t0 = time.time()
    vendor: str | None = None
    while True:
        try:
            html = page.content()
            title = page.title()
        except Exception:
            # 挑战通过瞬间会触发导航，取不到内容 → 再等一轮
            if (time.time() - t0) * 1000 >= timeout_ms:
                return False, int((time.time() - t0) * 1000), vendor
            try:
                page.wait_for_timeout(poll_ms)
            except Exception:
                time.sleep(poll_ms / 1000.0)
            continue
        is_ch, vendor, _why = _detect.looks_like_challenge(html=html, title=title)
        if not is_ch:
            return True, int((time.time() - t0) * 1000), None
        if (time.time() - t0) * 1000 >= timeout_ms:
            return False, int((time.time() - t0) * 1000), vendor
        page.wait_for_timeout(poll_ms)


# 各层级的"响应过慢"默认阈值（毫秒）。0 = 不启用。
# 只有 T0 有意义：一个纯 GET 正常应 <2s；拖到 10s 以上基本是被限速。
# T1/T2 是浏览器层，本身就要 5–8s，用固定阈值会造成全员误报，故默认关闭。
DEFAULT_SLOW_MS = {0: 10_000, 1: 0, 2: 0}


def _resolve_slow(tier: int, slow_ms: int | None) -> int:
    return DEFAULT_SLOW_MS.get(tier, 0) if slow_ms is None else slow_ms


def _safe_content(page, retries: int = 4, settle_ms: int = 900) -> str:
    """稳健取页面 HTML —— 规避"页面正在导航中"导致取内容失败。

    真实场景：命中 Cloudflare Turnstile 等挑战页时，页面会在挑战通过/重定向的
    瞬间触发导航，此时调用 page.content() 会抛
    "Unable to retrieve content because the page is navigating and changing the content"。
    实测 GSMArena 就稳定触发这个错误。

    做法：失败时等待导航落定后重试；挑战页本身的内容也要能取到，
    否则会把"被拦"误报成"未知错误"，下游就不知道该升级了。
    """
    last_exc: Exception | None = None
    for i in range(max(1, retries)):
        try:
            return page.content()
        except Exception as e:            # noqa: BLE001 —— 上游异常类型不稳定，统一兜住
            last_exc = e
            try:
                page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
            try:
                page.wait_for_timeout(settle_ms)
            except Exception:
                time.sleep(settle_ms / 1000.0)
    if last_exc:
        raise last_exc
    return ""


# --------------------------------------------------------------------------- #
# T0 · curl_cffi
# --------------------------------------------------------------------------- #
def fetch_curl_cffi(url: str, *, timeout: float = 30.0, impersonate: str = "chrome",
                    proxy: str | None = None, headers: dict | None = None,
                    min_text_len: int = 200, slow_ms: int | None = None) -> dict:
    res = new_result(url, tier=0, backend="curl_cffi")
    t0 = time.time()
    try:
        from curl_cffi import requests as creq
    except Exception as e:
        res["error"] = f"curl_cffi 不可用：{e}"
        return res
    try:
        kwargs = {"timeout": timeout, "allow_redirects": True, "impersonate": impersonate}
        if proxy:
            kwargs["proxies"] = {"http": proxy, "https": proxy}
        if headers:
            kwargs["headers"] = headers
        r = creq.get(url, **kwargs)
        res["final_url"] = str(r.url)
        res = _finalize(res, status=r.status_code, html=r.text,
                        min_text_len=min_text_len, start_ts=t0,
                        slow_ms=_resolve_slow(0, slow_ms))
    except Exception as e:
        res["error"] = f"{type(e).__name__}: {e}"
    res["elapsed_ms"] = int((time.time() - t0) * 1000)
    return res


# --------------------------------------------------------------------------- #
# T1 · patchright（默认起步层）
# --------------------------------------------------------------------------- #
def _patchright_launch(p, *, headless: bool, channel: str, proxy: str | None,
                       user_data_dir: str | None, no_viewport: bool = True):
    """按 patchright 官方最佳实践启动：持久上下文 + 真实 Chrome + 不注入 UA。

    官方要点（见 references/adapters.md）：
      - 用 launch_persistent_context，并启用 no_viewport
      - 优先 channel="chrome"（真实 Chrome 比 Chromium 更不易被识别）
      - **不要**自定义 UA / 额外 header，否则反而增加指纹熵
    """
    udd = user_data_dir or str(STATE_DIR / "profiles" / "patchright")
    Path(udd).mkdir(parents=True, exist_ok=True)
    kwargs = dict(user_data_dir=udd, headless=headless, no_viewport=no_viewport)
    if proxy:
        kwargs["proxy"] = {"server": proxy}
    if channel and channel != "chromium":
        kwargs["channel"] = channel
    return p.chromium.launch_persistent_context(**kwargs)


def fetch_patchright(url: str, *, headless: bool = False, channel: str = "auto",
                     timeout_ms: int = 45000, wait_ms: int = 2500,
                     proxy: str | None = None, user_data_dir: str | None = None,
                     min_text_len: int = 200, slow_ms: int | None = None,
                     challenge_wait_ms: int = 15_000,
                     **_ignored) -> dict:
    res = new_result(url, tier=1, backend="patchright")
    t0 = time.time()
    try:
        from patchright.sync_api import sync_playwright
    except Exception as e:
        res["error"] = f"patchright 不可用：{e}"
        return res

    ch = "chrome" if (channel == "auto" and chrome_present()) else \
         ("chromium" if channel == "auto" else channel)
    ctx = None
    try:
        with sync_playwright() as p:
            try:
                ctx = _patchright_launch(p, headless=headless, channel=ch, proxy=proxy,
                                         user_data_dir=user_data_dir)
            except Exception:
                if ch == "chrome":       # 回退到 Chromium
                    ch = "chromium"
                    ctx = _patchright_launch(p, headless=headless, channel=ch,
                                             proxy=proxy, user_data_dir=user_data_dir)
                else:
                    raise
            page = ctx.new_page()
            resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=min(15000, timeout_ms))
            except Exception:
                pass                          # 长连接站点不会 networkidle，忽略
            if wait_ms:
                page.wait_for_timeout(wait_ms)
            ch_ok, ch_waited, ch_vendor = _await_challenge_pass(
                page, timeout_ms=challenge_wait_ms)
            res["challenge_wait_ms"] = ch_waited
            if not ch_ok:
                res["challenge_unpassed"] = ch_vendor or "challenge"
            html = _safe_content(page)
            res["final_url"] = page.url
            res["backend"] = f"patchright[{ch}]"
            res = _finalize(res, status=(resp.status if resp else None),
                            html=html, title=page.title(), min_text_len=min_text_len,
                            start_ts=t0, slow_ms=_resolve_slow(1, slow_ms))
    except Exception as e:
        res["error"] = f"{type(e).__name__}: {e}"
        if "Executable doesn't exist" in str(e) or "playwright install" in str(e):
            res["error"] += "  → 需执行：patchright install chromium（或 install chrome）"
    finally:
        try:
            if ctx:
                ctx.close()
        except Exception:
            pass
    res["elapsed_ms"] = int((time.time() - t0) * 1000)
    return res


# --------------------------------------------------------------------------- #
# T2 · cloakbrowser / camoufox
# --------------------------------------------------------------------------- #
def fetch_cloakbrowser(url: str, *, headless: bool = False, humanize: bool = True,
                       timeout_ms: int = 45000, wait_ms: int = 2500,
                       proxy: str | None = None, geoip: bool = False,
                       timezone: str | None = None, locale: str | None = None,
                       min_text_len: int = 200, slow_ms: int | None = None,
                       challenge_wait_ms: int = 15_000,
                       **_ignored) -> dict:
    res = new_result(url, tier=2, backend="cloakbrowser")
    t0 = time.time()
    try:
        from cloakbrowser import launch
    except Exception as e:
        res["error"] = f"cloakbrowser 不可用：{e}"
        return res
    browser = None
    try:
        kw = {"headless": headless, "humanize": humanize}
        if proxy:
            kw["proxy"] = proxy
        if geoip:
            kw["geoip"] = True
        if timezone:
            kw["timezone"] = timezone
        if locale:
            kw["locale"] = locale
        browser = launch(**kw)
        page = browser.new_page()
        resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        if wait_ms:
            page.wait_for_timeout(wait_ms)
        ch_ok, ch_waited, ch_vendor = _await_challenge_pass(
            page, timeout_ms=challenge_wait_ms)
        res["challenge_wait_ms"] = ch_waited
        if not ch_ok:
            res["challenge_unpassed"] = ch_vendor or "challenge"
        html = _safe_content(page)
        res["final_url"] = page.url
        res = _finalize(res, status=(resp.status if resp else None), html=html,
                        title=page.title(), min_text_len=min_text_len,
                        start_ts=t0, slow_ms=_resolve_slow(2, slow_ms))
    except Exception as e:
        res["error"] = f"{type(e).__name__}: {e}"
    finally:
        try:
            if browser:
                browser.close()
        except Exception:
            pass
    res["elapsed_ms"] = int((time.time() - t0) * 1000)
    return res


def fetch_camoufox(url: str, *, headless: bool = False, timeout_ms: int = 45000,
                   wait_ms: int = 2500, proxy: str | None = None,
                   humanize: bool = True, geoip: bool = False,
                   min_text_len: int = 200, slow_ms: int | None = None,
                   challenge_wait_ms: int = 15_000,
                   **_ignored) -> dict:
    res = new_result(url, tier=2, backend="camoufox")
    t0 = time.time()
    try:
        from camoufox.sync_api import Camoufox
    except Exception as e:
        res["error"] = f"camoufox 不可用：{e}"
        return res
    try:
        kw = {"headless": headless, "humanize": humanize}
        if proxy:
            kw["proxy"] = {"server": proxy}
        if geoip:
            kw["geoip"] = True
        with Camoufox(**kw) as browser:
            page = browser.new_page()
            resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            if wait_ms:
                page.wait_for_timeout(wait_ms)
            ch_ok, ch_waited, ch_vendor = _await_challenge_pass(
                page, timeout_ms=challenge_wait_ms)
            res["challenge_wait_ms"] = ch_waited
            if not ch_ok:
                res["challenge_unpassed"] = ch_vendor or "challenge"
            html = _safe_content(page)
            res["final_url"] = page.url
            res = _finalize(res, status=(resp.status if resp else None), html=html,
                            title=page.title(), min_text_len=min_text_len,
                            start_ts=t0, slow_ms=_resolve_slow(2, slow_ms))
    except Exception as e:
        res["error"] = f"{type(e).__name__}: {e}"
        if "Camoufox" in str(e) and ("not found" in str(e).lower() or "fetch" in str(e).lower()):
            res["error"] += "  → 需先执行：python -m camoufox fetch"
    res["elapsed_ms"] = int((time.time() - t0) * 1000)
    return res


# --------------------------------------------------------------------------- #
# 注册表
# --------------------------------------------------------------------------- #
FETCHERS = {
    "curl_cffi":    fetch_curl_cffi,
    "patchright":   fetch_patchright,
    "cloakbrowser": fetch_cloakbrowser,
    "camoufox":     fetch_camoufox,
}

# 每个层级候选后端的默认尝试顺序
TIER_CHAIN: dict[int, list[str]] = {
    0: ["curl_cffi"],
    1: ["patchright"],
    2: ["cloakbrowser", "camoufox"],
}


def run_backend(backend: str, url: str, options: dict | None = None) -> dict:
    fn = FETCHERS.get(backend)
    if not fn:
        r = new_result(url, tier=3, backend=backend)
        r["error"] = f"未知后端：{backend}"
        return r
    return fn(url, **(options or {}))
