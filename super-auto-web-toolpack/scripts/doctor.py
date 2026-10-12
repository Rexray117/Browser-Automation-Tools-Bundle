#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""环境体检：逐项报告分层抓取所需依赖，并给出编号修复建议。

    python doctor.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import STATE_DIR, PROFILE_FILE, LatencyBaseline, load_profiles  # noqa: E402
import tiers as _tiers  # noqa: E402

PY = sys.executable
OK, WARN, BAD = "✓", "!", "✗"
fixes: list[str] = []


def line(flag: str, label: str, detail: str = "") -> None:
    print(f"  [{flag}] {label:<26} {detail}")


def check_python() -> None:
    v = sys.version_info
    flag = OK if v >= (3, 10) else BAD
    line(flag, "Python", f"{v.major}.{v.minor}.{v.micro}  ({PY})")
    if flag == BAD:
        fixes.append("升级 Python 到 3.10+（curl_cffi / patchright 要求）")


def check_backends() -> None:
    print("\n分级后端：")
    for b in _tiers.FETCHERS:
        ok, why = _tiers.backend_available(b)
        line(OK if ok else WARN, b, why)
    if not _tiers.backend_available("curl_cffi")[0]:
        fixes.append(f"{PY} -m pip install curl_cffi")
    if not _tiers.backend_available("patchright")[0]:
        fixes.append(f"{PY} -m pip install patchright && {PY} -m patchright install chromium")


def check_browsers() -> None:
    print("\n浏览器二进制：")
    chrome = _tiers.chrome_present()
    line(OK if chrome else WARN, "系统 Chrome",
         "已检测到（T1 可用 channel=chrome，反检测更佳）" if chrome
         else "未检测到，T1 将回退到 Chromium")

    try:
        from patchright._impl._driver import compute_driver_executable  # noqa: F401
        line(OK, "patchright 驱动", "已安装")
    except Exception:
        line(WARN, "patchright 驱动", "未能确认；若 T1 报 Executable doesn't exist 见下方修复")

    # 探测 patchright 的 Chromium 是否已下载
    try:
        from patchright.sync_api import sync_playwright
        with sync_playwright() as p:
            exe = p.chromium.executable_path
        exists = Path(exe).exists()
        line(OK if exists else BAD, "patchright Chromium",
             f"{'已就绪' if exists else '缺失'}  {exe}")
        if not exists:
            fixes.append(f"{PY} -m patchright install chromium   # 下载 Chromium（约 150MB）")
    except Exception as e:
        line(WARN, "patchright Chromium", f"探测失败：{type(e).__name__}")

    for mod, label, cmd in (
        ("cloakbrowser", "cloakbrowser", "pip install cloakbrowser（首次运行自动下载二进制）"),
        ("camoufox", "camoufox", "pip install camoufox && python -m camoufox fetch"),
    ):
        try:
            __import__(mod)
            line(OK, label, "Python 包已安装")
        except Exception:
            line(WARN, label, f"未安装 —— 如需 T2：{cmd}")

    if not chrome:
        fixes.append("安装 Google Chrome —— T1 默认有头 + channel=chrome，可显著提升反检测表现")


