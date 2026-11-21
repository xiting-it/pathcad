# pathCAM 前端 UI 库调研报告

调研对象：把 pathCAM 的 Web 桌面界面（原生 ES Module、零构建、FastAPI 静态服务、PyInstaller 单文件 exe）改造成更成体系的控件。
测量时间：2026-11-16。所有字节数均为实际下载/解包后测量，非估算。

---

## 0. 结论先行

| 库 | 许可证 | 免构建？ | 体积（gzip） | 结论 |
|---|---|---|---|---|
| **Shoelace** 2.20.1 | MIT | ✅ `/cdn` 全预打包 | 5 组件 47.7 KB | **不引**（已停止开发，但技术上是本表最稳的选项） |
| **Web Awesome** 3.14.0 | MIT（Core） | ✅ `dist-cdn` 全预打包 | 5 组件 79.2 KB | **不引**，但有前提缺陷：图标默认走远程 |
| **Lit** 3.3.3 | BSD-3-Clause | ✅ 只消费不用构建 | 全套 ~7.3 KB | **不用单独引**（是上面两家的内部依赖） |
| **htmx** 2.0.11 | 0BSD | ✅ 单个 `.min.js` | 16.8 KB | **不引**，架构不匹配 |
| **Alpine.js** 3.17.5 | MIT | ✅ 单个 `.min.js` | 19.9 KB | **不引**，和现有 app.js 打架 |
| **Tabler Icons** | MIT | ✅ 单文件 SVG | 单图标 562 B | **引**（手抄 ~30 个 SVG 内联） |
| **Lucide** 1.73.0 | ISC | ✅ 单文件 SVG | 单图标 ~400 B | 备选，和 Tabler 二选一 |
| **split.js** 1.6.5 | MIT | ✅ UMD + 单文件 ESM | **2.5 KB** | **引** |
| **golden-layout** 2.6.0 | MIT | ❌ **ESM 浏览器根本加载不了** | 30.8 KB（+esm） | **不引** |
| **dockview-core** 8.9.0 | MIT | ✅ UMD + 单文件 ESM | 93.0 KB | 不引（对两块面板过重） |

**最终清单：split.js（2.5 KB）+ 内联 SVG 图标（~10 KB）+ 手写 details/tooltip。合计新增 < 15 KB gzip。**

---

## 1. 许可证审计（逐字证据）

| 库 | 版本 | SPDX | 原文出处 |
|---|---|---|---|
| @shoelace-style/shoelace | 2.20.1 | `MIT` | package.json `"license": "MIT"` |
| @awesome.me/webawesome | 3.14.0 | `MIT` | package.json `"license": "MIT"` |
| lit | 3.3.3 | `BSD-3-Clause` | package.json `"license": "BSD-3-Clause"` |
| htmx.org | 2.0.11 | `0BSD` | package.json `"license": "0BSD"` |
| alpinejs | 3.17.5 | `MIT` | package.json `"license": "MIT"` |
| @tabler/icons | 3.52.0 | `MIT` | package.json `"license": "MIT"` |
| lucide | 1.73.0 | `ISC` | package.json `"license": "ISC"` |
| split.js | 1.6.5 | `MIT` | package.json `"license": "MIT"` |
| golden-layout | 2.6.0 | `MIT` | package.json `"license": "MIT"` |

**没有一个是 GPL / AGPL / LGPL。商用交付都合法。**

### 原文摘录

Shoelace `LICENSE.md`：
```
Copyright (c) 2020 A Beautiful Site, LLC

Permission is hereby granted, free of charge, to any person obtaining a copy of this software
and associated documentation files (the "Software"), to deal in the Software without restriction,
including without limitation the rights to use, copy, modify, merge, publish, distribute,
sublicense, and/or sell copies of the Software...
```

Web Awesome `LICENSE.md`：
```
Copyright (c) 2025 Fonticons, Inc.

Permission is hereby granted, free of charge, to any person obtaining a copy of this software
... to deal in the Software without restriction ... and/or sell copies of the Software...
```

Web Awesome 官方许可页（webawesome.com/license，Last updated: October 1, 2025）明确写：
> "All parts of Web Awesome Core are provided under the **MIT License**."
> "**Include in commercial and open source projects** without additional permissions"
> "**Self-host** files in your apps and websites"
> "**Embed** in web, mobile, and desktop apps"
> "When distributing any portion of the software, include the copyright and permission notice from the MIT License."

