# -*- coding: utf-8 -*-
"""W0 · 微信小程序包盘点（**只读**，不修改任何文件）。

做什么
------
扫描本机微信客户端的小程序缓存目录，列出：
  * 已缓存的小程序（appid / 版本 / 主包 / 分包 / 体积 / 最后活动时间）
  * 每个包的加密状态（V1MMWX = 新版加密 / 明文 = 老版可直接解包）
  * 是否存在可静态分析的条件

为什么这是第一步
----------------
小程序抓取的四条路线（官方 SDK / MITM / 反编译 / 真机）**前提条件完全不同**。
在动手之前先弄清"本机有什么"，能避免走死路：
  * 有开发者工具 → 走 W1（官方 SDK，最合规）
  * 有已缓存的小程序包 → 走 W3（反编译，需解密）
  * 都没有 → 只能 W2（MITM 抓包，需证书）

合规
----
本脚本**只读取文件元信息与文件头**，不读正文、不解密、不写盘、不发起网络请求。
所有产出落在一个报告文件里，供你自行判断后续该怎么走。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import STATE_DIR, now_iso  # noqa: E402

# 加密包魔数（微信新版 4.x）
MP_MAGIC_ENCRYPTED = b"V1MMWX"
# 明文包首字节（老版）
MP_HEADER_PLAIN = 0xBE

# 微信小程序缓存根目录候选（覆盖 4.x / 3.x / 各盘符）
def _roots() -> list[Path]:
    home = Path.home()
    appdata = Path(os.environ.get("APPDATA") or (home / "AppData" / "Roaming"))
    out: list[Path] = []

    # 4.x：多 uid 目录
    xw = appdata / "Tencent" / "xwechat" / "radium" / "users"
    if xw.is_dir():
        for uid in sorted(xw.iterdir()):
            if (uid / "applet").is_dir():
                out.append(uid / "applet")

    # 3.x：WeChat Files\Applet
    for base in (home / "Documents", Path("D:/Documents"), Path("E:/Documents")):
        wf = base / "WeChat Files"
        if wf.is_dir():
            for uid in sorted(wf.iterdir()):
                if (uid / "Applet").is_dir():
                    out.append(uid / "Applet")

    # 兜底：老版本目录名
    for cand in (appdata / "Tencent" / "WeChat" / "Applet",):
        if cand.is_dir():
            out.append(cand)
    return out


def _read_magic(p: Path, n: int = 16) -> str:
    try:
        with p.open("rb") as f:
            return f.read(n).decode("latin-1")
    except Exception:
        return ""


def classify_pkg(p: Path) -> str:
    """识别包格式：encrypted / plain / unknown"""
    head = _read_magic(p, 6)
    if head.encode("latin-1", "ignore")[:6] == MP_MAGIC_ENCRYPTED:
        return "encrypted"
    try:
        with p.open("rb") as f:
            if f.read(1) and f.read(1)[0] == MP_HEADER_PLAIN:
                return "plain"
    except Exception:
        pass
    return "unknown"


def scan_app(app_dir: Path) -> dict:
    """扫描单个小程序（一个 appid 目录，内含多个版本）。"""
    appid = app_dir.name
    versions = []
    for vdir in sorted([d for d in app_dir.iterdir() if d.is_dir()]):
        pkgs = sorted(vdir.glob("*.wxapkg"))
        if not pkgs:
            continue
        total = sum(p.stat().st_size for p in pkgs)
        fmt = {}
        for p in pkgs:
            fmt[classify_pkg(p)] = fmt.get(classify_pkg(p), 0) + 1
        newest = max(p.stat().st_mtime for p in pkgs)
        versions.append({
            "version": vdir.name,
            "packages": [{
                "name": p.name,
                "size": p.stat().st_size,
                "format": classify_pkg(p),
                "is_main": p.name == "__APP__.wxapkg",
                "mtime": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            } for p in pkgs],
            "total_bytes": total,
            "formats": fmt,
            "newest_mtime": datetime.fromtimestamp(newest).isoformat(timespec="seconds"),
            "packages_count": len(pkgs),
        })
    if not versions:
        return {}
    all_fmt = {}
    for v in versions:
        for k, n in v["formats"].items():
            all_fmt[k] = all_fmt.get(k, 0) + n
    total_bytes = sum(v["total_bytes"] for v in versions)
    return {
        "appid": appid,
        "versions": versions,
        "latest_version": versions[-1]["version"],
        "latest_mtime": max(v["newest_mtime"] for v in versions),
        "packages_total": sum(v["packages_count"] for v in versions),
        "bytes_total": total_bytes,
        "formats": all_fmt,
        "encrypted": all_fmt.get("encrypted", 0) > 0,
    }


def scan(roots: list[Path]) -> dict:
    apps: dict[str, dict] = {}
    for root in roots:
        pkg_dir = root / "packages"
        if not pkg_dir.is_dir():
            continue
        for app_dir in sorted([d for d in pkg_dir.iterdir() if d.is_dir()]):
            info = scan_app(app_dir)
            if not info:
                continue
            info["root"] = str(root)
            # 同一 appid 出现在多个 uid 时，取最新的
            old = apps.get(info["appid"])
            if not old or info["latest_mtime"] > old["latest_mtime"]:
                apps[info["appid"]] = info
    return apps


def has_aux_dirs(root: Path) -> dict:
    """顺带报告辅助目录（有则说明该 app 有本地数据/图标/缓存）。"""
    out = {}
    for d in ("icon", "codecache", "local", "publicLib"):
        sub = root / d
        if sub.is_dir():
            out[d] = len([x for x in sub.iterdir()]) if sub.is_dir() else 0
    return out


# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #
def human(n: int) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{u}" if u == "B" else f"{n:.1f}{u}"
        n /= 1024.0
    return f"{n:.1f}TB"


def print_report(roots: list[Path], apps: dict, aux: dict) -> None:
    print("=" * 78)
    print("W0 · 微信小程序包盘点（只读）")
    print("=" * 78)
    if not roots:
        print("\n未找到微信小程序缓存目录。可能原因：")
        print("  · 本机未安装微信 PC 客户端")
        print("  · 微信版本较新，缓存路径与已知路径不同")
        print("  · 从未在 PC 微信上打开过任何小程序")
        print("\n→ 前置条件不满足，W3（反编译）路线不可用。")
        print("  请改走 W1（官方 SDK，需微信开发者工具）或 W2（MITM 抓包）。")
        return

    print(f"\n缓存根目录（{len(roots)} 个）：")
    for r in roots:
        n = len(list((r / 'packages').iterdir())) if (r / "packages").is_dir() else 0
        print(f"  · {r}  ({n} 个小程序)")

    enc = sum(1 for a in apps.values() if a["encrypted"])
    total = sum(a["bytes_total"] for a in apps.values())
    print(f"\n已缓存小程序：{len(apps)} 个 ｜ 加密包：{enc} 个 ｜ 总体积：{human(total)}")

    print("\n%-22s %-8s %-6s %-10s %-10s %s" % (
        "appid", "版本", "包数", "体积", "格式", "最后活动"))
    print("-" * 78)
    for appid, a in sorted(apps.items(), key=lambda kv: kv[1]["latest_mtime"], reverse=True):
        fmt = ",".join(f"{k}×{v}" for k, v in a["formats"].items())
        print("%-22s %-8s %-6d %-10s %-10s %s" % (
            appid, a["latest_version"], a["packages_total"],
            human(a["bytes_total"]), fmt, a["latest_mtime"][:16]))

    # 结论与路线判定
    print("\n" + "=" * 78)
    print("路线可行性判定")
    print("=" * 78)
    if apps:
        if enc == len(apps):
            print("✗ W3 反编译：包**全部加密**（V1MMWX），不能直接解包。")
            print("  → 需先解密。解密依赖外部工具（见 references/miniprogram.md），")
            print("    且属逆向范畴，请自行确认合规边界。")
        elif enc == 0:
            print("✓ W3 反编译：包为**明文**格式，可直接用 wxappUnpacker 类工具解包分析。")
        else:
            print(f"◐ W3 反编译：{enc} 个加密 / {len(apps)-enc} 个明文，按 appid 分别处理。")
    devtools = _devtools_present()
    print(("✓ " if devtools else "✗ ") + "W1 官方 SDK：" +
          ("已检测到微信开发者工具" if devtools else
           "未检测到微信开发者工具 → 需先安装"))
    print("· W2 MITM 抓包：与上述条件无关，只要有微信客户端 + 抓包能力即可（见 mp_capture.py）")


def _devtools_present() -> bool:
    cands = [
        Path("C:/Program Files (x86)/Tencent/微信web开发者工具"),
        Path("C:/Program Files/Tencent/微信web开发者工具"),
        Path("D:/Program Files (x86)/Tencent/微信web开发者工具"),
        Path("D:/Program Files/Tencent/微信web开发者工具"),
    ]
    return any(c.is_dir() for c in cands)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="W0 · 微信小程序包盘点（只读，不修改任何文件）")
    ap.add_argument("--json", default=None, help="额外导出 JSON 报告到该路径")
    ap.add_argument("--md", default=None, help="额外导出 Markdown 报告到该路径")
    ap.add_argument("--list-roots", action="store_true", help="只列出探测到的缓存根目录")
    args = ap.parse_args()

    roots = _roots()
    if args.list_roots:
        for r in roots:
            print(r)
        return 0 if roots else 1

    apps = scan(roots)
    aux = {str(r): has_aux_dirs(r) for r in roots}
    print_report(roots, apps, aux)

    payload = {
        "generated_at": now_iso(),
        "roots": [str(r) for r in roots],
        "aux_dirs": aux,
        "app_count": len(apps),
        "apps": apps,
        "devtools_present": _devtools_present(),
    }
    if args.json:
        Path(args.json).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n→ JSON: {args.json}")
    if args.md:
        lines = ["# 微信小程序包盘点（W0）", "",
                 f"- 生成时间：{payload['generated_at']}",
                 f"- 缓存根目录：{len(roots)} 个",
                 f"- 已缓存小程序：**{len(apps)}** 个",
                 f"- 微信开发者工具：{'已安装' if payload['devtools_present'] else '未安装'}",
                 "", "| appid | 版本 | 包数 | 体积 | 格式 | 最后活动 |",
                 "|---|---|---|---|---|---|"]
        for appid, a in sorted(apps.items(), key=lambda kv: kv[1]["latest_mtime"], reverse=True):
            fmt = ", ".join(f"{k}×{v}" for k, v in a["formats"].items())
            lines.append(f"| `{appid}` | {a['latest_version']} | {a['packages_total']} "
                         f"| {human(a['bytes_total'])} | {fmt} | {a['latest_mtime'][:16]} |")
        Path(args.md).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"→ MD  : {args.md}")
    return 0 if apps else 1


if __name__ == "__main__":
    raise SystemExit(main())
