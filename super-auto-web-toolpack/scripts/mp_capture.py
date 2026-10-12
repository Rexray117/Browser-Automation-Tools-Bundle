# -*- coding: utf-8 -*-
"""W2 · 微信小程序 API 抓包（MITM）。

定位
----
不依赖微信开发者工具（无需装）、不依赖解密小程序包。
原理：小程序的所有 `wx.request` 最终都走 HTTPS，用中间人代理即可看到**真实 API 调用**。

这是四条路线里**前提条件最少**的一条——只要有微信客户端 + 能配代理/证书。

三条子命令
----------
  init    生成 mitmproxy addon 与配置（不改动系统设置）
  run     启动 mitmdump（前台，Ctrl-C 结束）
  report  解析抓取产物，汇总 API 清单（host / path / 方法 / 参数结构 / 疑似签名参数）

小程序的识别特征
----------------
微信小程序的请求带 `Referer: https://servicewechat.com/<appid>/<version>/page-frame.html`，
这是**最可靠的过滤依据**——比按域名黑名单准得多（小程序可请求任意自有域名）。

合规与安全
----------
* 只抓**你自己账号**在小程序里的流量；不注入、不改包、不重放。
* **凭据脱敏**：addon 落盘前会把 `Cookie / Authorization / token / sign` 等
  敏感头的**值**替换为 `<redacted>`，只保留字段名与长度——
  既能用于分析签名参数结构，又不会把凭据留成明文文件。
* 只记录 host/path/方法/参数名，**默认不落请求体正文**（`--keep-body` 显式开启才落）。
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import STATE_DIR, now_iso, short  # noqa: E402

CAPTURE_DIR = STATE_DIR / "mp_capture"

# --------------------------------------------------------------------------- #
# mitmproxy addon 模板
# --------------------------------------------------------------------------- #
ADDON_TEMPLATE = r'''# -*- coding: utf-8 -*-
"""由 super-auto-web-toolpack 生成的 mitmproxy addon。

