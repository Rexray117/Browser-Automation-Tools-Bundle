#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跨层级抓取性能基准：在指定站点上横向对比各后端的成功率、耗时与内容量。

    python benchmark.py --urls https://a.com https://b.com
    python benchmark.py --urls-file sites.txt --backends patchright,cloakbrowser,camoufox
    python benchmark.py --urls-file sites.txt --repeat 3 --md out.md --json out.json

设计要点
--------
* **同一 URL 依次跑所有后端**，保证对比公平（同网速、同时点）
* 统计维度：成功/被拦/跳过、判定出的反爬厂商、有效字符数、耗时
* 尊重 robots.txt（可用 --no-robots 关闭，但请自行确认站点条款）
* 不做并发——并发会污染耗时数据，也会给站点造成不必要的压力
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import DomainRateLimiter, RobotsGate, domain_of, short  # noqa: E402
import tiers as _tiers  # noqa: E402

BACKEND_TIER = {"curl_cffi": 0, "patchright": 1, "cloakbrowser": 2, "camoufox": 2}
DEFAULT_BACKENDS = "patchright,cloakbrowser,camoufox"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="跨层级抓取性能基准（T1 vs T2 cloakbrowser vs T2 camoufox）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--urls", nargs="*", default=[], help="目标 URL（可多个）")
    p.add_argument("--urls-file", help="每行一个 URL（# 开头为注释）")
    p.add_argument("--backends", default=DEFAULT_BACKENDS,
                   help=f"参与对比的后端，逗号分隔（默认 {DEFAULT_BACKENDS}）")
    p.add_argument("--repeat", type=int, default=1, help="每个组合重复次数（默认 1）")
    p.add_argument("--timeout-ms", type=int, default=60000, help="浏览器层超时毫秒")
    p.add_argument("--timeout", type=float, default=30.0, help="T0 超时秒数")
    p.add_argument("--wait-ms", type=int, default=2500, help="渲染后额外等待毫秒")
    p.add_argument("--challenge-wait", type=int, default=15000, dest="challenge_wait",
                   help="挑战页最多等多久自动放行（毫秒，默认 15000）")
    p.add_argument("--min-text", type=int, default=200, help="内容下限（字符）")
    p.add_argument("--proxy", help="走代理，如 http://127.0.0.1:7897")
    p.add_argument("--headless", action="store_true", help="无头模式（默认有头）")
    p.add_argument("--min-interval", type=float, default=1.0, help="同域最小间隔秒")
    p.add_argument("--no-robots", action="store_true", help="跳过 robots 预检")
    p.add_argument("--md", help="同时输出 Markdown 报告到该路径")
    p.add_argument("--json", help="同时输出 JSON 结果到该路径")
    return p


