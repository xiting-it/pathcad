"""交付验收：模拟板全流程（等足时序）。"""

import asyncio

from playwright.async_api import async_playwright

URL = "http://127.0.0.1:8765"


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1600, "height": 950})
        errs = []
        pg.on("pageerror", lambda e: errs.append("JS:" + str(e)))
        pg.on("console", lambda m: errs.append("C:" + m.text) if m.type == "error" else None)

        await pg.goto(URL, wait_until="networkidle")
        await pg.wait_for_timeout(3000)
        await pg.evaluate("() => { try{document.getElementById('btnOnboardClose').click()}catch(e){} }")

        # 1. 载入示例
        await pg.evaluate("() => document.getElementById('btnSample').click()")
        await pg.wait_for_timeout(2500)
        print(f"[1] 载入示例: 层数={await pg.input_value('#sumLayers')}")

        # 2. 启动模拟板（先做，避免时序问题）
        await pg.evaluate(
            "() => { const c=document.getElementById('chkMock');"
            " c.checked=true; c.dispatchEvent(new Event('change')); }"
        )
        await pg.wait_for_timeout(4000)
        st = await pg.evaluate(
            "() => ({ connected: window.CONN.connected,"
            " mock: document.getElementById('mockState').textContent })"
        )
        print(f"[2] 模拟板: {st}")

        # 3. 编译
        await pg.evaluate("() => document.getElementById('btnCompile').click()")
        await pg.wait_for_timeout(8000)
        print(f"[3] 编译: {await pg.inner_text('#simStats')}")
        print(f"    发送按钮禁用: {await pg.evaluate('() => document.getElementById(\"btnSend\").disabled')}")

        # 4. 发送
        await pg.evaluate("() => document.getElementById('btnSend').click()")
        await pg.wait_for_timeout(5000)
        job = await pg.evaluate(
            "() => ({ running: window.CONN.jobRunning,"
            " text: document.getElementById('jobText').textContent.slice(0,50) })"
        )
        print(f"[4] 发送中: {job}")

        # 5. 中止
        await pg.evaluate("() => document.getElementById('btnStopJob').click()")
        await pg.wait_for_timeout(3000)
        job2 = await pg.evaluate(
            "() => ({ running: window.CONN.jobRunning,"
            " text: document.getElementById('jobText').textContent.slice(0,50) })"
        )
        print(f"[5] 中止后: {job2}")

        await pg.screenshot(path="_shots/accept-final.png")
        print("errors:", errs if errs else "(none)")
        await b.close()


asyncio.run(main())
