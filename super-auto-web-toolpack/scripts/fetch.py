#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""super-auto-web-toolpack 主编排器。

用法
----
    python fetch.py <url> [选项]
    python fetch.py --urls-file urls.txt [选项]

核心行为
--------
1. 先查站点画像：该域名上次走通的层级 → 直接从该层起步（避免每次从 T0 重试）
2. 按 T0 → T1 → T2 逐级尝试，用 detect.py 判定是否被拦，命中即升级
3. 同域限速（默认 1s + ±20% 抖动）/ robots.txt 预检 / 成功层级回写画像
4. 结果按日期落盘 JSON + CSV

退出码
------
    0  成功
    1  一般失败（网络异常、DNS、超时）
    2  被拦截且已达最高层级（需人工介入 / 换代理）
    3  依赖缺失（对应层级后端不可用）
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (DomainRateLimiter, LatencyBaseline, RobotsGate, TIER_NAMES,
                     append_history, domain_of, get_profile, now_iso, short,
                     update_profile)
import detect as _detect
import tiers as _tiers

EXIT_OK, EXIT_FAIL, EXIT_BLOCKED, EXIT_MISSING_DEP = 0, 1, 2, 3


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="分层网页抓取：T0 curl_cffi → T1 patchright → T2 强指纹浏览器",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("url", nargs="?", help="目标 URL")
    p.add_argument("--urls-file", help="批量模式：每行一个 URL（# 开头为注释）")
    p.add_argument("--start-tier", type=int, default=None, choices=[0, 1, 2],
                   help="强制起始层级（默认取站点画像，无画像则 0）")
    p.add_argument("--max-tier", type=int, default=2, choices=[0, 1, 2],
                   help="允许升到的最高层级（默认 2）")
    p.add_argument("--only-tier", type=int, default=None, choices=[0, 1, 2],
                   help="只用该层级，不自动升级")
    p.add_argument("--tier2", default="cloakbrowser,camoufox",
                   help="T2 候选后端顺序，逗号分隔（默认 cloakbrowser,camoufox）")
    p.add_argument("--headless", action="store_true",
                   help="强制无头模式。默认【有头】：实测无头模式的 UA 会暴露 "
                        "'HeadlessChrome'，在 bot.sannysoft.com 上多失败 3 项")
    p.add_argument("--proxy", help="HTTP/SOCKS 代理，如 http://127.0.0.1:7890")
    p.add_argument("--timeout", type=float, default=30.0, help="T0 超时秒数")
    p.add_argument("--timeout-ms", type=int, default=45000, help="浏览器层超时毫秒")
    p.add_argument("--wait-ms", type=int, default=2500, help="浏览器渲染后额外等待毫秒")
    p.add_argument("--challenge-wait", type=int, default=15000, dest="challenge_wait",
                   help="命中挑战页时，最多等多久让它自动放行（毫秒，默认 15000）。"
                        "实测 Cloudflare Turnstile 非交互挑战约 9s 后自动通过，"
                        "等太短会把'慢放行'误报成'被拦'")
    p.add_argument("--min-interval", type=float, default=1.0,
                   help="同域最小请求间隔秒（默认 1.0）")
    p.add_argument("--min-text", type=int, default=200,
                   help="内容下限（字符）。低于此值视为'内容未完整下发'并触发升级（默认 200）")
    p.add_argument("--slow-ms", type=int, default=None,
                   help="响应过慢阈值（毫秒），超过即视为被限速并升级。"
                        "显式指定后不再使用相对基线")
    p.add_argument("--slow-factor", type=float, default=3.0,
                   help="相对基线倍数：超过本站该层历史中位数的 N 倍即判限速（默认 3.0）")
    p.add_argument("--baseline-n", type=int, default=10,
                   help="相对基线保留的样本数上限（默认 10）")
    p.add_argument("--no-baseline", action="store_true",
                   help="关闭相对基线，只用固定阈值")
    p.add_argument("--require", default=None,
                   help="内容断言：正文/HTML 中必须匹配该正则，否则视为未取到并升级。"
                        "例如 --require 'Albert Einstein'")
    p.add_argument("--no-robots", action="store_true", help="跳过 robots.txt 预检（默认检查）")
    p.add_argument("--no-profile", action="store_true", help="不读写站点画像")
    p.add_argument("--out", default="./scrape_out", help="输出目录（默认 ./scrape_out）")
    p.add_argument("--no-save", action="store_true", help="不落盘，只在终端输出")
    p.add_argument("--json", action="store_true", help="以 JSON 输出结果（含 html 截断）")
    p.add_argument("--text", action="store_true", help="只输出正文纯文本")
    p.add_argument("--max-chars", type=int, default=6000, help="终端/JSON 中正文截断长度")
    p.add_argument("--backends", action="store_true", help="列出各后端可用性后退出")
    p.add_argument("--baselines", action="store_true",
                   help="列出已记录的耗时基线后退出")
    return p


