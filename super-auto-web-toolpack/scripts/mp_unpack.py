# -*- coding: utf-8 -*-
"""W3 · 小程序反编译适配与静态提取。

⚠️ 合规提示（务必先读）
----------------------
本模块**不自己实现解密与反编译**——那属于逆向工程范畴。
它做的是两件事：
  1. **适配层**：检测并指导使用社区开源工具（解密 → 解包）
  2. **静态提取**：对**已解包**的源码做接口挖掘（这部分是纯文本分析，无风险）

前提：小程序包需先解密。**新版微信（4.x）的包是 V1MMWX 加密格式**，
老版解包工具对它无效，需先用专门的解密工具处理。

使用边界：
  · 仅用于**你自己有权访问**的小程序、且用于学习研究
  · 不要用于获取他人隐私数据、不要用于绕过付费/权限
  · 解密与反编译可能违反目标平台服务条款，后果自负

子命令
------
  tools    检测本机可用的解包/解密工具
  guide    打印完整工具链指引（含法律风险提示）
  scan     对已解包的小程序源码做静态提取（API 端点 / 域名 / 调用点）
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import short  # noqa: E402

# 社区工具登记表（经 GitHub 核实的真实项目）
TOOLS = [
    {
        "name": "wxapkg (wux1an)",
        "slug": "wux1an/wxapkg",
        "stars": 4245,
        "lang": "Python/Rust",
        "pushed": "2026-04-28",
        "what": "跨平台 GUI，扫描 + 解密 + 解包一条龙",
        "bins": ["wxapkg"],
        "install": "从 GitHub Releases 下载对应平台的可执行文件",
        "note": "支持新版 V1MMWX 加密，最省事",
    },
    {
        "name": "KillWxapkg (Ackites)",
        "slug": "Ackites/KillWxapkg",
        "stars": 6011,
        "lang": "Go",
        "pushed": "2024-09-20",
        "what": "自动化反编译，自动解密 + 解包 + 还原工程目录，支持 Hook",
        "bins": ["KillWxapkg", "killwxapkg"],
        "install": "GitHub Releases 下载二进制（Go 单文件）",
        "note": "星数最高，功能最全，支持还原目录结构",
    },
    {
        "name": "wxappUnpacker (xdmjun)",
        "slug": "xdmjun/wxappUnpacker",
        "stars": 2426,
        "lang": "JavaScript",
        "pushed": "2023-04-08",
        "what": "经典解包器（只解包，不解密）",
        "bins": [],
        "install": "git clone && npm i",
        "note": "⚠ 2023 年后停更，仅支持老版明文包；对 V1MMWX 无效",
    },
    {
        "name": "pc_wxapkg_decrypt (BlackTrace)",
        "slug": "BlackTrace/pc_wxapkg_decrypt",
        "stars": 752,
        "lang": "Go",
        "pushed": "2020-08-06",
        "what": "PC 端 wxapkg 解密（只解密，不解包）",
        "bins": ["pc_wxapkg_decrypt"],
        "install": "GitHub Releases",
        "note": "⚠ 2020 年，可能不支持 V1MMWX",
    },
    {
        "name": "pc_wxapkg_decrypt_python (superdashu)",
        "slug": "superdashu/pc_wxapkg_decrypt_python",
        "stars": 208,
        "lang": "Python",
        "pushed": "2020-08-13",
        "what": "同上的 Python 版",
        "bins": [],
        "install": "pip install -r requirements.txt",
        "note": "⚠ 2020 年，可能不支持 V1MMWX",
    },
]


def cmd_tools(args) -> int:
    print("=" * 92)
    print("W3 · 小程序反编译工具检测")
    print("=" * 92)
    print("%-30s %-8s %-10s %-10s %s" % ("工具", "★", "语言", "状态", "检测到的命令"))
    print("-" * 92)
    avail = []
    for t in TOOLS:
        found = next((shutil.which(b) for b in t["bins"] if shutil.which(b)), None)
        status = "✓ 可用" if found else "— 未安装"
        if found:
            avail.append((t, found))
        print("%-30s %-8s %-10s %-10s %s" % (
            t["name"], t["stars"], t["lang"], status, found or ""))
    print()
    if avail:
        print("已可用：")
        for t, b in avail:
            print(f"  · {t['name']} → {b}    （{t['what']}）")
    else:
        print("结论：未检测到任何解包/解密工具。")
        print("      推荐先装 `KillWxapkg`（★6011，Go 单文件）或 `wuxapkg`（★4245，GUI）。")
    print()
    print("注意：工具检测只查 PATH。若已下载为本地文件，把所在目录加入 PATH 或直接手动运行。")
    return 0 if avail else 3


def cmd_guide(args) -> int:
    print("""W3 · 小程序反编译工具链指引
================================================================

