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
from _common import STATE_DIR, now_iso, wechat_devtools_homes  # noqa: E402

AUTOMATOR_DIR = STATE_DIR / "mp_automator"

# 微信开发者工具 CLI 的候选安装目录（cli.bat / cli）：跨全部存在盘符，盘符不写死
DEVTOOLS_HOMES = wechat_devtools_homes()

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


# --------------------------------------------------------------------------- #
# scaffold：生成专为 automator 测试设计的 TDesign 小程序项目
# --------------------------------------------------------------------------- #
# 项目设计要点（为什么每个文件长这样）：
#   * 页面 onLoad 即 setData({loaded: true, ...}) —— automator 拿 page.data()
#     可直接断言"页面已就绪"，不依赖任何 DOM 探测
#   * 一个绑定 tap 的按钮 setData({clicked: true}) —— 可断言交互链路
#   * 表单页含 t-input —— 可测 automator 的 input()
#   * app.json 刻意不带 "style": "v2" —— TDesign 官方要求，否则样式冲突
#   * project.config.json 配 packNpmManually —— 配合 build-npm 离线构建

_PROJECT_CONFIG = {
    "appid": "touristappid",          # 游客模式 appid，无需注册小程序
    "compileType": "miniprogram",
    "libVersion": "2.33.0",
    "projectname": "sawt-automator-demo",
    "setting": {
        "es6": True,
        "postcss": True,
        "minified": True,
        "packNpmManually": True,
        "packNpmRelationList": [{
            "packageJsonPath": "./package.json",
            "miniprogramNpmDistDir": "./",
        }],
    },
}

_APP_JSON = {
    "pages": ["pages/index/index", "pages/form/form"],
    "window": {
        "navigationBarTitleText": "SAWT Automator Demo",
        "navigationBarBackgroundColor": "#0052d9",
        "navigationBarTextStyle": "white",
    },
    "sitemapLocation": "sitemap.json",
}
# 注意：刻意不带 "style": "v2"（TDesign 要求移除，否则样式冲突）

_APP_JS = """App({
  onLaunch() {
    // 供 automator 断言 app 已启动
    this.globalData = { launchedAt: Date.now() };
  },
  globalData: {},
});
"""

_APP_WXSS = """page {
  --td-button-primary-bg-color: #0052d9;
  background: #f6f7f9;
  font-size: 28rpx;
}
"""

_SITEMAP = {"desc": "sawt demo", "rules": [{"action": "allow", "page": "*"}]}

_INDEX_JSON = {
    "usingComponents": {
        "t-button": "tdesign-miniprogram/button/button",
        "t-cell": "tdesign-miniprogram/cell/cell",
        "t-cell-group": "tdesign-miniprogram/cell-group/cell-group",
        "t-tag": "tdesign-miniprogram/tag/tag",
    }
}

_INDEX_WXML = """<view class="page">
  <view class="hero">
    <t-tag theme="primary" variant="light">W1 automator demo</t-tag>
    <view class="title">Super auto-web toolpack</view>
  </view>

  <t-cell-group>
    <t-cell title="页面状态" note="{{loaded ? '已加载' : '加载中'}}" />
    <t-cell title="点击状态" note="{{clicked ? '已点击' : '未点击'}}" />
    <t-cell title="加载时间" note="{{loadedAt}}" />
  </t-cell-group>

  <view class="actions">
    <t-button theme="primary" size="large" block bind:tap="onTap"
              id="main-btn">点我（automator 可断言）</t-button>
    <t-button theme="light" size="large" block bind:tap="goForm"
              id="go-form">去表单页</t-button>
  </view>
</view>
"""

_INDEX_WXSS = """.page { padding: 32rpx; }
.hero { padding: 48rpx 0 24rpx; }
.title { font-size: 40rpx; font-weight: 600; margin-top: 16rpx; }
.actions { margin-top: 48rpx; display: flex; flex-direction: column; gap: 24rpx; }
"""