⚠️ 注意区分：Web Awesome **Core** 免费 MIT；**Pro** 是付费订阅（另一个 license）。本报告只用 Core。

htmx `LICENSE`（0BSD，比 MIT 还宽松，**连署名都不需要**）：
```
Zero-Clause BSD
=============

Permission to use, copy, modify, and/or distribute this software for
any purpose with or without fee is hereby granted.
```

Lit `LICENSE`（BSD-3-Clause）：
```
BSD 3-Clause License

Copyright (c) 2017 Google LLC. All rights reserved.
```

split.js README `## License`：
```
Copyright (c) 2019 Nathan Cahill

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction...
```

### ⚠️ 一个隐藏的许可证陷阱

Web Awesome 的 `<wa-icon>` **默认图标库不打包在 npm 包里**，而是运行时从 Font Awesome 的服务器取：

`dist-cdn/chunks/chunk.KKI7M5DP.js` 源码：
```js
var FA_VERSION = "7.3.0";
function getIconUrl(name, family, variant) {
  const iconBase = getIconPath();
  if (iconBase) { return `${iconBase}/${folder}/${name}.svg`; }   // 你自建时走这里
  const kitCode = getKitCode();
  return isPro ? `https://ka-p.fontawesome.com/...` : `https://ka-f.fontawesome.com/releases/v${FA_VERSION}/svgs/${folder}/${name}.svg`;
}
```

两个后果：
1. **离线 exe 会挂**。我实测 `https://ka-f.fontawesome.com/releases/v7.3.0/svgs/solid/gear.svg` → curl 超时（26 秒无响应）。用户的打印机上位机不一定有外网。
2. **Font Awesome Free 图标是 CC BY 4.0，要求署名**。`@fortawesome/fontawesome-free` 的 LICENSE.txt 原文：
   ```
   # Icons: CC BY 4.0 License (https://creativecommons.org/licenses/by/4.0/)
   The Font Awesome Free download is licensed under a Creative Commons
   Attribution 4.0 International License and applies to all icons packaged
   as SVG and JS file types.
   ```
   直接命中了你的硬约束「**不能要求强制署名**」。

对比 Shoelace：**本地自带 2050 个 SVG 图标**（`cdn/assets/icons/`，共 1,591,701 字节），`gear.svg` 实测 HTTP 200 / 1507 字节，完全离线可用，MIT 无署名要求。

---

## 2. 免构建可用性：实测，不是看文档

判断标准不是「文档说能用」，而是「浏览器能不能真的解析模块图」。浏览器解析 ESM 时**不会自动补 `.js` 后缀**，也不认裸模块名（`import x from 'lit'`）。我用一个严格解析器把每个库的入口按浏览器语义走了一遍（不猜后缀、不放行裸说明符）。

### 2.1 结果

| 库 | 入口 | 解析出文件数 | 裸说明符 / 断链 | 判定 |
|---|---|---|---|---|
| Shoelace `/cdn` | `components/button/button.js` 等 | 42 | 0 / 0 | ✅ **严格浏览器安全** |
| Web Awesome `dist-cdn` | `components/button/button.js` 等 | 39 | 0 / 0 | ✅ **严格浏览器安全** |
| split.js | `dist/split.es.js` | 1 | 0 / 0 | ✅ **单文件、零 import** |
| golden-layout | `dist/esm/index.js` | 1 | **20 个断链** | ❌ **浏览器加载不了** |

### 2.2 关键细节：`/cdn` vs `/dist`

Shoelace 官方文档（shoelace.style/getting-started/installation）原文：

> "The `/cdn` files come **pre-bundled**, which means all dependencies are inlined so you do not need to worry about loading additional libraries. The `/dist` files **DO NOT** come pre-bundled."

这个区别是决定性的。我实测 `dist/` 目录里有 **167 处裸说明符**：
```
111 from "lit"
 58 from "@lit/react"
 54 from "lit/decorators.js"
 36 from "lit/directives/class-map.js"
 15 from "lit/directives/if-defined.js"
```
`dist/` 是给打包器用的，**直接扔进浏览器会报 "Failed to resolve module specifier"**。用 `/cdn` 就没事（裸说明符只出现在 `.d.ts` 类型文件里，不参与运行时）。

Web Awesome 同理：`dist-cdn/` 是给 CDN 的，`dist/` 是给打包器的。

