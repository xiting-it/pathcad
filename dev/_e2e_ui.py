"""端到端验证（走 UI）：画方块 → 填充 → 生成 → 发送到真板子。"""

import asyncio
import time

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
        await pg.wait_for_timeout(2000)
        try:
            await pg.click("#btnOnboardClose", timeout=2000)
        except Exception:
            pass

        # ── 新建 + 画方块 ──
        await pg.click("#btnNew")
        await pg.wait_for_timeout(700)
        await pg.click(".tool[data-tool='rect']")
        box = await pg.locator("#cv").bounding_box()
        cx, cy = box["x"] + box["width"] * 0.35, box["y"] + box["height"] * 0.3
        await pg.mouse.move(cx, cy)
        await pg.mouse.down()
        await pg.mouse.move(cx + 200, cy + 200, steps=10)
        await pg.mouse.up()
        await pg.wait_for_timeout(600)
        print(f"[1] 画方块: {await pg.evaluate('() => document.querySelectorAll(\".shapeItem\").length')} 个")

        # ── 套用格栅参数（8mm）──
        await pg.click("#btnLatticePreset")
        await pg.wait_for_timeout(1000)
        print(f"[2] 预设: 间距={await pg.input_value('#fillSpacing')}")

        # ── 填充 ──
        await pg.evaluate('() => document.querySelectorAll(".shapeItem")[0].click()')
        await pg.wait_for_timeout(400)
        await pg.click("#btnFill")
        await pg.wait_for_timeout(2500)
        n = await pg.evaluate('() => document.querySelectorAll(".shapeItem").length')
        print(f"[3] 填充: {n} 个形状")

        # ── 生成 G-code（F1 方言）──
        await pg.click("#btnCompile")
        await pg.wait_for_timeout(5000)
        stat = await pg.inner_text("#simStats")
        print(f"[4] 编译: {stat}")

        # 检查 G-code 里有没有非法指令
        await pg.click(".btab[data-bview='gcode']")
        await pg.wait_for_timeout(500)
        gcode = await pg.inner_text("#gcodeBox")
        lines = [l.strip() for l in gcode.splitlines() if l.strip() and not l.strip().startswith(";")]
        illegal = [l for l in lines if l.split()[0].upper() not in ("G00", "G01", "G02", "G28", "M50")]
        print(f"[5] G-code {len(lines)} 行, 非法指令: {illegal if illegal else '无'}")

        # ── 连接板子 ──
        await pg.click("#btnConnect")
        await pg.wait_for_timeout(2500)
        print(f"[6] 连接: {await pg.inner_text('#connMsg')}")

        # ── 发送 ──
        await pg.click("#btnSend")
        await pg.wait_for_timeout(1500)
        txt = ""
        for _ in range(120):
            await pg.wait_for_timeout(500)
            txt = await pg.inner_text("#jobText")
            if "完成" in txt or "中止" in txt:
                break
        print(f"[7] 发送: {txt.replace(chr(10), ' | ')[:120]}")

        # ── 最终位置 ──
        await pg.fill("#manualCmd", "M50")
        await pg.click("#btnManualSend")
        await pg.wait_for_timeout(1200)
        print(f"[8] 最终位置: {await pg.inner_text('#manualResult')}")

        await pg.screenshot(path="_shots/98-e2e-board.png")
        print("errors:", errs if errs else "(none)")
        await b.close()


asyncio.run(main())