def collect_urls(args) -> list[str]:
    urls: list[str] = []
    if args.url:
        urls.append(args.url.strip())
    if args.urls_file:
        for line in Path(args.urls_file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    return urls


def backend_options(args, backend: str, slow_ms: int | None = None) -> dict:
    common = {"proxy": args.proxy, "min_text_len": args.min_text,
              "slow_ms": slow_ms}
    if backend == "curl_cffi":
        return {**common, "timeout": args.timeout}
    if backend == "patchright":
        return {**common, "headless": args.headless, "timeout_ms": args.timeout_ms,
                "wait_ms": args.wait_ms, "challenge_wait_ms": args.challenge_wait}
    if backend == "cloakbrowser":
        return {**common, "headless": args.headless, "timeout_ms": args.timeout_ms,
                "wait_ms": args.wait_ms, "humanize": True,
                "challenge_wait_ms": args.challenge_wait}
    if backend == "camoufox":
        return {**common, "headless": args.headless, "timeout_ms": args.timeout_ms,
                "wait_ms": args.wait_ms, "challenge_wait_ms": args.challenge_wait}
    return common


def tier_chain(start: int, max_tier: int, args) -> list[tuple[int, str]]:
    def backends_for(t: int) -> list[str]:
        if t == 2 and args.tier2:
            return [b.strip() for b in args.tier2.split(",") if b.strip()]
        return _tiers.TIER_CHAIN[t]

    if args.only_tier is not None:
        return [(args.only_tier, b) for b in backends_for(args.only_tier)]
    chain: list[tuple[int, str]] = []
    for t in range(start, max_tier + 1):
        for b in backends_for(t):
            chain.append((t, b))
    return chain


def fetch_one(url: str, args, limiter: DomainRateLimiter,
              robots: RobotsGate, baseline: LatencyBaseline) -> tuple[dict, int]:
    """抓取单个 URL，返回 (结果, 退出码)。"""
    dom = domain_of(url)

    # robots 预检
    allowed, why = robots.allowed(url)
    if not allowed:
        r = {"url": url, "ok": False, "tier": None, "backend": None,
             "block_reason": f"robots.txt 拒绝：{why}", "error": "robots_disallowed",
             "fetched_at": now_iso(), "attempts": []}
        print(f"  ✗ robots.txt 不允许抓取：{url}\n    {why}", file=sys.stderr)
        return r, EXIT_BLOCKED

    # 起始层级：画像优先
    start = args.start_tier
    if start is None:
        if args.no_profile:
            start = 0
        else:
            prof = get_profile(url)
            start = int(prof.get("preferred_tier") or 0)
            if start:
                print(f"  画像：{dom} 上次走通 {TIER_NAMES[start]}，从该层起步", file=sys.stderr)

    chain = tier_chain(start, args.max_tier, args)
    attempts: list[dict] = []
    tried: list[dict] = []
    last: dict | None = None
    saw_missing_dep = False

    for tier, backend in chain:
        avail, why = _tiers.backend_available(backend)
        if not avail:
            saw_missing_dep = True
            attempts.append({"tier": tier, "backend": backend, "skipped": True,
                             "reason": why})
            print(f"  – 跳过 {TIER_NAMES[tier]} / {backend}：{short(why, 90)}",
                  file=sys.stderr)
            continue

        slow_ms, slow_basis = baseline.resolve_slow_ms(
            dom, tier, fixed_default=_tiers.DEFAULT_SLOW_MS.get(tier, 0),
            fixed_override=args.slow_ms)
        limiter.wait(dom)
        print(f"  → 尝试 {TIER_NAMES[tier]} / {backend}"
              + (f"  [慢阈值 {slow_ms}ms · {slow_basis}]" if slow_ms else ""),
              file=sys.stderr)
        res = _tiers.run_backend(backend, url,
                                 backend_options(args, backend, slow_ms=slow_ms))
        res["slow_threshold_ms"] = slow_ms
        res["slow_basis"] = slow_basis
        attempts.append({"tier": tier, "backend": backend, "ok": res.get("ok"),
                         "status": res.get("status"), "blocked": res.get("blocked"),
                         "vendor": res.get("block_vendor"),
                         "reason": res.get("block_reason"), "elapsed_ms": res.get("elapsed_ms"),
                         "error": res.get("error")})
        last = res
        tried.append(res)

        # 内容断言：长度检测发现不了"取到了但取不全"，用 --require 兜住
        if res.get("ok") and args.require:
            hay = f"{res.get('title') or ''}\n{res.get('text') or ''}"
            if not re.search(args.require, hay, re.I):
                res["ok"] = False
                res["blocked"] = True
                res["soft_limited"] = True
                res["soft_reasons"] = ["content-missing"]
                res["block_vendor"] = "content-missing"
                res["block_reason"] = (f"内容断言未命中：/{args.require}/ "
                                       f"—— 页面可能未完整渲染或需登录")
                attempts[-1]["ok"] = False
                attempts[-1]["vendor"] = "content-missing"
                attempts[-1]["reason"] = res["block_reason"]

        if res.get("ok"):
            res["attempts"] = attempts
            limiter.on_success(dom)
            baseline.record(dom, tier, res.get("elapsed_ms"))
            if not args.no_profile:
                update_profile(url, tier=tier, backend=res.get("backend") or backend,
                               ok=True, vendor=res.get("block_vendor"))
            print(f"  ✓ 成功：{TIER_NAMES[tier]} / {res.get('backend')} "
                  f"（{res.get('elapsed_ms')}ms, {len(res.get('text') or '')} 字符）",
                  file=sys.stderr)
            return res, EXIT_OK

        limiter.on_failure(dom)
        d = _detect.DetectResult(
            blocked=bool(res.get("blocked")), vendor=res.get("block_vendor"),
            reason=res.get("block_reason") or res.get("error") or "",
            soft=bool(res.get("soft_limited")),
            soft_reasons=list(res.get("soft_reasons") or []))
        ok_esc, why_esc = _detect.should_escalate(d, current_tier=tier,
                                                 max_tier=args.max_tier)
        print(f"  ✗ 失败：{res.get('block_reason') or res.get('error')}"
              f"{'' if ok_esc else f'  [{why_esc}]'}", file=sys.stderr)
        if not ok_esc:
            last = res
            break
        if res.get("error"):                 # 依赖/运行错误，继续试下一后端
            continue

    if last:
        # 兜底：全层级都没成功时，返回「内容最多」的那次 —— 不完整数据也胜过空手而归。
        # 实测动因：T2 并非总比 T1 强。camoufox 在 GSMArena 内容页会陷入
        # Cloudflare 挑战循环（0 字符），而同站 patchright 能拿到 7900+ 字符。
        best = max(tried, key=lambda r: len(r.get("text") or ""), default=None)
        if best is not None and best is not last and len(best.get("text") or "") > len(
                last.get("text") or ""):
            last = best
            last["best_effort"] = True
            last["best_effort_note"] = (
                f"全部层级均未完全成功；此处返回内容最多的一次尝试"
                f"（T{last.get('tier')} / {last.get('backend')}）")
            print(f"  ⚠ 兜底：返回内容最多的尝试 T{last.get('tier')} / "
                  f"{last.get('backend')}（{len(last.get('text') or '')} 字符）",
                  file=sys.stderr)
        last["attempts"] = attempts
        if not args.no_profile:
            update_profile(url, tier=last.get("tier") or 0,
                           backend=last.get("backend") or "?",
                           ok=False, vendor=last.get("block_vendor"))
    else:
        last = {"url": url, "ok": False, "backend": None, "tier": None,
                "error": "no_backend_available", "attempts": attempts,
                "block_reason": "所有候选后端均不可用", "fetched_at": now_iso()}

    if saw_missing_dep and not last.get("error"):
        return last, EXIT_MISSING_DEP
    return last, (EXIT_MISSING_DEP if last.get("error") == "no_backend_available"
                  else EXIT_BLOCKED)


def save_output(results: list[dict], args) -> Path | None:
    if args.no_save:
        return None
    outdir = Path(args.out)
    days: dict[str, list[dict]] = {}
    for r in results:
        d = domain_of(r.get("url") or "") or "unknown"
        days.setdefault(d, []).append(r)
    stamp = datetime.now().strftime("%Y%m%d")
    tstamp = datetime.now().strftime("%H%M%S")
    first = None
    for dom, rows in days.items():
        tgt = outdir / dom / stamp
        tgt.mkdir(parents=True, exist_ok=True)
        row = []
        for r in rows:
            slim = {k: v for k, v in r.items() if k != "html"}
            slim["text"] = (r.get("text") or "")[:200000]
            row.append(slim)
        (tgt / f"data-{tstamp}.json").write_text(
            json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
        with (tgt / f"data-{tstamp}.csv").open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["url", "ok", "tier", "backend", "status", "title",
                        "chars", "blocked", "soft", "soft_reasons",
                        "vendor", "reason", "elapsed_ms"])
            for r in rows:
                w.writerow([r.get("url"), r.get("ok"), r.get("tier"), r.get("backend"),
                            r.get("status"), short(r.get("title"), 80),
                            len(r.get("text") or ""), r.get("blocked"),
                            r.get("soft_limited"),
                            "/".join(r.get("soft_reasons") or []),
                            r.get("block_vendor"), short(r.get("block_reason"), 100),
                            r.get("elapsed_ms")])
        first = first or tgt
    return first


def print_human(res: dict, args) -> None:
    print("─" * 68)
    print(f"URL      : {res.get('url')}")
    if res.get("final_url") and res["final_url"] != res.get("url"):
        print(f"最终 URL : {res['final_url']}")
    print(f"结果     : {'✓ 成功' if res.get('ok') else '✗ 失败'}"
          f"   层级: {res.get('tier_name') or '-'}"
          f"   后端: {res.get('backend') or '-'}")
    if res.get("status"):
        print(f"HTTP     : {res['status']}   耗时: {res.get('elapsed_ms')}ms")
    if res.get("title"):
        print(f"标题     : {res['title']}")
    if res.get("blocked"):
        print(f"拦截     : {res.get('block_vendor')} — {res.get('block_reason')}")
    if res.get("soft_limited"):
        reasons = "/".join(res.get("soft_reasons") or []) or "soft-limit"
        print(f"⚠ 软限制 : {reasons} —— 拿到内容但可能不完整，已尝试升级")
    if res.get("best_effort"):
        print(f"⚠ 兜底   : {res.get('best_effort_note')}")
    if res.get("error"):
        print(f"错误     : {res['error']}")
    if res.get("slow_threshold_ms"):
        print(f"慢阈值   : {res['slow_threshold_ms']}ms  （{res.get('slow_basis')}）")
    if args.text and res.get("text"):
        print("─" * 68)
        print(res["text"][:args.max_chars])
    elif res.get("text"):
        print("─" * 68)
        print(short(res["text"], args.max_chars))
    if res.get("attempts") and len(res["attempts"]) > 1:
        print("─" * 68)
        print("层级尝试记录:")
        for a in res["attempts"]:
            flag = "✓" if a.get("ok") else ("–" if a.get("skipped") else "✗")
            print(f"  {flag} T{a.get('tier')} {a.get('backend'):<14} "
                  f"{short(a.get('reason') or a.get('error') or 'ok', 66)}")


def main() -> int:
    args = build_parser().parse_args()

    if args.backends:
        print("各后端可用性：")
        for b in _tiers.FETCHERS:
            ok, why = _tiers.backend_available(b)
            print(f"  [{'✓' if ok else '✗'}] {b:<14} {why}")
        return EXIT_OK

    if args.baselines:
        bl = LatencyBaseline(n=args.baseline_n, factor=args.slow_factor,
                             enabled=not args.no_baseline)
        s = bl.summary()
        if not s:
            print("尚无耗时基线记录（成功抓取若干次后自动积累）。")
            return EXIT_OK
        print(f"耗时基线（中位数 × {args.slow_factor:g} = 判慢阈值）：")
        print(f"  {'域名':<30} {'层':<4} {'样本':>4} {'中位数':>9} {'范围':>17} {'阈值':>9}")
        for k, v in sorted(s.items()):
            print(f"  {v['domain']:<30} {v['tier']:<4} {v['samples']:>4} "
                  f"{v['median_ms']:>7}ms {str(v['min_ms'])+'-'+str(v['max_ms']):>15}ms "
                  f"{int(v['median_ms']*args.slow_factor):>7}ms")
        return EXIT_OK

    urls = collect_urls(args)
    if not urls:
        print("错误：请提供 URL 或 --urls-file", file=sys.stderr)
        return EXIT_FAIL

    limiter = DomainRateLimiter(min_interval=args.min_interval)
    robots = RobotsGate(enabled=not args.no_robots)
    baseline = LatencyBaseline(n=args.baseline_n, factor=args.slow_factor,
                               enabled=not args.no_baseline)

    results: list[dict] = []
    worst = EXIT_OK
    for i, url in enumerate(urls, 1):
        if len(urls) > 1:
            print(f"\n[{i}/{len(urls)}] {url}", file=sys.stderr)
        res, code = fetch_one(url, args, limiter, robots, baseline)
        results.append(res)
        worst = max(worst, code)
        append_history({k: res.get(k) for k in
                        ("url", "ok", "tier", "backend", "status", "blocked",
                         "block_vendor", "block_reason", "elapsed_ms", "fetched_at")})

    if args.json:
        print(json.dumps(
            [{k: v for k, v in r.items() if k != "html"} for r in results],
            ensure_ascii=False, indent=2))
    else:
        for r in results:
            print_human(r, args)

    saved = save_output(results, args)
    if saved:
        print("─" * 68, file=sys.stderr)
        print(f"已落盘：{saved}", file=sys.stderr)

    ok_n = sum(1 for r in results if r.get("ok"))
    print(f"完成：{ok_n}/{len(results)} 成功", file=sys.stderr)
    return worst


if __name__ == "__main__":
    sys.exit(main())