### 2.3 golden-layout 为什么不能用

`dist/esm/index.js` 里 20 条 import，全部无扩展名：
```js
export * from './ts/config/config';
export * from './ts/config/resolved-config';
export * from './ts/container/component-container';
...
```
它自己的 `tsconfig` 用 `moduleResolution: bundler`，假定有打包器。实测 CDN 行为：

```
https://cdn.jsdelivr.net/npm/golden-layout@2.6.0/dist/esm/ts/config/config  -> http=404
https://cdn.jsdelivr.net/npm/golden-layout@2.6.0/dist/esm/ts/config/config.js -> http=200
```
浏览器会请求前一个，**404**。全包 136 条 import **无一例外**缺后缀，`dist/` 里也没有 UMD 或 IIFE 构建（`find -name "*umd*" -o -name "*.min.js"` 空结果）。

唯一的免构建路径是 jsDelivr 的 `/+esm` 动态打包：
```
https://cdn.jsdelivr.net/npm/golden-layout@2.6.0/+esm  -> 130,339 字节，0 import ✅
```
但它是**运行时动态生成**的 URL，jsDelivr 官方在文件头写明：
> "Do NOT use SRI with dynamically generated files!"

意味着你没法做完整性校验，而且这个 URL 的哈希以后会变。给一个要打包进 exe、要求确定性交付的桌面软件，这是不可接受的地基。**而且它 2022-09-26 之后再没发过版**（npm `time.modified: 2023-02-21`，dist-tags 只有 2.6.0）。

### 2.4 Lit 到底要不要构建

**不用。** 两个层面：
- **消费 Lit 写的组件**（Shoelace / WA）：Lit 已经内联在它们的 `/cdn`、`dist-cdn` 里了，你一行 Lit 都不用写，也不用装。
- **自己写 Lit 组件**：`import { LitElement, html } from 'https://cdn.jsdelivr.net/npm/lit@3.3.3/+esm'` 可以直接跑，不需要编译器。Lit 的「需要构建」只指 TS、装饰器、模板预编译这些**可选的**优化项——纯 JS 写法零构建。

Lit 运行时实测体积（jsDelivr `+esm`，min 后）：
```
lit-html.js           7,533 字节   gzip 3,414
reactive-element.js   7,802 字节   gzip 3,145
css-tag.js            1,814 字节   gzip 1,071
lit-element.js        1,398 字节   gzip   742
----------------------------------------------
合计                  ~18.5 KB    gzip ~7.3 KB
```
7.3 KB 很便宜，但对你**没有净收益**：Shoelace 的 `<sl-switch>` 一个组件就要 24.9 KB gzip，手写一个 switch 是 40 行 CSS + 10 行 JS。

---

## 3. 体积实测（gzip = level 9，raw = 实际字节）

### 3.1 Shoelace 2.20.1

| 引入范围 | 模块数 | raw | gzip |
|---|---|---|---|
| 只要 button | 27 | 101,032 | **24,908** |
| 5 件套（details/tooltip/dropdown/switch/range） | 48 | 212,122 | **47,692** |
| 5 件套 + icon | 50 | 212,878 | 47,825 |
| + split-panel（6 件 + icon） | 56 | 226,370 | **50,637** |
| 12 件常用 | 87 | 343,712 | 69,369 |
| 全量 `cdn/shoelace.js` | 208 | 764,487 | **150,031** |
| 主题 `themes/dark.css` | — | 19,432 | **4,083** |
| 主题 `themes/light.css` | — | 19,536 | — |
| 图标目录（2050 个 svg） | — | 1,591,701 | — |
| 整个 `cdn/` 目录（544 个 js） | — | 4,274,175 | — |

参考：jsDelivr 把 `cdn/shoelace.js` 再 bundle 一遍是 472,132 字节 / gzip 108,218。

### 3.2 Web Awesome 3.14.0

| 引入范围 | 模块数 | raw | gzip |
|---|---|---|---|
| 5 件套（details/tooltip/dropdown/switch/slider） | 62 | 359,985 | **79,181** |
| + icon + split-panel | 66 | 372,152 | **81,498** |
| 12 件常用 | 95 | 480,925 | 100,716 |
| **全部 73 个组件** | 308 | 1,387,979 | **277,857** |
| 主题 `styles/themes/default.css` | — | 16,773 | **2,610** |
| 全部样式（layers+native+utilities+theme） | — | 55,057 | **9,445** |
| 包体积 `dist-cdn/` | — | 9,152,851 | — |
| 包体积 `dist/` | — | 8,348,932 | — |
| npm 解包总大小 | — | 17,507,552 | — |