def collect_urls(args) -> list[str]:
    urls = list(args.urls)
    if args.urls_file:
        for line in Path(args.urls_file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def run_cell(backend: str, url: str, args, limiter: DomainRateLimiter,
             robots: RobotsGate) -> dict:
    tier = BACKEND_TIER.get(backend, 9)
    cell = {"backend": backend, "tier": tier, "url": url}

    avail, why = _tiers.backend_available(backend)
    if not avail:
        cell.update(skipped=True, reason=f"后端不可用：{why}")
        return cell

    allowed, why = robots.allowed(url)
    if not allowed:
        cell.update(skipped=True, reason=f"robots 禁止：{why}")
        return cell

    limiter.wait(domain_of(url))
    if backend == "curl_cffi":
        opts = {"proxy": args.proxy, "timeout": args.timeout,
                "min_text_len": args.min_text, "slow_ms": 0}
    else:
        opts = {"proxy": args.proxy, "headless": args.headless,
                "timeout_ms": args.timeout_ms, "wait_ms": args.wait_ms,
                "min_text_len": args.min_text, "slow_ms": 0,
                "challenge_wait_ms": args.challenge_wait}

    t0 = time.time()
    res = _tiers.run_backend(backend, url, opts)
    cell.update(
        ok=bool(res.get("ok")),
        status=res.get("status"),
        blocked=bool(res.get("blocked")),
        vendor=res.get("block_vendor"),
        reason=short(res.get("block_reason") or res.get("error"), 110),
        chars=len(res.get("text") or ""),
        title=short(res.get("title"), 60),
        elapsed_ms=res.get("elapsed_ms") or int((time.time() - t0) * 1000),
        soft=bool(res.get("soft_limited")),
    )
    return cell


def fmt_table(results: dict[str, list[dict]], backends: list[str]) -> str:
    lines = []
    for url, cells in results.items():
        lines.append("")
        lines.append(f"■ {url}")
        lines.append(f"  {'后端':<14}{'层级':<5}{'结果':<8}{'HTTP':<6}"
                     f"{'字符':>8}{'耗时':>9}  {'反爬/说明'}")
        lines.append("  " + "-" * 96)
        for c in cells:
            if c.get("skipped"):
                lines.append(f"  {c['backend']:<14}{'T'+str(c['tier']):<5}"
                             f"{'跳过':<8}{'-':<6}{'-':>8}{'-':>9}  {c['reason']}")
                continue
            flag = "✓成功" if c.get("ok") else ("✗被拦" if c.get("blocked") else "✗失败")
            note = c.get("vendor") or c.get("reason") or ""
            if c.get("soft") and c.get("ok") is False:
                note += " [软限制]"
            lines.append(f"  {c['backend']:<14}{'T'+str(c['tier']):<5}{flag:<8}"
                         f"{str(c.get('status')):<6}{c.get('chars', 0):>8}"
                         f"{(str(c.get('elapsed_ms'))+'ms'):>9}  {note}")
    return "\n".join(lines)


def fmt_markdown(results: dict[str, list[dict]], backends: list[str]) -> str:
    md = [f"# 跨层级抓取性能基准\n",
          f"> 时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
          f"> 后端：{', '.join(backends)}\n"]
    for url, cells in results.items():
        md.append(f"\n## {url}\n")
        md.append("| 后端 | 层级 | 结果 | HTTP | 字符数 | 耗时 | 反爬厂商 / 说明 |")
        md.append("|---|---|---|---|---:|---:|---|")
        for c in cells:
            if c.get("skipped"):
                md.append(f"| `{c['backend']}` | T{c['tier']} | 跳过 | - | - | - | {c['reason']} |")
                continue
            flag = "✅ 成功" if c.get("ok") else ("🚫 被拦" if c.get("blocked") else "❌ 失败")
            note = c.get("vendor") or c.get("reason") or ""
            if c.get("soft"):
                note += " ⚠️软限制"
            md.append(f"| `{c['backend']}` | T{c['tier']} | {flag} | {c.get('status')} | "
                      f"{c.get('chars', 0)} | {c.get('elapsed_ms')}ms | {note} |")
    return "\n".join(md)


def main() -> int:
    args = build_parser().parse_args()
    urls = collect_urls(args)
    if not urls:
        print("错误：请提供 --urls 或 --urls-file", file=sys.stderr)
        return 1
    backends = [b.strip() for b in args.backends.split(",") if b.strip()]

    limiter = DomainRateLimiter(min_interval=args.min_interval)
    robots = RobotsGate(enabled=not args.no_robots)

    print("=" * 100)
    print(f"跨层级抓取性能基准   后端: {', '.join(backends)}   URL 数: {len(urls)}   重复: {args.repeat}")
    print("=" * 100)

    results: dict[str, list[dict]] = {}
    for url in urls:
        cells: list[dict] = []
        for rep in range(args.repeat):
            for b in backends:
                tag = f"[{url[:44]}…]" if len(url) > 46 else f"[{url}]"
                print(f"  {tag} {b} (第 {rep+1} 次) …", file=sys.stderr, end="\r")
                cells.append(run_cell(b, url, args, limiter, robots))
        results[url] = cells
        print(" " * 100, file=sys.stderr, end="\r")

    print(fmt_table(results, backends))

    if args.md:
        Path(args.md).write_text(fmt_markdown(results, backends), encoding="utf-8")
        print(f"\nMarkdown 报告：{args.md}", file=sys.stderr)
    if args.json:
        Path(args.json).write_text(
            json.dumps({"generated_at": datetime.now().isoformat(),
                        "backends": backends, "results": results},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"JSON 结果：{args.json}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
