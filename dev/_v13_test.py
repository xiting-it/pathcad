"""v1.3 剩余 5 项需求验证（全部用 JS 驱动，避开遮挡问题）。"""

import asyncio

from playwright.async_api import async_playwright

URL = "http://127.0.0.1:8765"


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1500, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)

        await pg.goto(URL, wait_until="networkidle")
        await pg.wait_for_timeout(2500)
        await pg.evaluate("() => { try{document.getElementById('btnOnboardClose').click()}catch(e){} }")
        await pg.wait_for_timeout(500)

        print("=" * 62)
        print("[3] 线间距预设")
        print("=" * 62)
        opts = await pg.evaluate(
            '() => [...document.querySelectorAll("#fillSpacingPreset option")].map(o=>o.value)'
        )
        print(f"    预设项: {opts}")

        for val in ["2", "0.5", "custom", "1"]:
            script = (
                "() => { const e=document.getElementById('fillSpacingPreset');"
                " e.value='" + val + "'; e.dispatchEvent(new Event('change')); }"
            )
            await pg.evaluate(script)
            await pg.wait_for_timeout(300)
            v = await pg.evaluate('() => document.getElementById("fillSpacing").value')
            cv = await pg.evaluate(
                '() => !document.getElementById("paramSpacingCustom").classList.contains("hidden")'
            )
            print(f"    选 {val:8s} -> fillSpacing={v}, 自定义滑块可见={cv}")

        print()
        print("=" * 62)
        print("[6] 扩充绘图工具")
        print("=" * 62)
        tools = await pg.evaluate(
            '() => [...document.querySelectorAll(".tool")].map(b=>b.dataset.tool)'
        )
        print(f"    工具({len(tools)}): {tools}")

        await pg.evaluate("() => { document.querySelectorAll('details').forEach(d => d.open = false); }")
        await pg.evaluate("() => document.getElementById('btnNew').click()")
        await pg.wait_for_timeout(800)

        box = await pg.locator("#cv").bounding_box()
        midx = box["x"] + box["width"] / 2
        midy = box["y"] + box["height"] / 2

        plans = [
            ("point", 0, 0, None),
            ("line", -60, -50, (-20, -20)),
            ("rect", 0, -60, (40, -20)),
            ("circle", 70, -60, (90, -40)),
            ("ellipse", -70, 20, (-30, 50)),
            ("polygon", -20, 30, (0, 55)),
            ("diamond", 30, 20, (60, 50)),
            ("star", 75, 25, (95, 50)),
        ]
        for name, ox, oy, end in plans:
            sel = ".tool[data-tool=\"" + name + "\"]"
            await pg.evaluate("(s) => document.querySelector(s).click()", sel)
            await pg.wait_for_timeout(250)
            cx, cy = midx + ox * 3, midy + oy * 3
            if end is None:
                await pg.mouse.click(cx, cy)
            else:
                await pg.mouse.move(cx, cy)
                await pg.mouse.down()
                await pg.mouse.move(midx + end[0] * 3, midy + end[1] * 3, steps=8)
                await pg.mouse.up()
            await pg.wait_for_timeout(450)
            n = await pg.evaluate('() => document.querySelectorAll(".shapeItem").length')
            print(f"    画 {name:8s} -> 形状数 {n}")

        names = await pg.evaluate(
            '() => window.S.project.layers[0].strokes.map(s => s._name || s.source?.type)'
        )
        print(f"    实际图元: {names}")

        print()
        print("=" * 62)
        print("[7] 尺寸坐标面板")
        print("=" * 62)
        await pg.evaluate("() => document.querySelectorAll('.shapeItem')[2].click()")
        await pg.wait_for_timeout(600)
        geo = await pg.evaluate("() => ({x:geoX.value,y:geoY.value,w:geoW.value,h:geoH.value})")
        print(f"    选中矩形，读到: {geo}")

        for fid, val in [("geoX", "50"), ("geoY", "50"), ("geoW", "30"), ("geoH", "15")]:
            script = (
                "() => { const e=document.getElementById('" + fid + "');"
                " e.value='" + val + "'; e.dispatchEvent(new Event('blur')); }"
            )
            await pg.evaluate(script)
            await pg.wait_for_timeout(400)
        geo2 = await pg.evaluate("() => ({x:geoX.value,y:geoY.value,w:geoW.value,h:geoH.value})")
        print(f"    改为 30x15 @ (50,50) 后: {geo2}")

        real = await pg.evaluate("""() => {
            const st = window.S.project.layers[0].strokes[2];
            let minx=1e9,miny=1e9,maxx=-1e9,maxy=-1e9;
            for (const p of st.points) {
                minx=Math.min(minx,p.x); maxx=Math.max(maxx,p.x);
                miny=Math.min(miny,p.y); maxy=Math.max(maxy,p.y);
            }
            return {w:+(maxx-minx).toFixed(2), h:+(maxy-miny).toFixed(2),
                    cx:+((minx+maxx)/2).toFixed(2), cy:+((miny+maxy)/2).toFixed(2)};
        }""")
        print(f"    实际几何验证: {real}")

        print()
        print("=" * 62)
        print("[5] 延时覆盖到全部起点/终点")
        print("=" * 62)
        n0 = await pg.evaluate("() => window.S.project.layers[0].strokes.length")
        print(f"    路径数: {n0}")

        await pg.evaluate("""() => {
            const e = document.getElementById('pRise');
            e.value = '200'; e.dispatchEvent(new Event('input'));
            document.getElementById('btnApplyRise').click();
        }""")
        await pg.wait_for_timeout(800)
        starts = await pg.evaluate(
            "() => window.S.project.layers[0].strokes.map(s=>s.start.delay_ms)"
        )
        print(f"    起点延时: {starts}")

        await pg.evaluate("""() => {
            const e = document.getElementById('pTail');
            e.value = '150'; e.dispatchEvent(new Event('input'));
            document.getElementById('btnApplyTail').click();
        }""")
        await pg.wait_for_timeout(800)
        ends = await pg.evaluate(
            "() => window.S.project.layers[0].strokes.map(s=>s.end.delay_ms)"
        )
        print(f"    终点延时: {ends}")

        print()
        print("=" * 62)
        print("[1] 机器状态页")
        print("=" * 62)
        await pg.evaluate("() => document.querySelector('.vtab[data-view=\"machine\"]').click()")
        await pg.wait_for_timeout(1500)

        cells = await pg.evaluate("""() => ({
            out: document.querySelectorAll('#ioOutGrid .ioCell').length,
            inp: document.querySelectorAll('#ioInGrid .ioCell').length,
            org: document.querySelectorAll('#ioOrgGrid .ioCell').length,
        })""")
        print(f"    IO 格子: {cells}")

        await pg.evaluate("() => { const c=document.getElementById('chkMock'); c.checked=true; c.dispatchEvent(new Event('change')); }")
        await pg.wait_for_timeout(3000)

        await pg.evaluate("() => document.getElementById('btnPollIO').click()")
        await pg.wait_for_timeout(2000)
        raw = await pg.inner_text("#ioRaw")
        print(f"    原始回包: {raw[:90]}")
        on = await pg.evaluate("() => document.querySelectorAll('#ioOutGrid .ioCell.on').length")
        print(f"    OUT 亮格数: {on}")
        logs = await pg.evaluate("() => document.querySelectorAll('#machineLogBody .logLine').length")
        print(f"    通讯日志: {logs} 行")

        await pg.evaluate("() => document.querySelector('[data-io=\"M121 V255\"]').click()")
        await pg.wait_for_timeout(1500)
        await pg.evaluate("() => document.getElementById('btnPollIO').click()")
        await pg.wait_for_timeout(1500)
        on2 = await pg.evaluate("() => document.querySelectorAll('#ioOutGrid .ioCell.on').length")
        print(f"    发 M121 V255 后 OUT 亮格数: {on2}")

        await pg.screenshot(path="_shots/v13-machine.png")

        print()
        print("=" * 62)
        print("编译验证")
        print("=" * 62)
        await pg.evaluate("() => document.getElementById('btnCompile').click()")
        await pg.wait_for_timeout(6000)
        print(f"    {await pg.inner_text('#simStats')}")
        w = await pg.inner_text("#warnings")
        print(f"    警告: {w[:120]}")

        print()
        print("errors:", errs if errs else "(none)")
        await b.close()


asyncio.run(main())