Web Awesome 组件目录 416,266 字节，chunks 1,385,591 字节，共 73 个组件目录。

### 3.3 单文件类

| 文件 | raw | gzip | import 数 |
|---|---|---|---|
| `split.js@1.6.5/dist/split.min.js`（UMD） | **6,769** | **2,496** | 0 |
| `split.js@1.6.5/dist/split.es.js`（ESM） | 27,761 | 7,427 | **0** |
| `htmx.org@2.0.11/dist/htmx.min.js` | 52,182 | 16,838 | 0 |
| `alpinejs@3.17.5/dist/cdn.min.js` | 55,935 | 19,914 | 0 |
| `alpinejs@3.17.5/dist/module.esm.min.js` | 55,919 | 19,930 | 0 |
| `lucide@1.73.0/dist/umd/lucide.min.js` | 487,171 | 115,757 | 0 |
| `lucide-static@0.468.0/sprite.svg`（1544 symbol） | 371,613 | 67,088 | — |
| `@tabler/icons-webfont@3.52.0/.../tabler-icons.min.css` | 253,439 | 47,791 | — |
| 单个 Tabler 图标 svg | **562** | — | — |
| 单个 Lucide 图标 svg | 427 | — | — |

### 3.4 golden-layout / dockview

| | raw | gzip |
|---|---|---|
| golden-layout `dist/esm`（33 文件） | 369,007 | 64,994 |
| golden-layout `+esm`（bundle 后单文件） | 130,339 | 30,756 |
| golden-layout `css/goldenlayout-base.css` | 6,276 | 1,297 |
| golden-layout 主题 css（dark） | 3,130 | 874 |
| golden-layout 必需的 11 个 PNG（`dist/img/`） | 1,331 | — |
| dockview-core `dist/dockview-core.min.js`（UMD） | 564,444 | 106,333 |
| dockview-core `dist/package/main.esm.min.mjs`（ESM） | 402,691 | **93,030** |

---

## 4. 逐个评估

### 4.1 Web Awesome（Shoelace 继任者）

**技术合格，但这一条否掉它：图标是远程的。**

- ✅ MIT、零构建（`dist-cdn` 实测严格浏览器安全）、73 个组件、17,507,552 字节全量包里我实际只用 5 个。
- ✅ 组件质量高。`<wa-split-panel>` 自带拖拽分栏、键盘可访问、`snap` 吸附、`--min/--max` CSS 变量。
- ✅ 有 `sl-` 前缀兼容主题（`styles/themes/shoelace.css`，25,092 字节），迁移成本低。
- ❌ **默认图标从 `ka-f.fontawesome.com` 远程取**（已实证），离线 exe 场景直接失败；Font Awesome Free 图标是 CC BY 4.0 需要署名。
- ❌ 自带体系太厚：全量样式 gzip 9,445 字节（`native.css` 一个 37,010 字节），会上你现有 30,172 字节的 `style.css` 打架。
- ⚠️ 5 个组件 79.2 KB gzip + 2.6 KB CSS ≈ 82 KB。塞进 exe 毫无压力，但对「开关/滑块/折叠」这几件事，成本/收益不划算。

**结论：不引。** 如果你最终决定走组件库路线，它是比 Shoelace 更该选的（活跃开发，2026-11-13 还有 commit，1402 stars），但**必须**先 `registerIconLibrary()` 指向本地 SVG，否则离线必挂。

### 4.2 Shoelace

- ✅ MIT，`/cdn` 预打包，实测严格浏览器安全，2050 个本地图标离线可用。
- ✅ 有一个很关键的组件：**`<sl-split-panel>`**——正好是你要的「拖动调整左右面板宽度」，带 `position`/`snap`/`--min`/`--max`/`sl-reposition` 事件。
- ❌ **官方已停止开发**。shoelace.style 顶部横幅原文："**Shoelace Is Sunset with no active development** / Use Web Awesome for ongoing work, issues, and features."
- ❌ 没有 Collapse 公开 API：`split-panel.component.d.ts` 里 `positionBeforeCollapsing` / `isCollapsed` 都是 `private`，只能靠拖到 0 宽度来「折叠」，没有 `collapse()` 方法。
- ⚠️ 5 件套 47.7 KB gzip，比 Web Awesome 便宜 40%，但这个差距对一个 exe 来说无关紧要——**真正的问题是它不再修 bug 了**。