def check_t2_binaries() -> None:
    """检查 T2 强指纹浏览器的**二进制**是否就绪。

    这是最容易踩空的一步：pip 包装上了 ≠ 能跑。
    cloakbrowser 与 camoufox 都需要额外下载各自改造过的浏览器（几百 MB），
    且下载源通常是 GitHub Release 资产 CDN，在受限网络下会被拦截。
    """
    print("\nT2 强指纹浏览器二进制：")

    # --- cloakbrowser ---
    try:
        import cloakbrowser
        info = cloakbrowser.binary_info()
        installed = bool(info.get("installed"))
        line(OK if installed else BAD, "cloakbrowser",
             f"{'已就绪' if installed else '二进制缺失'}  "
             f"version={info.get('version')}  tier={info.get('tier')}")
        if not installed:
            print(f"        期望路径: {info.get('binary_path')}")
            print(f"        下载源  : {info.get('download_url')}")
            fixes.append(
                "下载 cloakbrowser 二进制（约 536MB）：\n"
                "       python -c \"import cloakbrowser; cloakbrowser.ensure_binary()\"\n"
                "       若下载源被网络拦截，请换网络或配置 --proxy")
    except ImportError:
        line(WARN, "cloakbrowser", "未安装 —— pip install cloakbrowser")
    except Exception as e:
        line(WARN, "cloakbrowser", f"二进制状态未知：{type(e).__name__}: {e}")

    # --- camoufox ---
    try:
        import camoufox  # noqa: F401
        cands = []
        if os.environ.get("LOCALAPPDATA"):
            cands.append(Path(os.environ["LOCALAPPDATA"]) / "camoufox")
        cands += [Path.home() / ".cache" / "camoufox", Path.home() / ".camoufox"]
        found = [p for p in cands if p.exists() and any(p.iterdir())]
        line(OK if found else BAD, "camoufox",
             ("已就绪  " + str(found[0])) if found else "Firefox 构建缺失")
        if not found:
            print(f"        查找位置: {', '.join(str(p) for p in cands)}")
            fixes.append("下载 camoufox Firefox 构建：python -m camoufox fetch")
    except ImportError:
        line(WARN, "camoufox", "未安装 —— pip install camoufox")
    except Exception as e:
        line(WARN, "camoufox", f"状态未知：{type(e).__name__}: {e}")

    # --- 结论 ---
    if not _t2_ready():
        print("\n  ⚠ T2 当前不可用：装了 Python 包但缺少浏览器二进制，"
              "升级到 T2 时会返回「后端不可用」并退出码 3。")
        print("    若无法下载二进制，请把 --max-tier 设为 1，避免无意义的重试。")


def _t2_ready() -> bool:
    try:
        import cloakbrowser
        if cloakbrowser.binary_info().get("installed"):
            return True
    except Exception:
        pass
    try:
        import camoufox  # noqa: F401
        for p in [Path(os.environ.get("LOCALAPPDATA", "")) / "camoufox",
                  Path.home() / ".cache" / "camoufox", Path.home() / ".camoufox"]:
            if p.exists() and any(p.iterdir()):
                return True
    except Exception:
        pass
    return False


def check_state() -> None:
    print("\n状态与画像：")
    line(OK, "状态目录", str(STATE_DIR))
    line(OK if STATE_DIR.exists() else WARN, "目录可写",
         "已存在" if STATE_DIR.exists() else "尚未创建（首次抓取时自动建）")
    profs = load_profiles()
    line(OK, "站点画像", f"{len(profs)} 个域名已记录")
    for dom, p in list(profs.items())[:8]:
        print(f"        · {dom:<32} 起跳层 T{p.get('preferred_tier')} "
              f"({p.get('preferred_backend')})  成功{p.get('success')}/拦截{p.get('blocks')}")
    bl = LatencyBaseline()
    s = bl.summary()
    if s:
        print(f"\n  耗时基线（{len(s)} 条 · 中位数 × {bl.factor:g} = 判慢阈值）：")
        for k, v in sorted(s.items())[:8]:
            print(f"        · {v['domain']:<32} {v['tier']:<4} 样本{v['samples']:>3}  "
                  f"中位数 {v['median_ms']:>6}ms  → 阈值 {int(v['median_ms']*bl.factor):>6}ms")
    else:
        print("\n  耗时基线：尚无记录（成功抓取若干次后自动积累，用于判断「相对变慢」）")


def check_network() -> None:
    """多目标探测：单个域名被代理拦截，不代表整体网络不可用。"""
    print("\n网络：")
    try:
        from curl_cffi import requests as creq
    except Exception as e:
        line(BAD, "curl_cffi", f"不可用：{type(e).__name__}: {e}")
        return
    targets = ["https://pypi.org", "https://www.baidu.com", "https://example.com"]
    ok_n = 0
    blocked = []
    for u in targets:
        try:
            r = creq.get(u, timeout=12, impersonate="chrome")
            good = r.status_code < 400
            ok_n += 1 if good else 0
            line(OK if good else WARN, u.replace("https://", ""),
                 f"HTTP {r.status_code}")
        except Exception as e:
            blocked.append(u)
            line(WARN, u.replace("https://", ""), f"{type(e).__name__}: {str(e)[:60]}")
    if ok_n == 0:
        line(BAD, "总体", "所有目标均不可达——网络或代理配置有问题")
        fixes.append("检查网络/代理；如需代理请用 --proxy 传入")
    elif blocked:
        line(OK, "总体", f"{ok_n}/{len(targets)} 可达；被拦的域名可能需要代理（--proxy）")
    else:
        line(OK, "总体", f"{ok_n}/{len(targets)} 全部可达")


