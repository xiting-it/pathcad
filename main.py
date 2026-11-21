"""CAM-B 启动入口（给 PyInstaller 打包用）。

与 start.bat 的区别：
- 自动找一个空闲端口（8765 被占也不怕）
- 启动后自动打开浏览器
- 打包成 exe 后，web/ 和 docs/ 从 sys._MEIPASS 读取
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser

from app.version import APP_NAME, APP_TITLE, VERSION


def _fix_console_encoding():
    """把控制台切到 UTF-8，否则中文横幅会乱码。

    Windows 控制台默认代码页 936(GBK)，Python 按 cp936 输出 UTF-8 里的
    中文就会变成乱码。必须在**任何 print 之前**调用。
    """
    if os.name != "nt":
        return
    try:
        import ctypes

        k = ctypes.windll.kernel32
        if k.GetConsoleWindow():      # 真的连着控制台才切，别污染管道
            k.SetConsoleOutputCP(65001)
            k.SetConsoleCP(65001)
    except Exception:
        pass
    for name in ("stdout", "stderr"):
        s = getattr(sys, name, None)
        if s is None:
            continue
        try:
            enc = (s.encoding or "").lower().replace("-", "")
            if enc and enc != "utf8":
                s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_fix_console_encoding()


def resource_root() -> str:
    """打包后资源在 sys._MEIPASS，开发时是脚本所在目录。"""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def find_port(start: int = 8765, tries: int = 30) -> int:
    for p in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError(f"从 {start} 开始的 {tries} 个端口都被占用")


def main():
    root = resource_root()
    # 让 app 包能找到资源
    os.environ.setdefault("PATHCAM_ROOT", root)
    if root not in sys.path:
        sys.path.insert(0, root)

    port = find_port()
    url = f"http://127.0.0.1:{port}"

    print("=" * 52)
    print(f"  {APP_TITLE}")
    print(f"  版本 {VERSION}")
    print("=" * 52)
    print(f"  地址: {url}")
    print("  浏览器会自动打开。关闭这个窗口即退出。")
    print("=" * 52)

    def open_browser():
        time.sleep(1.2)
        try:
            webbrowser.open(url)
        except Exception:
            pass

    threading.Thread(target=open_browser, daemon=True).start()

    import uvicorn

    from app.server import app

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
