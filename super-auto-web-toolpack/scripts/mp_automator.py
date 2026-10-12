# -*- coding: utf-8 -*-
"""W1 · 微信小程序自动化（官方 SDK：miniprogram-automator）。

定位
----
**四条路线里最合规的一条**——用的是微信官方提供的自动化 SDK，
不做任何逆向、不注入进程、不破解签名。

前提条件（硬前提，缺一不可）
--------------------------
1. 安装「微信开发者工具」
2. 开发者工具中开启「设置 → 安全设置 → 服务端口」
3. 有目标小程序的**源码项目目录**（或用工具打开该小程序的代码片段）

> 注意：官方 SDK 操作的是**开发者工具中加载的项目**，不是微信客户端里跑的小程序。
> 所以它适合「我自己的小程序 / 我有源码的小程序」，不适合「抓别人线上的小程序」。
> 要抓线上小程序，应走 W2（MITM 抓包）或 W3（反编译）。

关于 SDK 本身
-------------
`miniprogram-automator` 只能从 npm 安装，**不在 GitHub 上开源**
（npm 元数据显示仓库为 `git@git.code.oa.com:devtools/automator.git`，腾讯内网）。
所以它没有公开源码可审计——这一点与 W2/W3 的开源工具不同。

子命令
------
  check    检测环境（开发者工具 / Node / npm 包）
  init     生成一份开箱即用的 Node 自动化脚本
  guide    打印完整操作指引
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import STATE_DIR, now_iso  # noqa: E402

AUTOMATOR_DIR = STATE_DIR / "mp_automator"

# 微信开发者工具 CLI 的相对路径候选（cli.bat / cli）
DEVTOOLS_HOMES = [
    Path("C:/Program Files (x86)/Tencent/微信web开发者工具"),
    Path("C:/Program Files/Tencent/微信web开发者工具"),
    Path("D:/Program Files (x86)/Tencent/微信web开发者工具"),
    Path("D:/Program Files/Tencent/微信web开发者工具"),
    Path("C:/Users/Public/Documents/微信web开发者工具"),
]

NODE_SCRIPT = r"""// 微信小程序自动化脚本（由 super-auto-web-toolpack 生成）
// 依赖： npm i miniprogram-automator
const automator = require('miniprogram-automator');

const CLI_PATH = process.env.MP_DEVTOOLS_CLI || '__CLI_PATH__';
const PROJECT  = process.env.MP_PROJECT || '__PROJECT__';