筛选规则：只保留 Referer 指向 servicewechat.com（微信小程序容器）的请求。
输出：脱敏后的 API 记录（JSONL），供 `mp_capture.py report` 分析。
"""
import json
import os
import re
import time
from urllib.parse import urlparse, parse_qs

OUT = os.environ.get("MP_CAP_OUT", "api.jsonl")
KEEP_BODY = os.environ.get("MP_CAP_KEEP_BODY") == "1"
APPID_FILTER = os.environ.get("MP_CAP_APPID") or None   # 只抓某个小程序

# 需要脱敏的敏感字段（保留字段名与长度，值替换掉）
SENSITIVE = re.compile(
    r"(cookie|authorization|token|access[-_]?token|refresh[-_]?token|"
    r"session|sign|signature|secret|password|passwd|pwd|apikey|api[-_]?key|"
    r"ticket|openid|unionid|encrypt|nonce|timestamp|ts|salt)",
    re.I)

# 静态资源后缀（不记录）
STATIC_EXT = re.compile(
    r"\.(js|css|png|jpe?g|gif|webp|svg|woff2?|ttf|otf|ico|map|mp4|m3u8|wasm)$", re.I)


def redact_headers(hdrs):
    out = {}
    for k, v in hdrs.items():
        if SENSITIVE.search(k):
            out[k] = f"<redacted:{len(str(v))}>"
        else:
            out[k] = v
    return out


def redact_params(qs):
    out = {}
    for k, vals in qs.items():
        v = vals[0] if vals else ""
        if SENSITIVE.search(k):
            out[k] = f"<redacted:{len(str(v))}>"
        else:
            out[k] = v[:200]
    return out


def appid_from_referer(ref):
    m = re.search(r"servicewechat\.com/(wx[0-9a-f]{16})/(\d+)", ref or "")
    return (m.group(1), m.group(2)) if m else (None, None)


class MpCapture:
    def __init__(self):
        self.n_seen = 0
        self.n_kept = 0

    def response(self, flow):
        try:
            self._handle(flow)
        except Exception as e:
            print("[mp_capture] 处理异常:", e)

    def _handle(self, flow):
        self.n_seen += 1
        req = flow.request
        url = req.pretty_url
        parsed = urlparse(url)
        if STATIC_EXT.search(parsed.path):
            return
        ref = req.headers.get("referer", "") or req.headers.get("Referer", "")
        appid, ver = appid_from_referer(ref)
        # 只保留小程序发起的请求
        if not appid:
            return
        if APPID_FILTER and appid != APPID_FILTER:
            return
        self.n_kept += 1

        rec = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "appid": appid,
            "app_version": ver,
            "method": req.method,
            "scheme": parsed.scheme,
            "host": parsed.hostname,
            "path": parsed.path,
            "status": flow.response.status_code if flow.response else None,
            "ctype": (flow.response.headers.get("content-type", "")
                      if flow.response else ""),
            "req_size": len(req.raw_content or b""),
            "resp_size": (len(flow.response.raw_content or b"")
                          if flow.response else 0),
            "query_params": redact_params(parse_qs(parsed.query)),
            "req_headers": redact_headers(dict(req.headers)),
            "resp_headers": (redact_headers(dict(flow.response.headers))
                             if flow.response else {}),
            "signed": bool(re.search(
                r"(sign|signature|token|nonce|secret)",
                parsed.query + json.dumps(dict(req.headers)), re.I)),
        }
        if KEEP_BODY:
            body = req.get_text(strict=False) or ""
            rec["req_body_head"] = body[:2000]
        with open(OUT, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


addons = [MpCapture()]
'''


# --------------------------------------------------------------------------- #
# 核心分析逻辑（纯函数，便于单测）
# --------------------------------------------------------------------------- #
SIGN_HINT = ("sign", "signature", "token", "nonce", "secret", "salt", "encrypt",
             "ticket", "auth", "timestamp", "ts")


def aggregate(records: list[dict]) -> dict:
    """把抓到的记录汇总成 API 清单。

    防御性过滤：只统计带 appid 的记录。addon 已按 Referer 过滤，
    但产物可能被追加、被手工编辑，这里再挡一层——否则非小程序流量会污染清单。
    """
    eps: dict[str, dict] = {}
    apps: dict[str, dict] = defaultdict(lambda: {"count": 0, "hosts": Counter()})
    skipped = 0
    kept_records = [r for r in records if r.get("appid")]
    skipped = len(records) - len(kept_records)
    for r in kept_records:
        key = f"{r.get('method')} {r.get('host')}{r.get('path')}"
        e = eps.setdefault(key, {
            "method": r.get("method"), "host": r.get("host"), "path": r.get("path"),
            "count": 0, "statuses": Counter(), "params": set(),
            "sign_params": set(), "ctype": r.get("ctype") or "",
            "resp_bytes": 0, "appids": set(),
        })
        e["count"] += 1
        e["statuses"][r.get("status")] += 1
        e["resp_bytes"] += int(r.get("resp_size") or 0)
        e["ctype"] = e["ctype"] or (r.get("ctype") or "")
        if r.get("appid"):
            e["appids"].add(r["appid"])
        for k, v in (r.get("query_params") or {}).items():
            e["params"].add(k)
            if any(h in k.lower() for h in SIGN_HINT):
                e["sign_params"].add(k)
        for k in (r.get("req_headers") or {}):
            if any(h in k.lower() for h in SIGN_HINT):
                e["sign_params"].add(f"hdr:{k}")
        if r.get("appid"):
            apps[r["appid"]]["count"] += 1
            apps[r["appid"]]["hosts"][r.get("host")] += 1
    # 序列化
    out_eps = []
    for k, e in sorted(eps.items(), key=lambda kv: -kv[1]["count"]):
        out_eps.append({
            "endpoint": k, "method": e["method"], "host": e["host"],
            "path": e["path"], "count": e["count"],
            "statuses": dict(e["statuses"]), "params": sorted(e["params"]),
            "sign_params": sorted(e["sign_params"]), "ctype": e["ctype"],
            "resp_bytes": e["resp_bytes"], "appids": sorted(e["appids"]),
        })
    return {
        "endpoint_count": len(out_eps),
        "record_count": len(kept_records),
        "skipped_non_mp": skipped,
        "endpoints": out_eps,
        "apps": {a: {"count": v["count"],
                     "hosts": v["hosts"].most_common(10)} for a, v in apps.items()},
    }


def render_report(agg: dict) -> str:
    L = ["# 微信小程序 API 抓包汇总（W2）", ""]
    L.append(f"- 记录数：**{agg['record_count']}** ｜ 去重端点：**{agg['endpoint_count']}**")
    if agg.get("skipped_non_mp"):
        L.append(f"- 已过滤非小程序流量：{agg['skipped_non_mp']} 条（无 appid）")
    L.append("")
    L.append("## 按小程序")
    L.append("")
    L.append("| appid | 请求数 | 主要域名 |")
    L.append("|---|---|---|")
    for a, v in sorted(agg["apps"].items(), key=lambda kv: -kv[1]["count"]):
        hosts = ", ".join(f"{h}({n})" for h, n in v["hosts"][:3])
        L.append(f"| `{a}` | {v['count']} | {hosts} |")
    L.append("")
    L.append("## API 端点清单")
    L.append("")
    L.append("| 方法 | 域名 | 路径 | 次数 | 状态 | 参数 | 疑似签名参数 |")
    L.append("|---|---|---|---|---|---|---|")
    for e in agg["endpoints"]:
        st = ",".join(f"{k}×{v}" for k, v in e["statuses"].items())
        pm = ", ".join(e["params"][:8]) or "—"
        sg = ", ".join(e["sign_params"][:6]) or "—"
        L.append(f"| {e['method']} | `{e['host']}` | `{short(e['path'], 60)}` "
                 f"| {e['count']} | {st} | {pm} | {sg} |")
    L.append("")
    L.append("> 疑似签名参数 = 名称中含 sign/token/nonce/timestamp 等——")
    L.append("> 这些参数通常由小程序端生成，是复现请求的主要障碍。")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- #
# 子命令
# --------------------------------------------------------------------------- #
def cmd_init(args) -> int:
    d = Path(args.outdir) if args.outdir else (CAPTURE_DIR / datetime.now().strftime("%Y%m%d_%H%M%S"))
    d.mkdir(parents=True, exist_ok=True)
    addon = d / "addon.py"
    addon.write_text(ADDON_TEMPLATE, encoding="utf-8")
    (d / "api.jsonl").touch()
    info = {
        "dir": str(d), "addon": str(addon),
        "api_jsonl": str(d / "api.jsonl"),
        "created_at": now_iso(),
    }
    (d / "capture.json").write_text(json.dumps(info, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    print(f"已生成抓包工作区：{d}")
    print(f"  addon : {addon}")
    print(f"  产物  : {d / 'api.jsonl'}")
    print()
    print("下一步：")
    print(f"  1) 安装依赖： pip install mitmproxy")
    print(f"  2) 启动代理： python mp_capture.py run --dir \"{d}\"")
    print( "  3) 让微信走代理（任选其一）：")
    print( "       · 系统代理指向 127.0.0.1:8080")
    print( "       · 小程序可能绕过系统代理 → 用 Proxifier 强制劫持 Weixin.exe 进程")
    print( "  4) 安装 mitmproxy CA 证书到「受信任的根证书颁发机构」")
    print( "  5) 在微信里操作目标小程序，然后：")
    print(f"       python mp_capture.py report --dir \"{d}\"")
    return 0


def _mitmdump_cmd() -> list[str] | None:
    """定位 mitmdump 可执行文件。

    注意：mitmproxy 10+ 移除了 `python -m mitmdump` 的调用方式，
    只剩 console_scripts 入口（mitmdump → mitmproxy.tools.main:mitmdump）。
    所以这里必须找可执行文件，而不是用 `-m`。
    """
    exe = shutil.which("mitmdump")
    if exe:
        return [exe]
    # 回退：当前解释器同目录的 Scripts/
    scripts = Path(sys.executable).parent
    for cand in ("mitmdump.exe", "mitmdump"):
        p = scripts / cand
        if p.exists():
            return [str(p)]
    return None


def cmd_run(args) -> int:
    d = Path(args.dir)
    addon = d / "addon.py"
    if not addon.exists():
        print(f"未找到 addon：{addon}\n请先运行： python mp_capture.py init", file=sys.stderr)
        return 3
    base = _mitmdump_cmd()
    if not base:
        print("未找到 mitmdump 可执行文件。请先： pip install mitmproxy", file=sys.stderr)
        print("（注意：mitmproxy 10+ 不再支持 `python -m mitmdump`，"
              "请确认 Scripts/ 目录在 PATH 中或已安装 mitmproxy）", file=sys.stderr)
        return 3
    cmd = base + ["-s", str(addon), "--listen-port", str(args.port)]
    if args.proxy_upstream:
        cmd += ["--mode", f"upstream:{args.proxy_upstream}"]
    print(f"执行：{' '.join(cmd)}")
    print(f"工作目录：{d}")
    print("（Ctrl-C 结束）")
    try:
        subprocess.run(cmd, cwd=str(d), check=False)
    except KeyboardInterrupt:
        print("\n已停止。")
    return 0


def cmd_report(args) -> int:
    d = Path(args.dir)
    src = Path(args.input) if args.input else (d / "api.jsonl")
    if not src.exists():
        print(f"未找到抓包产物：{src}", file=sys.stderr)
        return 2
    recs = []
    bad = 0
    for line in src.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            recs.append(json.loads(line))
        except Exception:
            bad += 1
    agg = aggregate(recs)
    md = render_report(agg)
    print(md)
    if bad:
        print(f"（跳过 {bad} 行无法解析的记录）", file=sys.stderr)
    if args.md:
        Path(args.md).write_text(md, encoding="utf-8")
        print(f"→ {args.md}")
    if args.json:
        Path(args.json).write_text(
            json.dumps(agg, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"→ {args.json}")
    return 0 if recs else 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description="W2 · 微信小程序 API 抓包（MITM）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("init", help="生成抓包工作区（addon + 目录）")
    p1.add_argument("--outdir", default=None, help="工作区路径（默认自动按时间戳建）")
    p1.set_defaults(fn=cmd_init)

    p2 = sub.add_parser("run", help="启动 mitmdump（挂在 addon 上）")
    p2.add_argument("--dir", required=True, help="init 生成的工作区")
    p2.add_argument("--port", type=int, default=8080)
    p2.add_argument("--proxy-upstream", default=None,
                    help="上游代理（若本机已有一个代理，如 http://127.0.0.1:7897）")
    p2.set_defaults(fn=cmd_run)

    p3 = sub.add_parser("report", help="汇总 API 清单")
    p3.add_argument("--dir", required=True, help="init 生成的工作区")
    p3.add_argument("--input", default=None, help="自定义 api.jsonl 路径")
    p3.add_argument("--md", default=None)
    p3.add_argument("--json", default=None)
    p3.set_defaults(fn=cmd_report)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