**结论：不引。**

### 4.3 Lit

**结论：不用单独引。** 它是上面两家的内部依赖，已经内联在 `/cdn` 和 `dist-cdn` 里。零构建消费完全可行（7.3 KB gzip），但引入它意味着你要开始写 Web Component，而你现有 112,252 字节的 `app.js` 是 `document.getElementById` + 命令式渲染的风格（`$(` 出现了 203 次），两套范式并存是纯负担。

### 4.4 htmx

- ✅ 0BSD——**全场最宽松的许可证**，连署名都不要求，商用交付零顾虑。
- ✅ 52,182 字节单文件，`<script src>` 直接跑。
- ❌ **架构不匹配**。htmx 的核心模型是「服务端返回 HTML 片段，前端替换 DOM」。你的 FastAPI 有 35 个路由，实测返回的都是 JSON（`return {"ok": True, ...}`、`return {"paths": [], "count": 0}`），前端是 `fetch(...).then(r => r.json())`。
- ❌ 你的核心是 4 张 `<canvas>`（`cv` / `cv3d` / `cvSim` / `cvBottom`）手工绘制。htmx 对 canvas 渲染一点忙都帮不上。

**结论：不引。** 除非你打算把整个后端改成返回 HTML 片段——那是重写，不是改造。

### 4.5 Alpine.js

- ✅ MIT，19,930 字节 gzip，单文件。
- ❌ 和现有代码直接冲突：Alpine 靠 `x-data` / `x-show` / `x-on` 扫描 DOM 接管行为。你已经有 `app.js` 里 `$()` + `addEventListener` 的完整命令式事件体系（`menubar.js`、`panel.js`、`grid.js`、`history.js`、`logview.js` 五个模块）。两套状态源并存，调试地狱。
- ❌ Alpine 的价值在「把交互写进 HTML 属性」，而你 44 个表单控件（15 个 `type="number"`、14 个 `type="range"`、8 个 `type="checkbox"`）已经有 `panel.js` 在管了（`GeoPanel._bind()` 里的 `blur`/`keydown` 逻辑）。

**结论：不引。**

### 4.6 图标库：Tabler Icons vs Lucide

两个都是 MIT/ISC 纯 SVG，免构建天然成立——**因为图标就是文件，不存在「库」的概念**。区别只在你怎么用：

| | Tabler 3.52.0 | Lucide 1.73.0 / lucide-static 0.468.0 |
|---|---|---|
| 许可证 | MIT | ISC |
| 单图标 | 562 字节（实测 `icons/outline/photo.svg`） | 427 字节 |
| 全量 UMD | — | 487,171 / gzip 115,757 |
| Sprite（`<symbol>`） | ❌ CDN 上 404（`tabler-sprite.svg` 不在 npm 包，`/docs/sprite.mdx` 只是文档） | ✅ `sprite.svg` 371,613 字节 / gzip 67,088 / 1544 个 symbol |
| 图标数量 | 12,060 个文件 | 4,110 个 esm 图标 |
| 风格 | 24px、`stroke-width` 2、`currentColor` | 同规格，`class="lucide lucide-camera"` |

**推荐：Tabler**（或 Lucide，随便挑一个，别两个都上）。选 Tabler 的理由是它的 `outline` 风格和你在 `style.css` 里已经用的细线条 UI（`.vsep { width: 1px }`、`border-radius: 2px`）更搭。

**关键用法：不要 `<script>` 引 UMD。** Lucide UMD 是 115 KB gzip——你只会用到 30 个图标，为 30 个图标付 115 KB 是浪费。正确做法是**把用到的十几个 SVG 内联成 JS 字符串**：

```js
// web/icons.js  —— ~10 KB raw，gzip 后 ~3 KB
export const ICONS = {
  play:  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 4l14 8-14 8z"/></svg>',
  pause: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="7" y="4" width="3" height="16"/><rect x="14" y="4" width="3" height="16"/></svg>',
  // ...
};
```

你现在是 `🔒` / `🔓` / `▶` 这几个 emoji（`index.html` 1 个 `▶`、1 个 `🔒`；`panel.js` 各 1 个）——emoji 在不同 Windows 版本渲染不一致，换成 SVG 顺带解决了这个。