_INDEX_JS = """Page({
  data: {
    loaded: false,
    clicked: false,
    loadedAt: '',
  },
  onLoad() {
    // automator 可断言：page.data().loaded === true
    this.setData({
      loaded: true,
      loadedAt: new Date().toLocaleString(),
    });
  },
  onTap() {
    // automator：page.$('#main-btn') → tap() → 断言 clicked === true
    this.setData({ clicked: true });
  },
  goForm() {
    wx.navigateTo({ url: '/pages/form/form' });
  },
});
"""

_FORM_JSON = {
    "usingComponents": {
        "t-input": "tdesign-miniprogram/input/input",
        "t-stepper": "tdesign-miniprogram/stepper/stepper",
        "t-button": "tdesign-miniprogram/button/button",
    }
}

_FORM_WXML = """<view class="page">
  <view class="title">表单交互（automator input 可测）</view>

  <t-input label="关键词" placeholder="输入后 automator 可断言"
           value="{{keyword}}" bind:change="onKeyword" />

  <view class="row">
    <text class="label">数量</text>
    <t-stepper value="{{count}}" min="1" max="99" bind:change="onCount" />
  </view>

  <view class="summary">已输入：{{keyword || '（空）'}} × {{count}}</view>

  <t-button theme="primary" block bind:tap="submit" id="submit-btn">提交</t-button>
</view>
"""

_FORM_WXSS = """.page { padding: 32rpx; }
.title { font-size: 36rpx; font-weight: 600; padding: 24rpx 0; }
.row { display: flex; align-items: center; padding: 16rpx 32rpx; }
.label { width: 140rpx; }
.summary { padding: 32rpx; color: #666; }
"""

_FORM_JS = """Page({
  data: { keyword: '', count: 1, submitted: false },
  onKeyword(e) {
    this.setData({ keyword: e.detail.value });
  },
  onCount(e) {
    this.setData({ count: e.detail.value });
  },
  submit() {
    // automator：input() 后 tap('#submit-btn') → 断言 submitted === true
    this.setData({ submitted: true });
  },
});
"""

# 配套断言脚本：装好工具+扫码后，`node demo_test.js` 一键验证 W1 全链路。
# 每个断言都对应 scaffold 页面里预埋的数据契约（loaded / clicked / 页面路径）。
_DEMO_TEST_JS = """// W1 全链路断言脚本（由 super-auto-web-toolpack scaffold 生成）
// 运行前提：微信开发者工具已安装、已扫码登录、已开启「服务端口」
// 运行：  node demo_test.js
const automator = require('miniprogram-automator');
const path = require('path');

const PROJECT = path.join(__dirname, 'demo_project');
let passed = 0, failed = 0;
function check(name, cond, detail) {
  if (cond) { passed++; console.log('  [PASS]', name); }
  else { failed++; console.log('  [FAIL]', name, detail !== undefined ? `(${JSON.stringify(detail)})` : ''); }
}

(async () => {
  let mini;
  try {
    console.log('启动开发者工具并加载项目…（首次约 30–60s）');
    mini = await automator.launch({
      projectPath: PROJECT,
      // cliPath 留空时 SDK 会用默认安装位置；必要时：
      // cliPath: 'C:/Program Files (x86)/Tencent/微信web开发者工具/cli.bat',
    });
    console.log('✓ 已连接\\n');

    // ---- 1) 首页加载断言 ----
    let page = await mini.currentPage();
    console.log('首页:', page.path);
    let data = await page.data();
    check('页面路径 = pages/index/index', page.path === 'pages/index/index', page.path);
    check('loaded === true（onLoad setData 生效）', data.loaded === true, data.loaded);
    check('loadedAt 非空', !!data.loadedAt, data.loadedAt);

    // ---- 2) 交互断言：tap 按钮 → clicked 变 true ----
    const btn = await page.$('#main-btn');
    check('找到 #main-btn（TDesign 组件渲染成功）', !!btn);
    if (btn) { await btn.tap(); await new Promise(r => setTimeout(r, 500)); }
    data = await page.data();
    check('clicked === true（tap 事件生效）', data.clicked === true, data.clicked);

    // ---- 3) 页面跳转断言 ----
    await mini.navigateTo('/pages/form/form');
    page = await mini.currentPage();
    check('跳转后路径 = pages/form/form', page.path === 'pages/form/form', page.path);

    // ---- 4) 表单页数据契约 ----
    data = await page.data();
    check('form 页 data 可读（keyword/count 存在）',
          'keyword' in data && 'count' in data, data);

    console.log(`\\n结果：${passed} 通过 / ${failed} 失败`);
    await mini.close();
    process.exit(failed ? 1 : 0);
  } catch (e) {
    console.error('\\n[启动失败]', e && e.message ? e.message : e);
    console.error('常见原因：① 工具未安装/未登录 ② 未开启「设置→安全设置→服务端口」');
    if (mini) { try { await mini.close(); } catch (_) {} }
    process.exit(2);
  }
})();
"""


