"""从交付包导入示例模型并截图，确认 3D 预览正常。"""

import asyncio
import pathlib

from playwright.async_api import async_playwright


async def main() -> None:
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1600, "height": 950})
        errs: list[str] = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("console", lambda m: errs.append("C:" + m.text) if m.type == "error" else None)

        await pg.goto("http://127.0.0.1:8765", wait_until="networkidle")
        await pg.wait_for_timeout(2500)
        await pg.evaluate("() => { try{document.getElementById('btnOnboardClose').click()}catch(e){} }")

        f = str(pathlib.Path("dist/示例模型/05-格栅立方.stl").resolve())
        await pg.set_input_files("#fileMesh", f)
        await pg.wait_for_timeout(8000)

        await pg.evaluate("() => document.querySelector('.vtab[data-view=\"3d\"]').click()")
        await pg.wait_for_timeout(2500)
        print("[3] 3D 层滑块:", await pg.inner_text("#layerLabel"))
        await pg.screenshot(path="_shots/dist-sample-3d.png")

        # 再试齿轮并填充编译
        await pg.evaluate("() => document.getElementById('btnNew').click()")
        await pg.wait_for_timeout(800)
        f2 = str(pathlib.Path("dist/示例模型/02-齿轮.stl").resolve())
        await pg.set_input_files("#fileMesh", f2)
        await pg.wait_for_timeout(7000)
        await pg.evaluate("() => document.getElementById('btnFillAll').click()")
        await pg.wait_for_timeout(12000)
        await pg.evaluate("() => document.getElementById('btnCompile').click()")
        await pg.wait_for_timeout(15000)
        print("[4] 齿轮编译:", await pg.inner_text("#simStats"))
        print("[5] 警告:", (await pg.inner_text("#warnings"))[:40] or "(无)")
        await pg.evaluate("() => document.querySelector('.vtab[data-view=\"3d\"]').click()")
        await pg.wait_for_timeout(2500)
        await pg.screenshot(path="_shots/dist-gear-3d.png")

        print("errors:", errs if errs else "(none)")
        await b.close()


asyncio.run(main())