### 4.7 其他桌面级布局库

| 库 | 版本 | 许可证 | 免构建 | gzip | 评价 |
|---|---|---|---|---|---|
| **split.js** | 1.6.5 | MIT | ✅ UMD + 单文件 ESM | **2.5 KB** | **推荐** |
| golden-layout | 2.6.0 | MIT | ❌ | 30.8 KB | 否决（见 2.3） |
| dockview-core | 8.9.0 | MIT | ✅ UMD + ESM 单文件 | 93.0 KB | 否决（功能过剩） |
| @floating-ui/dom | 1.8.0 | MIT | 需看包 | — | 仅在手写 tooltip 做定位时才需要，通常不如 CSS 定位 |

`dockview-core` 是 golden-layout 的现代替代品（有 UMD、有单文件 ESM、CSS 自注入），但它是**完整的 IDE 式停靠系统**：拖拽标签页、浮动窗口、最大化、序列化布局。你只要「拖一条分割线」，用它是杀鸡用牛刀，93 KB gzip 换不来任何你要的东西。

---

## 5. 场景映射：你要的 7 件事，哪些引库、哪些手写

| 需求 | 现状 | 建议 | 理由 |
|---|---|---|---|
| **分栏可拖拽** | 硬编码 `grid-template-columns: 178px 1fr 272px`（`style.css:155`） | **引 split.js（2.5 KB）** | 手写要处理 pointer capture、触摸、键盘无障碍、双击复位、最小/最大约束、`ResizeObserver`——split.js 全给你了，而且它只有 2.5 KB。这是全场性价比最高的一笔。 |
| **面板折叠** | 无（只有 `toggleLogs()` 整体切换） | **手写**，用原生 `<details>` | `<details>/<summary>` 是浏览器原生折叠，零 JS、自带键盘支持和无障碍语义。Shoelace 的 `<sl-details>` 成本 24.9 KB gzip，换来的只是样式统一。你的 `style.css` 已经有 `.sectionHead` / `.sectionLabel` 体系，加 20 行 CSS 就够。 |
| **图标** | emoji（`🔒`/`🔓`/`▶`） | **内联 SVG 字符串**（~3 KB gzip） | 见 4.6。别引 UMD。 |
| **tooltip** | `title=` 属性，30 处 | **手写** CSS + 一个 `data-tip` 属性选择器 | 原生 `title` 有 1 秒延迟且不可样式化。一个 `[data-tip]:hover::after` 规则 + `position: absolute` 大约 15 行。引 tippy.js（6.3.7，2 MB 解包）只为了这个不值。**前提**：你的 tooltip 都不靠近视口边缘。如果要做智能翻转定位，才考虑 `@floating-ui/dom`。 |
| **下拉菜单** | 已有完整实现（`menubar.js` 4,314 字节，`data-menu` / `menuTitle` / `menuDrop`） | **不引，保持现状** | 你已经有 Word/WPS 风格菜单栏了，功能是完整的。任何库替换都是纯倒退。 |
| **开关（switch）** | 8 个 `type="checkbox"` | **手写** | 一个 `<input type="checkbox">` + `appearance: none` + `::before` 就是 switch，约 30 行 CSS。`<sl-switch>` 要 24.9 KB gzip。**这是最典型的「手写更划算」。** |
| **滑块（slider）** | 14 个 `type="range"` | **手写** | 同上。`style.css:277` 附近你已经在给 `input[type=range]` 写 `::-webkit-slider-thumb` 了，说明原生控件自定义完全可行。`<sl-range>` 又是一笔 24.9 KB。 |

**一句话总结场景**：你的界面是「菜单栏 + 三栏布局 + canvas 画布 + 表单侧栏」，本质是**桌面应用布局问题**，不是**组件问题**。这里面唯一真正难手写的是**可拖拽分栏**（split.js 2.5 KB 解决），其余都能用原生 HTML + CSS 搞定。

---

## 6. split.js vs golden-layout 深度对比

这是问题 5 的核心，单独展开。

### split.js 1.6.5 —— 推荐