(async () => {
  let mini;
  try {
    mini = await automator.launch({
      cliPath: CLI_PATH,
      projectPath: PROJECT,
    });
    console.log('[ok] 已连接开发者工具');

    const page = await mini.currentPage();
    console.log('[page]', page.path);

    // 1) 抓取页面结构（ref-annotated，元素少、省 token）
    const el = await page.$('view');
    console.log('[root]', el ? await el.tagName() : '(no view)');

    // 2) 取页面数据（小程序页面自带 data，比解析 DOM 更准）
    const data = await page.data();
    console.log('[data]', JSON.stringify(data).slice(0, 800));

    // 3) 示例操作：按选择器点击 / 输入
    // const btn = await page.$('.submit');
    // if (btn) await btn.tap();
    // const input = await page.$('input');
    // if (input) await input.input('hello');

    // 4) 页面截图（存档用）
    // await mini.screenshot({ path: 'shot.png' });

    await mini.close();
    console.log('[done]');
  } catch (e) {
    console.error('[fail]', e && e.message ? e.message : e);
    if (mini) { try { await mini.close(); } catch (_) {} }
    process.exit(1);
  }
})();
"""


def find_cli() -> Path | None:
    for home in DEVTOOLS_HOMES:
        if not home.is_dir():
            continue
        for rel in ("cli.bat", "cli", "bin/cli.bat", "bin/cli"):
            p = home / rel
            if p.exists():
                return p
    return None


def cmd_check(args) -> int:
    print("=" * 74)
    print("W1 · 小程序自动化环境检查（官方 SDK 路线）")
    print("=" * 74)
    ok = True

    # 1) 开发者工具
    home = next((h for h in DEVTOOLS_HOMES if h.is_dir()), None)
    if home:
        print(f"[✓] 微信开发者工具目录  : {home}")
    else:
        print("[✗] 微信开发者工具目录  : 未找到")
        print("      → 从 https://developers.weixin.qq.com/miniprogram/dev/devtools/download.html 安装")
        ok = False

    cli = find_cli()
    if cli:
        print(f"[✓] 开发者工具 CLI      : {cli}")
    else:
        print("[✗] 开发者工具 CLI      : 未找到（cli.bat）")
        if home:
            print(f"      已装工具但 CLI 不在预期位置，请确认 {home} 下有 cli.bat")
        ok = False

    # 2) Node / npm
    node = shutil.which("node")
    npm = shutil.which("npm")
    print(f"[{'✓' if node else '✗'}] Node                  : {node or '未找到'}")
    print(f"[{'✓' if npm else '✗'}] npm                   : {npm or '未找到'}")
    if not (node and npm):
        ok = False

    # 3) SDK 是否已装（在工作区里找）
    found_pkg = None
    for d in [AUTOMATOR_DIR] + ([Path(args.project)] if args.project else []):
        if d and (d / "node_modules" / "miniprogram-automator").is_dir():
            found_pkg = d / "node_modules" / "miniprogram-automator"
            break
    if found_pkg:
        print(f"[✓] miniprogram-automator: {found_pkg}")
    else:
        print("[ ] miniprogram-automator: 未安装（在项目里执行 npm i miniprogram-automator）")

    print()
    print("服务端口（必须手动开）：开发者工具 → 设置 → 安全设置 → 打开「服务端口」")
    print()
    if ok:
        print("结论：环境就绪，可用 `mp_automator.py init` 生成脚本。")
    else:
        print("结论：环境未就绪。补全上述 [✗] 项后重试。")
    return 0 if ok else 3


def cmd_init(args) -> int:
    d = Path(args.outdir) if args.outdir else AUTOMATOR_DIR
    d.mkdir(parents=True, exist_ok=True)
    cli = find_cli()
    script = (NODE_SCRIPT
              .replace("__CLI_PATH__", str(cli) if cli else "C:/path/to/cli.bat")
              .replace("__PROJECT__", args.project or "C:/path/to/miniprogram"))
    p = d / "mp_auto.js"
    p.write_text(script, encoding="utf-8")
    (d / "package.json").write_text(json.dumps({
        "name": "sawt-mp-automator",
        "private": True,
        "version": "1.0.0",
        "dependencies": {"miniprogram-automator": "^0.12.1"},
    }, indent=2), encoding="utf-8")
    (d / "README.md").write_text(
        "# W1 小程序自动化\n\n"
        "```bash\n"
        "npm install\n"
        "node mp_auto.js\n"
        "```\n\n"
        "环境变量可覆盖：`MP_DEVTOOLS_CLI`（cli.bat 路径）、`MP_PROJECT`（小程序项目目录）。\n",
        encoding="utf-8")
    print(f"已生成自动化工作区：{d}")
    print(f"  · mp_auto.js   （Node 脚本，含页面结构 / 页面数据 / 点击 / 输入 / 截图示例）")
    print(f"  · package.json （已锁定 miniprogram-automator ^0.12.1）")
    print()
    print("运行：")
    print(f"  cd \"{d}\" && npm install && node mp_auto.js")
    if not cli:
        print("\n⚠ 未检测到开发者工具 CLI，请先 `mp_automator.py check` 并按提示安装。")
    return 0


def cmd_guide(args) -> int:
    print("""W1 · 微信小程序自动化（官方 SDK）操作指引
================================================================

【为什么选它】
  · 唯一不做逆向、不注入、不破解的路线
  · 小程序页面自带 data，取数比解析 DOM 更准
  · 官方 SDK 输出 ref-annotated 结构，token 开销低

【硬前提】
  1. 安装微信开发者工具
  2. 设置 → 安全设置 → 打开「服务端口」
  3. 准备小程序源码项目目录（. 有 app.json 的那个目录）

【关键认知 · 避免走错路】
  官方 SDK 操作的是「开发者工具里加载的项目」，
  不是「微信客户端里跑着的线上小程序」。

  → 适合：你自己的小程序 / 你有源码的小程序
  → 不适合：抓别人线上跑的小程序 ← 这种要走 W2 或 W3

【四步走】
  1) python mp_automator.py check
  2) python mp_automator.py init --project "D:/my-miniprogram"
  3) cd <工作区> && npm install
  4) node mp_auto.js

【常见坑】
  · 「服务端口未开启」→ 报 connect 失败；去设置里打开
  · 工具版本与 SDK 不匹配 → 升级开发者工具到最新稳定版
  · 端口被占 → automator.launch 会自选端口，一般不需手动指定；必要时用 launch({port})
""")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="W1 · 微信小程序自动化（官方 SDK miniprogram-automator）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("check", help="检测环境")
    p1.add_argument("--project", default=None, help="可选：小程序项目目录（用于查 SDK 是否已装）")
    p1.set_defaults(fn=cmd_check)

    p2 = sub.add_parser("init", help="生成 Node 自动化脚本")
    p2.add_argument("--project", default=None, help="小程序项目目录（含 app.json）")
    p2.add_argument("--outdir", default=None, help="输出目录")
    p2.set_defaults(fn=cmd_init)

    p3 = sub.add_parser("guide", help="打印完整操作指引")
    p3.set_defaults(fn=cmd_guide)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