⚠️ 先读这段
  本路线涉及**解密 + 反编译**，属逆向工程范畴。
  · 仅用于你自己有权访问的小程序，且用于学习研究
  · 解密/反编译可能违反目标平台服务条款
  · 不要用于获取他人数据、绕过付费或权限

【为什么需要工具而不是本 skill 自己做】
  新版微信（4.x）的小程序包是 **V1MMWX 加密格式**：
      文件头 = "V1MMWX" + 加密载荷
  解密需要微信进程内存中的密钥或特定算法，属逆向。本 skill 不实现这部分，
  只做适配（帮你找到并调用社区工具），保持边界清晰。

【三步走】

  ① 定位包（本 skill 已能自动做）
     python mp_discover.py
     → 列出本机缓存的小程序 + 每个包的加密状态

  ② 解密 + 解包（用社区工具）
     推荐 KillWxapkg（Go 单文件，★6011）：
         KillWxapkg -id <appid>          # 直接按 appid 从微信缓存解包
         KillWxapkg -in <path.wxapkg>    # 或指定文件
     或 wxapkg（GUI，★4245）：
         图形界面选目录，一键扫描 + 解密 + 解包

     路径参考（微信 4.x）：
         %APPDATA%\\Tencent\\xwechat\\radium\\users\\<uid>\\applet\\packages\\<appid>\\<ver>\\

  ③ 静态提取（本 skill 做这部分，无风险）
     python mp_unpack.py scan --dir <解包输出目录>
     → 提取 API 端点、域名、wx.request 调用点、疑似签名参数

【产出物长什么样】
  解包成功后会得到 app.json / app-service.js / pages/... 等源码结构。
  `scan` 直接对这些文件做文本挖掘，不执行任何代码。

【与 W2 的关系】
  W3 给你「接口长什么样、参数怎么算」；
  W2 给你「真实响应数据长什么样」。
  两者互补：先用 W3 搞懂签名参数，再用 W2 验证。
""")
    return 0


# --------------------------------------------------------------------------- #
# 静态提取（纯文本分析，不执行代码）
# --------------------------------------------------------------------------- #
RE_URL = re.compile(r"""["'`]((?:https?:)?//[A-Za-z0-9._\-]+(?::\d+)?[^"'`\s]*)["'`]""")
RE_PATH = re.compile(r"""["'`](/[A-Za-z0-9._\-/{}:]{3,120})["'`]""")
RE_HOST = re.compile(r"""(?:https?:)?//([A-Za-z0-9._\-]+\.[A-Za-z]{2,})""")
RE_REQUEST = re.compile(
    r"""wx\.(request|uploadFile|downloadFile|connectSocket)\s*\(\s*\{?""")
RE_URLFIELD = re.compile(r"""url\s*:\s*([^,\n}]{1,200})""")
RE_BASE = re.compile(
    r"""(?:baseUrl|BASE_URL|baseURL|apiBase|API_BASE|host|domain)\s*[:=]\s*["'`]([^"'`]{4,200})["'`]""",
    re.I)
# 签名线索 1：对象字面量里与加密函数同现的字段名
RE_SIGN = re.compile(
    r"""["']([A-Za-z_][A-Za-z0-9_]{0,30})["']\s*:\s*[^,\n}]{0,120}\b"""
    r"""(?:md5|sha1|sha256|hmac|sign|encrypt|crypto|aes|des)\b""", re.I)
# 签名线索 2：加密函数调用（md5(...) / hmac( / CryptoJS.AES.encrypt( ...）
RE_CRYPTO_CALL = re.compile(
    r"""\b(md5|sha1|sha256|hmac|CryptoJS(?:\.[A-Za-z0-9_]+)*|createHmac|createCipher\w*)\s*\(""",
    re.I)
# 签名线索 3：密钥/盐常量
RE_SECRET_CONST = re.compile(
    r"""\b(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]{0,40})\s*=\s*["'`]([^"'`]{6,120})["'`]"""
    r"""(?=[^\n]*\n?[^\n]*)""")
RE_SECRET_NAME = re.compile(r"(key|secret|salt|iv|token|appid|appsecret)", re.I)
RE_QUOTES = re.compile(r"""^[\s"'`]+|[\s"'`]+$""")

SKIP_DIRS = {"node_modules", ".git", "dist", "build", "__pycache__"}


def _iter_source(root: Path):
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.suffix.lower() in (".js", ".json", ".wxml", ".wxss", ".ts", ".wxs"):
            yield p


def _clean(s: str) -> str:
    return RE_QUOTES.sub("", str(s)).strip()


