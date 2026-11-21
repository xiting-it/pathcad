import asyncio

from playwright.async_api import async_playwright


async def main() -> None:
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1600, "height": 950})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.goto("http://127.0.0.1:8765", wait_until="networkidle")
        await pg.wait_for_timeout(2500)
        try:
            await pg.click("#btnOnboardClose", timeout=1500)
        except Exception:
            pass

        # 打开机器设置 → 勾 X 反转 + Z 行程改 8
        await pg.evaluate("() => document.querySelector('[data-act=machineSettings]').click()")
        await pg.wait_for_timeout(500)
        await pg.check("#msInvX")
        await pg.fill("#msTravelZ", "8")
        await pg.click("#btnMachineSave")
        await pg.wait_for_timeout(600)

        m = await pg.evaluate(
            "() => ({ix: S.project.machine.invert_x, tz: S.project.machine.travel_z})"
        )
        print("[1] 机器设置:", m)

        # 画矩形
        await pg.evaluate("() => document.querySelector('.tool[data-tool=\"rect\"]').click()")
        box = await pg.locator("#cv").bounding_box()
        cx = box["x"] + box["width"] * 0.4
        cy = box["y"] + box["height"] * 0.4
        await pg.mouse.move(cx, cy)
        await pg.mouse.down()
        await pg.mouse.move(cx + 120, cy + 80, steps=8)
        await pg.mouse.up()
        await pg.wait_for_timeout(600)

        await pg.evaluate("() => document.getElementById('btnCompile').click()")
        await pg.wait_for_timeout(6000)

        g = await pg.evaluate(
            "() => (S.lastCompile?.gcode || '').split(String.fromCharCode(10))"
        )
        moves = [ln for ln in g if ln.startswith("G0 Z") or ln.startswith("G0 X")]
        print("[2] 移动指令:", moves[:4])
        print("[3] 是否还有 Z100:", any("Z100" in ln for ln in g))
        print("[4] X 坐标是否全为负:", all(
            "-" in ln.split("X")[1].split()[0] for ln in moves if "X" in ln
        ) if any("X" in ln for ln in moves) else "无X指令")

        print("errors:", errs if errs else "(none)")
        await b.close()


asyncio.run(main())