def cmd_scaffold(args) -> int:
    d = Path(args.project) if args.project else (AUTOMATOR_DIR / "demo_project")
    if d.exists() and any(d.iterdir()) and not args.force:
        print(f"目录非空：{d}\n如需覆盖请加 --force", file=sys.stderr)
        return 2
    import json as _json

    pages = {
        "pages/index": (_INDEX_JSON, _INDEX_WXML, _INDEX_WXSS, _INDEX_JS),
        "pages/form": (_FORM_JSON, _FORM_WXML, _FORM_WXSS, _FORM_JS),
    }
    d.mkdir(parents=True, exist_ok=True)
    (d / "project.config.json").write_text(
        _json.dumps(_PROJECT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
    (d / "app.json").write_text(
        _json.dumps(_APP_JSON, ensure_ascii=False, indent=2), encoding="utf-8")
    (d / "app.js").write_text(_APP_JS, encoding="utf-8")
    (d / "app.wxss").write_text(_APP_WXSS, encoding="utf-8")
    (d / "sitemap.json").write_text(
        _json.dumps(_SITEMAP, ensure_ascii=False), encoding="utf-8")
    (d / "package.json").write_text(_json.dumps({
        "name": "sawt-automator-demo",
        "version": "1.0.0",
        "dependencies": {"tdesign-miniprogram": "^1.8.0"},
    }, indent=2), encoding="utf-8")
    for base, (jc, wxml, wxss, js) in pages.items():
        p = d / base
        p.mkdir(parents=True, exist_ok=True)
        name = base.rsplit("/", 1)[-1]
        (p / f"{name}.json").write_text(
            _json.dumps(jc, ensure_ascii=False, indent=2), encoding="utf-8")
        (p / f"{name}.wxml").write_text(wxml, encoding="utf-8")
        (p / f"{name}.wxss").write_text(wxss, encoding="utf-8")
        (p / f"{name}.js").write_text(js, encoding="utf-8")
    # 断言脚本写到工作区根（demo_project 的旁边），依赖工作区的 miniprogram-automator
    ws = d.parent
    test_js = ws / "demo_test.js"
    test_js.write_text(_DEMO_TEST_JS, encoding="utf-8")
    print(f"已生成 TDesign 测试项目：{d}")
    print("  · pages/index  —— 加载/点击断言点（loaded / clicked）")
    print("  · pages/form   —— 输入断言点（keyword / count / submitted）")
    print("  · appid=touristappid（游客模式，无需注册）")
    print(f"  · {test_js}  —— 一键断言脚本")
    print()
    print("下一步：")
    print(f'  1) cd "{d}" && npm install          # 装 tdesign-miniprogram')
    print(f'  2) cd "{ws}" && npm install miniprogram-automator   # SDK 装在测试端工作区')
    print(f"  3) python {Path(__file__).name} build-npm --project \"{d}\"")
    print(f"  4) python {Path(__file__).name} check --project \"{d}\"")
    print(f"  5) 装好工具并扫码登录后：cd \"{ws}\" && node demo_test.js")
    return 0


# --------------------------------------------------------------------------- #
# build-npm：按微信官方规则手动构建 miniprogram_npm（不依赖开发者工具）
# --------------------------------------------------------------------------- #
def _copytree(src: Path, dst: Path) -> None:
    import shutil
    if dst.exists():
        shutil.rmtree(dst)          # 只删构建产物目录（miniprogram_npm/<pkg>），安全
    shutil.copytree(src, dst)


def cmd_build_npm(args) -> int:
    """复刻微信「构建 npm」的规则：
    对 package.json 的每个依赖，读取其 package.json 的 `miniprogram` 字段
    （缺省为包根目录），把该目录拷贝到 <project>/miniprogram_npm/<pkg>/。
    这样无需在开发者工具里手动点「工具 → 构建 npm」。
    """
    import json as _json
    proj = Path(args.project)
    pj = proj / "package.json"
    if not pj.exists():
        print(f"未找到 {pj}；请先在项目里 npm install", file=sys.stderr)
        return 2
    deps = (_json.loads(pj.read_text(encoding="utf-8")).get("dependencies") or {})
    if not deps:
        print("package.json 无 dependencies，无需构建")
        return 0
    nm = proj / "node_modules"
    out = proj / "miniprogram_npm"
    out.mkdir(exist_ok=True)
    # 重建前清理孤儿包：miniprogram_npm 下已不在本次依赖列表的目录
    # （只动本工具管理的构建产物目录，防止上次构建的残留混入运行时）
    keep = set(deps)
    for child in list(out.iterdir()):
        if child.is_dir() and child.name not in keep:
            import shutil as _sh
            _sh.rmtree(child)
            print(f"  - 清理孤儿包：{child.name}（已不在 dependencies）")
    ok, fail = [], []
    for pkg in deps:
        src_pkg = nm / pkg
        meta = src_pkg / "package.json"
        if not meta.exists():
            fail.append((pkg, "node_modules 中不存在（先 npm install）"))
            continue
        try:
            m = _json.loads(meta.read_text(encoding="utf-8"))
        except Exception as e:
            fail.append((pkg, f"package.json 解析失败：{e}"))
            continue
        sub = m.get("miniprogram") or "."
        src = src_pkg / sub if sub != "." else src_pkg
        if not src.is_dir():
            fail.append((pkg, f"miniprogram 字段指向的目录不存在：{sub}"))
            continue
        _copytree(src, out / pkg)
        ok.append((pkg, sub))
    print(f"构建完成：成功 {len(ok)} / 失败 {len(fail)}")
    for pkg, sub in ok:
        print(f"  ✓ {pkg:<28} ← {sub}")
    for pkg, why in fail:
        print(f"  ✗ {pkg:<28} {why}")
    if fail:
        return 1
    print(f"\n产物：{out}")
    print("（等价于开发者工具里的「工具 → 构建 npm」；TS 项目另需在 tsconfig.paths 指向 miniprogram_npm）")
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

    p4 = sub.add_parser("scaffold",
                        help="生成专为 automator 测试设计的 TDesign 小程序项目")
    p4.add_argument("--project", default=None, help="项目输出目录（默认状态目录下 demo_project）")
    p4.add_argument("--force", action="store_true", help="目录非空时强制覆盖")
    p4.set_defaults(fn=cmd_scaffold)

    p5 = sub.add_parser("build-npm",
                        help="按微信官方规则手动构建 miniprogram_npm（不依赖开发者工具）")
    p5.add_argument("--project", required=True, help="小程序项目目录（含 package.json）")
    p5.set_defaults(fn=cmd_build_npm)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
