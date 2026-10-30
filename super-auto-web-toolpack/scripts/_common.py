# -*- coding: utf-8 -*-
"""super-auto-web-toolpack 共用模块：统一返回契约 / 站点画像 / 同域限速 / robots 预检。

设计原则
--------
1. **不静默失败**：任何后端不可用，返回 ok=False + reason，不抛异常打断编排。
2. **状态显式**：所有结果写入固定字段，供上层 Agent 与画像机制消费。
3. **合规内置**：同域最小间隔 + ±抖动、指数退避、robots.txt 预检、不落任何凭据。
"""
from __future__ import annotations

import json
import os
import random
import re
import string
import sys
import time
import urllib.parse
import urllib.robotparser
from datetime import datetime, timezone
from pathlib import Path


# --------------------------------------------------------------------------- #
# 输出编码：中文 Windows 下 stdout 被重定向时的必然崩溃点
# --------------------------------------------------------------------------- #
def _ensure_utf8_stdout() -> None:
    """把 stdout/stderr 切成 UTF-8（幂等，永不抛）。

    症状（实测：中文 Windows / chcp 936 / ``sys.stdout.encoding == 'gbk'``）：
    只要 stdout 被管道或重定向（Agent 调用、CI、IDE、``| cat`` 都算），
    ``print("✓ ...")`` 立刻
    ``UnicodeEncodeError: 'gbk' codec can't encode character '\\u2713'``，
    抓取已经成功却崩在打印阶段、退出码 1，会被误判成"环境坏了"。

    修法刻意**不是**把 ✓ / ! / ✗ 降级成 ASCII —— 那会丢掉终端里一眼可读的
    状态语义；而是把输出流本身改成 UTF-8（现代 Windows 终端与管道都能呈现）。
    ``reconfigure`` 不存在的流（自定义包装、已关闭的流）静默跳过。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


_ensure_utf8_stdout()

SKILL_DIR = Path(__file__).resolve().parent.parent

# 状态目录：默认 ~/.super-auto-web-toolpack，可用环境变量覆盖
STATE_DIR = Path(os.environ.get("SAWT_STATE_DIR") or (Path.home() / ".super-auto-web-toolpack"))
PROFILE_FILE = STATE_DIR / "site_profiles.json"
HISTORY_FILE = STATE_DIR / "fetch_history.jsonl"

MAX_HTML_BYTES = int(os.environ.get("SAWT_MAX_HTML_BYTES") or 4_000_000)

# 各层级语义（供文档与输出复用，单一事实来源）
TIER_NAMES = {
    0: "T0 轻量直连 (curl_cffi)",
    1: "T1 patchright (JS 渲染/挑战)",
    2: "T2 强指纹浏览器 (cloakbrowser/camoufox)",
    3: "T3 需人工介入 (验证码/代理)",
}
BACKEND_TIER = {
    "curl_cffi": 0,
    "patchright": 1,
    "cloakbrowser": 2,
    "camoufox": 2,
    "manual": 3,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def domain_of(url: str) -> str:
    try:
        return (urllib.parse.urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def truncate(text: str | None, limit: int = MAX_HTML_BYTES) -> str | None:
    if text is None:
        return None
    return text[:limit] if len(text) > limit else text


def new_result(url: str, *, tier: int, backend: str) -> dict:
    """统一的返回契约。所有后端必须填满这里的字段。"""
    return {
        "url": url,
        "final_url": url,
        "ok": False,
        "tier": tier,
        "tier_name": TIER_NAMES.get(tier, f"T{tier}"),
        "backend": backend,
        "status": None,
        "title": None,
        "html": None,
        "text": None,
        "elapsed_ms": None,
        "blocked": False,
        "block_vendor": None,
        "block_reason": None,
        "error": None,
        "attempts": [],
        "fetched_at": now_iso(),
        # 降级标记：后端不是最理想实现（如 T1 的 chrome 通道启动失败后回退
        # patchright 自带 Chromium）时置 true + 原因，调用方可据此判断质量。
        "degraded": False,
        "degraded_reason": None,
    }


# --------------------------------------------------------------------------- #
# HTML → 纯文本（不引入重量级依赖；bs4 存在时优先用）
# --------------------------------------------------------------------------- #
_DROP_TAGS = re.compile(
    r"<(script|style|noscript|template|svg|head)\b[^>]*>.*?</\1>", re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\u00a0]+")
_MULTI_NL = re.compile(r"\n{3,}")


def html_to_text(html: str | None) -> str | None:
    if not html:
        return None
    try:
        from bs4 import BeautifulSoup  # type: ignore
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript", "template", "svg", "head"]):
            tag.decompose()
        raw = soup.get_text("\n")
    except Exception:
        raw = _TAGS.sub(" ", _DROP_TAGS.sub(" ", html))
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    raw = _WS.sub(" ", raw)
    raw = _MULTI_NL.sub("\n\n", raw)
    return raw.strip()


def extract_title(html: str | None) -> str | None:
    if not html:
        return None
    try:
        from bs4 import BeautifulSoup  # type: ignore
        t = BeautifulSoup(html, "lxml").find("title")
        if t and t.get_text(strip=True):
            return t.get_text(strip=True)[:300]
    except Exception:
        pass
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip()[:300] if m else None


# --------------------------------------------------------------------------- #
# 站点画像：记录每个域名"上次走通的层级"，下次直接从该层起步
# --------------------------------------------------------------------------- #
def _ensure_state() -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def load_profiles() -> dict:
    _ensure_state()
    if PROFILE_FILE.exists():
        try:
            return json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_profiles(data: dict) -> None:
    _ensure_state()
    try:
        PROFILE_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def get_profile(url: str) -> dict:
    return load_profiles().get(domain_of(url), {})


def update_profile(url: str, *, tier: int, backend: str, ok: bool,
                   vendor: str | None = None) -> None:
    """成功则把该域名的"起跳层"抬到本次成功的层级；失败则记录失败计数。"""
    dom = domain_of(url)
    if not dom:
        return
    data = load_profiles()
    prof = data.get(dom) or {
        "domain": dom, "preferred_tier": 0, "preferred_backend": "curl_cffi",
        "success": 0, "fail": 0, "vendors_seen": [], "blocks": 0,
    }
    prof["last_url"] = url
    prof["updated_at"] = now_iso()
    if ok:
        prof["success"] = int(prof.get("success", 0)) + 1
        prof["fail"] = 0
        prof["preferred_tier"] = tier
        prof["preferred_backend"] = backend
    else:
        prof["fail"] = int(prof.get("fail", 0)) + 1
        prof["blocks"] = int(prof.get("blocks", 0)) + 1
    if vendor:
        vs = set(prof.get("vendors_seen") or [])
        vs.add(vendor)
        prof["vendors_seen"] = sorted(vs)
    data[dom] = prof
    save_profiles(data)


def append_history(record: dict) -> None:
    _ensure_state()
    try:
        with HISTORY_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# 同域限速：最小间隔 + ±20% 抖动 + 失败指数退避
# --------------------------------------------------------------------------- #
class DomainRateLimiter:
    def __init__(self, min_interval: float = 1.0, max_backoff: float = 30.0):
        self.min_interval = float(min_interval)
        self.max_backoff = float(max_backoff)
        self._last: dict[str, float] = {}
        self._fails: dict[str, int] = {}

    def wait(self, domain: str) -> float:
        if not domain:
            return 0.0
        base = self.min_interval * (2 ** self._fails.get(domain, 0))
        delay = min(base, self.max_backoff)
        delay *= random.uniform(0.8, 1.2)          # 抖动：防规律性请求被识别
        elapsed = time.time() - self._last.get(domain, 0.0)
        sleep_for = max(0.0, delay - elapsed)
        if sleep_for > 0:
            time.sleep(sleep_for)
        self._last[domain] = time.time()
        return sleep_for

    def on_success(self, domain: str) -> None:
        self._fails[domain] = 0

    def on_failure(self, domain: str) -> None:
        self._fails[domain] = self._fails.get(domain, 0) + 1


# --------------------------------------------------------------------------- #
# robots.txt 预检（合规前置，默认开启）
# --------------------------------------------------------------------------- #
class RobotsGate:
    def __init__(self, enabled: bool = True, user_agent: str = "*"):
        self.enabled = enabled
        self.ua = user_agent
        self._cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def _parser(self, url: str):
        dom = domain_of(url)
        if dom in self._cache:
            return self._cache[dom]
        rp = None
        try:
            scheme = urllib.parse.urlparse(url).scheme or "https"
            rp = urllib.robotparser.RobotFileParser()
            rp.set_url(f"{scheme}://{dom}/robots.txt")
            rp.read()
        except Exception:
            rp = None
        self._cache[dom] = rp
        return rp

    def allowed(self, url: str) -> tuple[bool, str]:
        """返回 (是否允许, 说明)。取不到 robots.txt 时视为允许并注明。"""
        if not self.enabled:
            return True, "robots 检查已关闭"
        rp = self._parser(url)
        if rp is None:
            return True, "robots.txt 不可达（视为允许，请自行确认站点条款）"
        try:
            if rp.can_fetch(self.ua, url):
                return True, "robots.txt 允许"
            return False, "robots.txt 禁止该路径"
        except Exception:
            return True, "robots 解析异常（视为允许）"


def short(s: str | None, n: int = 120) -> str:
    if not s:
        return ""
    s = re.sub(r"\s+", " ", str(s)).strip()
    return s[:n] + ("…" if len(s) > n else "")


# --------------------------------------------------------------------------- #
# 本机路径探测：盘符不能写死（用户可能装在任意盘）
# --------------------------------------------------------------------------- #
def existing_drives() -> list[str]:
    """枚举本机**真实存在**的盘符根，形如 ``["C:/", "D:/"]``；非 Windows 返回空列表。

    症状：老实现把候选写死成 ``C:/`` + ``D:/`` 两处，装在 ``E:``/``F:`` 盘的
    微信开发者工具、微信客户端、小程序项目会被静默漏检（本机 E: 恰好在候选里，纯属运气）。
    """
    if os.name != "nt":
        return []
    out: list[str] = []
    for ch in string.ascii_uppercase:
        root = f"{ch}:/"
        try:
            if os.path.exists(root):
                out.append(root)
        except OSError:
            continue
    return out


def program_roots() -> list[Path]:
    """程序安装根目录候选：环境变量 + 每个存在盘符的 Program Files + 用户级 Programs。"""
    roots: list[Path] = []
    for env in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        v = os.environ.get(env)
        if v:
            roots.append(Path(v))
    for d in existing_drives():
        roots.append(Path(d) / "Program Files")
        roots.append(Path(d) / "Program Files (x86)")
    roots.append(Path.home() / "AppData" / "Local" / "Programs")
    dedup: list[Path] = []
    for p in roots:
        if p not in dedup:
            dedup.append(p)
    return dedup


def wechat_devtools_homes() -> list[Path]:
    """微信开发者工具的候选安装目录（跨盘符、去重、保序）。

    CLI 所在位置随版本不同：3.x 在 ``cli.bat``，2.x 在 ``cli.js``，
    路径可能是 ``<盘>:\\Program Files (x86)\\Tencent\\微信web开发者工具\\``。
    """
    subs = (Path("Tencent") / "微信web开发者工具", Path("微信web开发者工具"))
    out: list[Path] = []
    for root in program_roots():
        for sub in subs:
            p = root / sub
            if p not in out:
                out.append(p)
    # 公共文档目录同样不写死盘符：PUBLIC 环境变量才是权威（可能不在 C:）
    pub_root = Path(os.environ.get("PUBLIC") or "C:/Users/Public")
    pub = pub_root / "Documents" / "微信web开发者工具"
    if pub not in out:
        out.append(pub)
    return out


def wechat_client_candidates() -> list[Path]:
    """微信客户端 exe 候选：3.x 为 ``WeChat.exe``，4.x 改名 ``Weixin.exe``。"""
    rels = (
        Path("Tencent") / "WeChat" / "WeChat.exe",
        Path("Tencent") / "Weixin" / "Weixin.exe",
        Path("Weixin") / "Weixin.exe",
    )
    out: list[Path] = []
    for root in program_roots():
        for rel in rels:
            p = root / rel
            if p not in out:
                out.append(p)
    return out


# --------------------------------------------------------------------------- #
# 相对基线：用站点自身的历史耗时中位数判断"变慢"
# --------------------------------------------------------------------------- #
BASELINE_FILE = STATE_DIR / "latency_baseline.json"


class LatencyBaseline:
    """按 (域名, 层级) 记录历史耗时，用中位数作基线判断"相对变慢"。

    为什么不能只用固定阈值
    ----------------------
    * T0 一个纯 GET 正常 <2s，固定 10s 阈值够用；
    * 但 T1/T2 是浏览器层，本身就要 5–8s，任何固定阈值都会全员误报；
    * 若因此对浏览器层关掉速度检测，又恰好给"只针对浏览器层限速"留了空子。

    解法：拿**站点自己的历史中位数**当对照——
    "这次比它平时慢了 3 倍以上" 才是可靠信号，且天然适配每个站点的常态。
    冷启动（样本不足）时回退到固定阈值。
    """

    def __init__(self, n: int = 10, factor: float = 3.0, min_samples: int = 3,
                 enabled: bool = True):
        self.n = max(1, int(n))
        self.factor = float(factor)
        self.min_samples = max(1, int(min_samples))
        self.enabled = enabled
        self._data: dict[str, list[int]] | None = None

    # -- 存取 ---------------------------------------------------------------
    def _load(self) -> dict:
        if self._data is not None:
            return self._data
        _ensure_state()
        raw = {}
        if BASELINE_FILE.exists():
            try:
                raw = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
            except Exception:
                raw = {}
        self._data = {k: list(v) for k, v in raw.items() if isinstance(v, list)}
        return self._data

    def _save(self) -> None:
        if self._data is None:
            return
        _ensure_state()
        try:
            BASELINE_FILE.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    @staticmethod
    def _key(domain: str, tier: int) -> str:
        return f"{domain}|T{tier}"

    # -- 记录与查询 ---------------------------------------------------------
    def record(self, domain: str, tier: int, ms: int | None) -> None:
        """只记录**成功且未被拦截**的耗时——被拦的耗时没有参考价值。"""
        if not self.enabled or not domain or not ms or ms <= 0:
            return
        data = self._load()
        k = self._key(domain, tier)
        series = data.get(k, [])
        series.append(int(ms))
        data[k] = series[-self.n:]
        self._save()

    def median(self, domain: str, tier: int) -> float | None:
        series = self._load().get(self._key(domain, tier)) or []
        if len(series) < self.min_samples:
            return None
        s = sorted(series)
        mid = len(s) // 2
        return float(s[mid]) if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0

    def samples(self, domain: str, tier: int) -> int:
        return len(self._load().get(self._key(domain, tier)) or [])

    def resolve_slow_ms(self, domain: str, tier: int, fixed_default: int = 0,
                        fixed_override: int | None = None) -> tuple[int, str]:
        """决定该次请求的"过慢"阈值，并说明依据来源。

        优先级：命令行显式指定 > 相对基线 > 固定默认 > 不启用
        """
        if fixed_override is not None:
            return int(fixed_override), "命令行 --slow-ms 指定"
        if self.enabled:
            med = self.median(domain, tier)
            if med is not None:
                thr = int(med * self.factor)
                return thr, (f"相对基线：{domain} T{tier} 中位数 {med:.0f}ms "
                             f"×{self.factor:g} = {thr}ms （样本 {self.samples(domain, tier)}）")
        if fixed_default > 0:
            return int(fixed_default), "固定默认（冷启动，基线样本不足）"
        return 0, "未启用（基线与固定阈值均无）"

    def summary(self) -> dict:
        out = {}
        for k, v in self._load().items():
            if v:
                s = sorted(v)
                mid = len(s) // 2
                med = s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0
                dom, _, tier = k.rpartition("|")
                out[k] = {"samples": len(v), "median_ms": int(med),
                          "max_ms": max(v), "min_ms": min(v), "domain": dom, "tier": tier}
        return out