def check_miniprogram() -> None:
    """W 层（微信小程序）前提条件检查——四条路线各自的前置条件完全不同。"""
    print("\n微信小程序（W 层）：")
    import shutil as _sh

    # W0：包缓存
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import mp_discover as _mpd
        roots = _mpd._roots()
    except Exception as e:
        roots = []
        line(WARN, "W0 包盘点", f"模块加载失败：{type(e).__name__}")
    if roots:
        n = 0
        enc = 0
        for r in roots:
            pd = r / "packages"
            if pd.is_dir():
                for d in pd.iterdir():
                    if d.is_dir():
                        n += 1
        line(OK, "W0 包盘点", f"{len(roots)} 个缓存根、{n} 个小程序 → 可运行 mp_discover.py")
    else:
        line(WARN, "W0 包盘点", "未找到小程序缓存（PC 微信未装或从未打开过小程序）")

    # W1：开发者工具
    devtools_homes = [
        Path("C:/Program Files (x86)/Tencent/微信web开发者工具"),
        Path("C:/Program Files/Tencent/微信web开发者工具"),
        Path("D:/Program Files (x86)/Tencent/微信web开发者工具"),
        Path("D:/Program Files/Tencent/微信web开发者工具"),
    ]
    dev = next((h for h in devtools_homes if h.is_dir()), None)
    cli = None
    for home in devtools_homes:
        if home.is_dir():
            for rel in ("cli.bat", "cli"):
                if (home / rel).exists():
                    cli = home / rel
                    break
        if cli:
            break
    if cli:
        line(OK, "W1 官方 SDK", f"开发者工具 CLI 就绪：{cli}")
    elif dev:
        line(WARN, "W1 官方 SDK", "开发者工具有，但找不到 cli.bat")
        fixes.append("确认微信开发者工具安装完整（需含 cli.bat），并开启「设置→安全设置→服务端口」")
    else:
        line(WARN, "W1 官方 SDK", "未安装微信开发者工具 → 该路线不可用")
        fixes.append("若要走 W1（官方 SDK，最合规）：安装微信开发者工具并开启服务端口")

    # W2：mitmproxy + 微信客户端
    mitm = _sh.which("mitmdump") or _sh.which("mitmproxy")
    wechat = None
    for p in (Path("E:/Program Files/Weixin/Weixin.exe"),
              Path("C:/Program Files/Tencent/WeChat/WeChat.exe"),
              Path("D:/Program Files/Tencent/WeChat/WeChat.exe")):
        if p.exists():
            wechat = p
            break
    if mitm:
        line(OK, "W2 MITM", f"mitmproxy 就绪：{mitm}")
    else:
        line(WARN, "W2 MITM", "mitmproxy 未安装（pip install mitmproxy）")
        fixes.append("走 W2 抓包：pip install mitmproxy，然后 python mp_capture.py init")
    line(OK if wechat else WARN, "W2 微信客户端",
         str(wechat) if wechat else "未在常见路径找到微信客户端")

    # W3：解包工具
    unpack = None
    for b in ("KillWxapkg", "killwxapkg", "wxapkg", "pc_wxapkg_decrypt"):
        if _sh.which(b):
            unpack = b
            break
    if unpack:
        line(OK, "W3 反编译", f"解包工具就绪：{unpack}")
    else:
        line(WARN, "W3 反编译", "未检测到解包/解密工具（见 mp_unpack.py guide）")
        fixes.append("走 W3 反编译：装 KillWxapkg(★6011) 或 wxapkg(★4245)，"
                     "详见 python scripts/mp_unpack.py guide")


def main() -> int:
    print("=" * 68)
    print("Super auto-web toolpack 环境体检")
    print("=" * 68)
    check_python()
    check_backends()
    check_browsers()
    check_t2_binaries()
    check_miniprogram()
    check_state()
    check_network()

    print("\n" + "=" * 68)
    if fixes:
        print("修复建议（按顺序处理，完成后重跑本脚本）：")
        for i, f in enumerate(fixes, 1):
            print(f"  {i}. {f}")
    else:
        print("未发现需要修复的问题。")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