**免构建：完美。** 两个文件都能直接用：
- `https://cdn.jsdelivr.net/npm/split.js@1.6.5/dist/split.min.js` — 6,769 字节 / gzip 2,496，**UMD**（`!function(e,t){...typeof exports...define.amd...(e=globalThis).Split=t()}`），`<script src>` 一行搞定。
- `https://cdn.jsdelivr.net/npm/split.js@1.6.5/dist/split.es.js` — 27,761 字节 / gzip 7,427，**ESM，0 个 import**，`import Split from '/static/vendor/split/split.es.js'` 直接可用。

**许可证：MIT**，`Copyright (c) 2019 Nathan Cahill`，无署名要求（MIT 只要求保留版权声明）。

**体积：2.5 KB gzip。** 全场最便宜。

**功能恰好覆盖你的需求：**
```js
Split(['#left', '#main', '#right'], {
  sizes: [178, null, 272],   // 初始化成你现在的宽度
  minSize: [0, 400, 0],      // 左/右可拖到 0 = 折叠
  gutterSize: 6,
  snapOffset: 20,            // 接近 0 时吸附 = 折叠手感
  onDragEnd: () => { GRID?.resize(); resizeAll(); },  // 关键：通知 canvas 重算
});
```
最后那个 `onDragEnd` 是你这项目最需要的——`app.js:143` 已经有 `resizeAll()` 在 `window.resize` 时调 `GRID.resize()` 和 4 个 canvas 的 `resizeOne()`。分栏拖动结束后不重算，canvas 会变形。这条比任何 UI 特性都重要。

**注意点**：split.js 只管拖拽，**不管折叠按钮**。折叠你要自己写：
```js
const sizes = Split.getSizes?.() // 或自己记状态
// 折叠：Split(['#left','#main','#right'], {sizes:[0,null,272]})
```
但既然 `minSize: [0, ...]` + `snapOffset` 已经能让用户拖到 0，再配一个「双击 gutter 复位」就够了。

### golden-layout 2.6.0 —— 否决

**免构建：不可行。** 已在 2.3 证明：`dist/esm` 的 136 条 import 全部无扩展名，CDN 返回 404；包里没有 UMD/IIFE 构建；唯一可行路径是 jsDelivr `/+esm` 运行时动态打包（130,339 字节），但官方禁止对动态文件用 SRI。

**维护状态：** 最后版本 2022-09-26，`time.modified` 停在 2023-02-21。**三年半没更新。**

**体积：** ESM 源 369,007 字节 / gzip 64,994；`+esm` bundle 130,339 / gzip 30,756。**是 split.js 的 12～26 倍。**

**额外负担：** `css/goldenlayout-base.css` 6,276 字节 + 主题 3,130 字节，且主题 CSS 硬引用 **11 个 PNG**：
```
url('../../img/lm_close_white.png')     url('../../img/lm_maximise_white.png')
url('../../img/lm_minimize_white.png')  url('../../img/lm_popin_white.png')
url('../../img/lm_popout_white.png')
```
这些都得跟着进 PyInstaller 的 `datas`，而且要保证相对路径正确（`dist/img/` 共 1,331 字节）。你现在的 `pathCAM.spec` 里 `datas=[('web','web'), ...]` 只映射了顶层目录，多一层 vendor 目录要自己想清楚路径。

**功能：严重过剩。** golden-layout 是 IDE 级停靠系统：标签页、浮动窗口、拖出成独立 browser window（`BrowserPopout`）、布局序列化成 JSON（`layout-manager.js` 就有 68,930 字节）。你的需求是「左边 178px 能拖宽点，右边 272px 能拖窄点」。用它是用航空母舰送快递。

**结论：不值得。** 哪怕它能免构建也不值得——30 KB gzip + 11 个 PNG + 一套标签页/浮动窗口的心智模型，换 2.5 KB 就能解决的拖拽条。

### 那如果要「可拖拽 + 可停靠 + 标签页」呢

如果哪天你真要做 Photoshop 式的多面板停靠，选 **dockview-core 8.9.0**（MIT，有 UMD `dockview-core.min.js` 564,444 字节 / gzip 106,333，也有单文件 ESM `main.esm.min.mjs` 402,691 / gzip 93,030，CSS 自注入），不要选 golden-layout。但**现在不要**。

---

## 7. 最终推荐清单

### ✅ 该引入

