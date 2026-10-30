#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""隐身自检：验证当前浏览器后端是否真的对反爬系统"看起来像真人"。

    python stealth_check.py                       # 用 patchright 跑本地指纹探针
    python stealth_check.py --backend cloakbrowser
    python stealth_check.py --sannysoft           # 额外加载 bot.sannysoft.com 实测
    python stealth_check.py --headless

设计说明：本地探针不依赖外网，检查的是自动化工具最经典的几处暴露点；
--sannysoft 会真实访问公开的反爬测试页，作为端到端佐证。
"""
from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tiers as _tiers  # noqa: E402
from _common import _ensure_utf8_stdout, short  # noqa: E402

# 本脚本最小可独立运行：显式切换 UTF-8，避免中文 Windows 下 `| cat` / 重定向时
# 在 ✓ ✗ 上抛 UnicodeEncodeError: 'gbk' codec can't encode character '\u2713'。
_ensure_utf8_stdout()

PROBE_JS = r"""
() => {
  const nav = navigator, win = window;
  const gl = (() => {
    try {
      const c = document.createElement('canvas');
      const g = c.getContext('webgl') || c.getContext('experimental-webgl');
      const d = g.getExtension('WEBGL_debug_renderer_info');
      return d ? g.getParameter(d.UNMASKED_RENDERER_WEBGL) : null;
    } catch (e) { return 'ERR:' + e.message; }
  })();
  return {
    webdriver: nav.webdriver,
    has_chrome: typeof win.chrome === 'object' && win.chrome !== null,
    chrome_runtime: !!(win.chrome && win.chrome.runtime),
    plugins_len: nav.plugins ? nav.plugins.length : -1,
    mimeTypes_len: nav.mimeTypes ? nav.mimeTypes.length : -1,
    languages: nav.languages,
    platform: nav.platform,
    hardwareConcurrency: nav.hardwareConcurrency,
    deviceMemory: nav.deviceMemory,
    userAgent: nav.userAgent,
    webgl_vendor: gl,
    permissions_notification: null,
    cdc_props: Object.keys(win).filter(k => /^(cdc_|\$cdc_|_phantom|__nightmare)/.test(k)),
    playwright_props: Object.keys(win).filter(k => /playwright|__pw|_pw_/i.test(k)),
    iframe_webdriver: (() => {
      try { const f = document.createElement('iframe'); document.body.appendChild(f);
            const w = f.contentWindow.navigator.webdriver; f.remove(); return w; }
      catch (e) { return 'ERR'; }
    })(),
  };
}
"""

# 逐项判定：(键, 人类期望, 说明)
# 注意 has_chrome 仅对 Chromium 系适用 —— camoufox 是 Firefox，没有 window.chrome 是正常的
CHECKS = [
    ("webdriver", False, "navigator.webdriver 应为 false/undefined（自动化标志）"),
    ("has_chrome", True, "window.chrome 应存在（**仅 Chromium 系适用**）"),
    ("plugins_len", lambda v: v > 0, "navigator.plugins 不应为空"),
    ("mimeTypes_len", lambda v: v > 0, "navigator.mimeTypes 不应为空"),
    ("cdc_props", [], "不应暴露 ChromeDriver 的 cdc_ 属性"),
    ("playwright_props", [], "不应暴露 Playwright 注入的全局变量"),
    ("iframe_webdriver", lambda v: not v, "iframe 内 navigator.webdriver 应为 false"),
]

CHROMIUM_BACKENDS = {"patchright", "cloakbrowser"}

# chrome 通道失败回退 chromium 时记一条，最终写进 JSON —— 否则调用方只看到
# backend="patchright"，会误以为测的就是真实 Chrome 通道。
DEGRADATIONS: list[str] = []


def judge(key: str, value, expect) -> tuple[bool, str]:
    if callable(expect):
        ok = bool(expect(value))
    else:
        ok = (value == expect)
    return ok, f"{value!r}"


@contextlib.contextmanager
def open_session(backend: str, *, headless: bool, proxy: str | None = None):
    """按后端打开一个页面会话，统一 yield page 并在退出时正确清理。"""
    if backend == "patchright":
        from patchright.sync_api import sync_playwright
        ch = "chrome" if _tiers.chrome_present() else "chromium"
        with sync_playwright() as p:
            try:
                ctx = _tiers._patchright_launch(p, headless=headless, channel=ch,
                                                proxy=proxy, user_data_dir=None)
            except Exception as e:
                if ch != "chrome":
                    raise
                # 已知坑：系统 Chrome 与 patchright 版本代差时 channel=chrome 必失败
                # （ProcessSingleton / Crashpad settings.dat 0x5）。回退 chromium，
                # 并把原因打出来 —— 不要让用户看到 playwright 的原始栈去猜。
                ch = "chromium"
                reason = f"chrome 通道启动失败，已回退 chromium：{short(str(e), 200)}"
                DEGRADATIONS.append(reason)
                print(f"[!] {reason}", file=sys.stderr)
                ctx = _tiers._patchright_launch(p, headless=headless, channel=ch,
                                                proxy=proxy, user_data_dir=None)
            try:
                yield ctx.new_page()
            finally:
                ctx.close()
    elif backend == "cloakbrowser":
        from cloakbrowser import launch
        kw = {"headless": headless, "humanize": True}
        if proxy:
            kw["proxy"] = proxy
        b = launch(**kw)
        try:
            yield b.new_page()
        finally:
            b.close()
    elif backend == "camoufox":
        from camoufox.sync_api import Camoufox
        kw = {"headless": headless}
        if proxy:
            kw["proxy"] = {"server": proxy}
        with Camoufox(**kw) as b:
            yield b.new_page()
    else:
        raise ValueError(f"未知后端 {backend}")


def probe_with(backend: str, *, headless: bool) -> dict | None:
    """打开 about:blank 并执行指纹探针。"""
    with open_session(backend, headless=headless) as page:
        page.goto("about:blank")
        return page.evaluate(PROBE_JS)


SANNY_JS = r"""
() => {
  const rows = [...document.querySelectorAll('table tr')];
  const failed = [], passed = [];
  for (const r of rows) {
    const tds = r.querySelectorAll('td');
    if (tds.length < 2) continue;
    const name = tds[0].innerText.trim();
    const val  = tds[1].innerText.trim();
    const cls  = tds[1].className || '';
    if (/fail|warn/i.test(cls)) failed.push(name + ' = ' + val);
    else passed.push(name);
  }
  return {passed_count: passed.length, failed_count: failed.length,
          failed: failed.slice(0, 20)};
}
"""


def sannysoft(backend: str, *, headless: bool) -> dict | None:
    """加载 bot.sannysoft.com 并统计通过/失败项（支持全部后端）。"""
    with open_session(backend, headless=headless) as page:
        page.goto("https://bot.sannysoft.com/", wait_until="networkidle", timeout=90000)
        page.wait_for_timeout(4000)
        return page.evaluate(SANNY_JS)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="patchright",
                    choices=["patchright", "cloakbrowser", "camoufox"])
    ap.add_argument("--headless", action="store_true",
                    help="无头模式（默认有头；无头会通过 UA 暴露 HeadlessChrome）")
    ap.add_argument("--sannysoft", action="store_true", help="额外访问 bot.sannysoft.com 实测")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    avail, why = _tiers.backend_available(args.backend)
    if not avail:
        print(f"后端不可用：{args.backend} —— {why}", file=sys.stderr)
        return 3

    print(f"隐身自检后端：{args.backend}  headless={args.headless}")
    print("=" * 68)
    data = probe_with(args.backend, headless=args.headless)
    if data is None:
        print("未能取得探针数据", file=sys.stderr)
        return 1

    fails = 0
    is_chromium = args.backend in CHROMIUM_BACKENDS
    for key, expect, desc in CHECKS:
        if key == "has_chrome" and not is_chromium:
            print(f"  [–] {key:<20} {'(跳过)':<38} camoufox 为 Firefox，无 window.chrome 属正常")
            continue
        ok, shown = judge(key, data.get(key), expect)
        if not ok:
            fails += 1
        print(f"  [{'✓' if ok else '✗'}] {key:<20} {shown:<38} {desc}")

    print("-" * 68)
    for k in ("userAgent", "platform", "languages", "hardwareConcurrency",
              "deviceMemory", "webgl_vendor"):
        print(f"      {k:<20} {data.get(k)}")

    out = {"backend": args.backend, "headless": args.headless,
           "local_checks_failed": fails, "probe": data}
    if DEGRADATIONS:
        out["degraded"] = True
        out["degraded_reason"] = "；".join(DEGRADATIONS)

    if args.sannysoft:
        print("=" * 68)
        print("加载 bot.sannysoft.com 实测（公开的反爬指纹测试页）…")
        try:
            s = sannysoft(args.backend, headless=args.headless)
            if s:
                out["sannysoft"] = s
                print(f"  通过 {s['passed_count']} 项 / 失败 {s['failed_count']} 项")
                chrome_only = ("chrome", "headchr", "chrmemory", "chrmemory")
                for f in s.get("failed", []):
                    low = f.lower()
                    tag = ""
                    if not is_chromium and any(k in low for k in chrome_only):
                        tag = "   ← Chrome 专属项，对 Firefox 不适用"
                    print(f"    ✗ {f}{tag}")
                if not is_chromium:
                    print("  说明：bot.sannysoft.com 以 Chrome 为基准，"
                          "camoufox 是 Firefox，Chrome 相关项失败不构成实际问题。")
        except Exception as e:
            print(f"  sannysoft 测试失败：{type(e).__name__}: {e}")

    print("=" * 68)
    print(f"本地探针：{'全部通过 ✓' if fails == 0 else f'{fails} 项未通过 ✗'}")
    if fails:
        print("提示：保持默认【有头】；Chromium 系建议 channel=chrome，"
              "仍不过则考虑升级到 T2（cloakbrowser / camoufox）。")
    if not is_chromium:
        print("注：camoufox 走 Firefox 路线，与 Chromium 系的检测项不完全通用。")
    if DEGRADATIONS:
        print("[!] 注意：本次 chrome 通道不可用，实际测的是 patchright 自带 Chromium"
              "（反检测能力略弱于真实 Chrome）。")
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if fails == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
