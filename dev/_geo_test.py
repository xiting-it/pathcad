"""⑦ 尺寸面板的真实交互测试：模拟用户逐框输入 + 回车。"""

import asyncio

from playwright.async_api import async_playwright

URL = "http://127.0.0.1:8765"


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1500, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))

        await pg.goto(URL, wait_until="networkidle")
        await pg.wait_for_timeout(2500)
        await pg.evaluate("() => { try{document.getElementById('btnOnboardClose').click()}catch(e){} }")
        await pg.wait_for_timeout(400)

        # 画一个矩形
        await pg.evaluate("() => { document.querySelectorAll('details').forEach(d=>d.open=false); }")
        await pg.evaluate("() => document.getElementById('btnNew').click()")
        await pg.wait_for_timeout(700)
        box = await pg.locator("#cv").bounding_box()
        cx = box["x"] + box["width"] / 2
        cy = box["y"] + box["height"] / 2
        await pg.evaluate("() => document.querySelector('.tool[data-tool=\"rect\"]').click()")
        await pg.mouse.move(cx, cy)
        await pg.mouse.down()
        await pg.mouse.move(cx + 150, cy + 120, steps=8)
        await pg.mouse.up()
        await pg.wait_for_timeout(600)

        geo = await pg.evaluate("() => ({x:geoX.value,y:geoY.value,w:geoW.value,h:geoH.value})")
        print(f"初始: {geo}")

        # ── 测试 A：关闭比例锁，改尺寸为 30x15 ──
        lockOn = await pg.evaluate(
            "() => document.getElementById('btnLockRatio').classList.contains('active')"
        )
        print(f"比例锁默认: {lockOn}")
        if lockOn:
            await pg.evaluate("() => document.getElementById('btnLockRatio').click()")
            await pg.wait_for_timeout(200)

        # 逐框：点进去 → 全选 → 输值 → 回车
        for fid, val in [("geoW", "30"), ("geoH", "15")]:
            await pg.click(f"#{fid}")
            await pg.keyboard.press("Control+A")
            await pg.keyboard.type(val)
            await pg.keyboard.press("Enter")
            await pg.wait_for_timeout(500)

        real = await pg.evaluate("""() => {
            const st = window.S.project.layers[0].strokes[0];
            let minx=1e9,miny=1e9,maxx=-1e9,maxy=-1e9;
            for (const p of st.points) {
                minx=Math.min(minx,p.x); maxx=Math.max(maxx,p.x);
                miny=Math.min(miny,p.y); maxy=Math.max(maxy,p.y);
            }
            return {w:+(maxx-minx).toFixed(2), h:+(maxy-miny).toFixed(2),
                    cx:+((minx+maxx)/2).toFixed(2), cy:+((miny+maxy)/2).toFixed(2)};
        }""")
        print(f"解锁改 30x15 后实际: {real}")

        # ── 测试 B：改中心到 (50, 50) ──
        for fid, val in [("geoX", "50"), ("geoY", "50")]:
            await pg.click(f"#{fid}")
            await pg.keyboard.press("Control+A")
            await pg.keyboard.type(val)
            await pg.keyboard.press("Enter")
            await pg.wait_for_timeout(500)

        real2 = await pg.evaluate("""() => {
            const st = window.S.project.layers[0].strokes[0];
            let minx=1e9,miny=1e9,maxx=-1e9,maxy=-1e9;
            for (const p of st.points) {
                minx=Math.min(minx,p.x); maxx=Math.max(maxx,p.x);
                miny=Math.min(miny,p.y); maxy=Math.max(maxy,p.y);
            }
            return {w:+(maxx-minx).toFixed(2), h:+(maxy-miny).toFixed(2),
                    cx:+((minx+maxx)/2).toFixed(2), cy:+((miny+maxy)/2).toFixed(2)};
        }""")
        print(f"改中心 (50,50) 后实际: {real2}")

        # ── 测试 C：开启比例锁，改宽看高是否按比例变 ──
        await pg.evaluate("() => document.getElementById('btnLockRatio').click()")
        await pg.wait_for_timeout(300)
        await pg.click("#geoW")
        await pg.keyboard.press("Control+A")
        await pg.keyboard.type("60")
        await pg.keyboard.press("Enter")
        await pg.wait_for_timeout(600)

        real3 = await pg.evaluate("""() => {
            const st = window.S.project.layers[0].strokes[0];
            let minx=1e9,miny=1e9,maxx=-1e9,maxy=-1e9;
            for (const p of st.points) {
                minx=Math.min(minx,p.x); maxx=Math.max(maxx,p.x);
                miny=Math.min(miny,p.y); maxy=Math.max(maxy,p.y);
            }
            return {w:+(maxx-minx).toFixed(2), h:+(maxy-miny).toFixed(2)};
        }""")
        print(f"锁比例改宽=60 后: {real3}（宽高比应保持 30:15 = 2:1）")

        await pg.screenshot(path="_shots/v13-geo.png")
        print("errors:", errs if errs else "(none)")
        await b.close()


asyncio.run(main())