| 项 | 版本 | 许可证 | 体积 | 怎么用 |
|---|---|---|---|---|
| **split.js** | 1.6.5 | MIT | 2.5 KB gzip | `dist/split.es.js` 单文件放入 `web/vendor/split/`，`import Split from '/static/vendor/split/split.es.js'` |
| **Tabler 图标（内联 SVG）** | 3.52.0 | MIT | ~3 KB gzip（30 个） | 不自带库，复制 SVG path 到 `web/icons.js` |
| 原生 `<details>` | — | — | 0 | 面板折叠，零 JS |

**合计新增：< 6 KB gzip。** 相比你现有 `web/` 的 213,467 字节，几乎不可见。

### ❌ 不该引入

| 项 | 理由 |
|---|---|
| **Web Awesome** | 图标默认远程 Font Awesome（离线 exe 必挂 + CC BY 4.0 要署名）；5 组件 79.2 KB gzip 换 3 个能 30 行手写的控件 |
| **Shoelace** | 官方已 sunset，不再修 bug；5 组件 47.7 KB |
| **Lit** | 是上面两家的内部依赖，已内联；单独引入意味着引入第二套渲染范式 |
| **htmx** | 0BSD 最好，但你要的是 JSON + canvas，htmx 的 HTML 片段模型用不上 |
| **Alpine.js** | 与 `app.js` 的命令式事件体系正面冲突 |
| **golden-layout** | ESM 浏览器加载不了、无 UMD、3 年半未更新、功能过剩 12 倍 |
| **dockview-core** | 93 KB gzip 的 IDE 停靠系统，杀鸡用牛刀 |
| **lucide UMD** | 115 KB gzip 只为 30 个图标；用单文件 SVG 即可 |

### 落地方案（针对你的 PyInstaller 现状）

1. `web/vendor/split/split.es.js` ← `split.js@1.6.5/dist/split.es.js`（27,761 字节）
2. `web/vendor/LICENSE-split.txt` ← MIT 原文（MIT 要求保留版权声明，这是唯一强制项）
3. `web/icons.js` ← 手抄的 SVG
4. `index.html` 的 `.layout` 从 `grid-template-columns: 178px 1fr 272px` 改成三栏 + 两个 gutter 的 flex 结构，split.js 接管宽度
5. `app.js` 的 `resizeAll()` 挂到 split.js 的 `onDragEnd`
6. `pathCAM.spec` **不用改**——`datas=[('web','web')]` 已经覆盖 `web/vendor/` 整个子树

**注意**：`web/` 通过 `app.mount("/static", StaticFiles(directory=str(WEB)))`（`server.py:984`）暴露，所以文件路径就是 `/static/vendor/split/split.es.js`。split.js 零 import，不涉及相对路径解析，不会踩到 `import.meta.url` 的坑。

---

## 8. 三条容易踩的坑

1. **千万别用 `dist/` 目录**。Shoelace 和 Web Awesome 的 `dist/` 里有 167 处裸说明符（`from "lit"`），浏览器会直接报 `Failed to resolve module specifier`。CDN 场景只能用 `/cdn`（Shoelace）或 `dist-cdn`（Web Awesome）。这个区别在官方文档里写了，但很容易被「我抄了 npm 路径」搞错。

2. **CDN 不是可选项**。你的 exe 是单文件、离线运行、用户机器上不一定有网。所有依赖必须 vendored 进 `web/vendor/`，`<script src="https://cdn...">` 只用于调研阶段的验证。Web Awesome 的远程图标就是最好的反例：默认配置下 `wa-icon` 会去 `ka-f.fontawesome.com` 拉 SVG，断网就是空白方块。

3. **别给 canvas 加组件库的 ResizeObserver**。Web Awesome / Shoelace 的组件自带 ResizeObserver 和 Shadow DOM。你的 4 张 canvas 是手工 `resizeOne(c)` 计算 `devicePixelRatio` 的（`app.js:133`），塞进 Shadow DOM 里 `getBoundingClientRect()` 的行为和尺寸观测时机都会变，容易出「canvas 模糊」或「宽高不跟手」。布局层（split.js）和渲染层（canvas）保持解耦。

---

## 附：本次调研的复现方法

```bash
# 许可证
curl -s https://registry.npmjs.org/<pkg>/latest | node -e "...console.log(j.license)"

# 体积（真实 gzip，不是 header）
curl -sSL -o f.js <cdn-url> && wc -c < f.js && gzip -9 -c f.js | wc -c

# 免构建可用性（关键：严格按浏览器语义解析，不猜后缀、不放行裸说明符）
# 见正文 2.1 的 strict resolver
```