def scan_source(root: Path) -> dict:
    hosts = Counter()
    endpoints = Counter()
    requests = Counter()
    bases = Counter()
    sign_hints = Counter()
    secrets = Counter()
    crypto_calls = Counter()
    files_scanned = 0
    file_hits = 0

    for p in _iter_source(root):
        files_scanned += 1
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        hit = False
        for m in RE_HOST.finditer(txt):
            h = m.group(1).lower()
            if h.endswith((".js", ".css", ".png", ".jpg", ".json")):
                continue
            hosts[h] += 1
            hit = True
        for m in RE_URL.finditer(txt):
            endpoints[_clean(m.group(1))] += 1
            hit = True
        for m in RE_PATH.finditer(txt):
            endpoints[_clean(m.group(1))] += 1
        # wx.request 调用点
        if RE_REQUEST.search(txt):
            for m in RE_REQUEST.finditer(txt):
                requests[m.group(1)] += 1
            hit = True
            for m in RE_URLFIELD.finditer(txt):
                endpoints[_clean(m.group(1))] += 1
        for m in RE_BASE.finditer(txt):
            bases[_clean(m.group(1))] += 1
        # 签名线索
        for m in RE_SIGN.finditer(txt):
            sign_hints[m.group(1)] += 1
        for m in RE_CRYPTO_CALL.finditer(txt):
            crypto_calls[m.group(1)] += 1
        for m in RE_SECRET_CONST.finditer(txt):
            nm, val = m.group(1), m.group(2)
            if RE_SECRET_NAME.search(nm):
                secrets[f"{nm} = {short(val, 40)}"] += 1
        if sign_hints or crypto_calls or secrets:
            hit = True
        if hit:
            file_hits += 1

    return {
        "root": str(root),
        "files_scanned": files_scanned,
        "files_with_hits": file_hits,
        "hosts": hosts.most_common(60),
        "endpoints": endpoints.most_common(120),
        "wx_calls": dict(requests),
        "base_urls": bases.most_common(20),
        "sign_hints": sign_hints.most_common(30),
        "crypto_calls": crypto_calls.most_common(20),
        "secret_consts": secrets.most_common(20),
    }


def render_scan(r: dict) -> str:
    L = [f"# 小程序源码静态提取（W3）", "",
         f"- 目录：`{r['root']}`",
         f"- 扫描文件：**{r['files_scanned']}** ｜ 有命中：**{r['files_with_hits']}**", ""]
    L += ["## 域名", ""]
    if r["hosts"]:
        L += ["| 域名 | 出现次数 |", "|---|---|"]
        L += [f"| `{h}` | {n} |" for h, n in r["hosts"][:40]]
    else:
        L.append("（未发现域名）")
    L += ["", "## wx.* 调用统计", ""]
    if r["wx_calls"]:
        L += [f"- `wx.{k}` × {v}" for k, v in r["wx_calls"].items()]
    else:
        L.append("（未发现 wx.request / uploadFile / downloadFile / connectSocket）")
    L += ["", "## 接口路径 / URL 候选", ""]
    if r["endpoints"]:
        L += ["| 候选 | 次数 |", "|---|---|"]
        L += [f"| `{short(e, 90)}` | {n} |" for e, n in r["endpoints"][:60]]
    else:
        L.append("（无）")
    if r["base_urls"]:
        L += ["", "## 疑似 baseURL 常量", ""]
        L += [f"- `{u}` （{n} 次）" for u, n in r["base_urls"]]
    if r["crypto_calls"]:
        L += ["", "## 加密函数调用", ""]
        L += [f"- `{k}(...)` × {n}" for k, n in r["crypto_calls"]]
    if r["secret_consts"]:
        L += ["", "## 疑似密钥/盐 常量", ""]
        L += [f"- `{k}`" for k, n in r["secret_consts"]]
    if r["sign_hints"]:
        L += ["", "## 疑似签名/加密相关字段", ""]
        L += [f"- `{k}` （{n} 次）" for k, n in r["sign_hints"]]
    L += ["", "> 以上均为**静态文本挖掘**结果，是候选而非确证。",
          "> 签名参数需要结合 W2 抓包的真实样本交叉验证。"]
    return "\n".join(L) + "\n"


def cmd_scan(args) -> int:
    root = Path(args.dir)
    if not root.is_dir():
        print(f"目录不存在：{root}", file=sys.stderr)
        return 2
    r = scan_source(root)
    md = render_scan(r)
    print(md)
    if args.md:
        Path(args.md).write_text(md, encoding="utf-8")
        print(f"→ {args.md}")
    if args.json:
        Path(args.json).write_text(json.dumps(r, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        print(f"→ {args.json}")
    return 0 if r["files_with_hits"] else 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description="W3 · 小程序反编译适配与静态提取")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("tools", help="检测本机解包/解密工具")
    p1.set_defaults(fn=cmd_tools)

    p2 = sub.add_parser("guide", help="打印工具链指引与合规提示")
    p2.set_defaults(fn=cmd_guide)

    p3 = sub.add_parser("scan", help="对已解包的源码做静态提取")
    p3.add_argument("--dir", required=True, help="解包输出目录")
    p3.add_argument("--md", default=None)
    p3.add_argument("--json", default=None)
    p3.set_defaults(fn=cmd_scan)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
