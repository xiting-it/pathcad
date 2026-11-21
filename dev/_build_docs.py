"""把 docs/ 下的 Markdown 渲染成可离线双击查看的独立 HTML，输出到 dist/文档/。

不依赖服务：图片拷到同级 images/ 目录，HTML 里用相对路径引用。
顶部导航改成文档之间互跳，不再是「回到编辑器」。
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.server import _render_markdown  # noqa: E402

OUT = ROOT / "dist" / "文档"
IMG_SRC = ROOT / "_manual"

# (源 md 路径, 输出文件名, 标题)
DOCS = [
    (ROOT / "docs" / "宝宝级操作手册.md", "1-宝宝级操作手册.html", "宝宝级操作手册"),
    (ROOT / "docs" / "使用手册.md", "2-使用手册.html", "使用手册"),
    (ROOT / "交付说明.md", "3-交付说明.html", "交付说明"),
    (ROOT / "docs" / "网口板测试报告.md", "4-网口板测试报告.html", "网口板测试报告"),
    (ROOT / "docs" / "F1板卡测试报告.md", "5-F1板卡测试报告.html", "F1板卡测试报告"),
    # 面向技术人员（开发/合作方）
    (ROOT / "docs" / "架构总览.md", "6-架构总览.html", "架构总览"),
    (ROOT / "docs" / "H5协议备忘.md", "7-H5协议备忘.html", "H5协议备忘"),
    (ROOT / "docs" / "版本管理与发布.md", "8-版本管理与发布.html", "版本管理与发布"),
    (ROOT / "docs" / "H5板调试手册.md", "9-H5板调试手册.html", "H5板调试手册"),
]


def _exe_name() -> str:
    try:
        from app.version import APP_NAME, VERSION
        return f"{APP_NAME}-{VERSION}-dist.exe"
    except Exception:
        return "CAM-B.exe"


def _app_name() -> str:
    try:
        from app.version import APP_NAME
        return APP_NAME
    except Exception:
        return "CAM-B"


def _ver() -> str:
    try:
        from app.version import APP_NAME, VERSION
        return f"{APP_NAME} v{VERSION}"
    except Exception:
        return ""


def build_nav(current: str) -> str:
    """文档之间互跳的顶栏。"""
    links = []
    for _, out_name, title in DOCS:
        cls = ' style="color:#4ea3ff;font-weight:600"' if out_name == current else ""
        links.append(f'<a href="{out_name}"{cls}>{title}</a>')
    return (
        '<div class="top"><b style="color:#4ea3ff">{_app_name()}</b>'
        + "".join(links)
        + "</div><div class=\"pad\"></div>"
    )


def rewrite_images(html: str, doc_dir: Path) -> tuple[str, set[str]]:
    """把 /manual-img/xxx 改成 images/xxx，并收集需要拷的图。"""
    used: set[str] = set()

    def repl(m: re.Match) -> str:
        rel = m.group(1).lstrip("/")
        rel = rel.replace("manual-img/", "", 1)
        used.add(rel)
        # 子目录拍平：new/01-x.png -> images/new__01-x.png
        flat = "images/" + rel.replace("/", "__")
        return f'src="{flat}"'

    html = re.sub(r'src="/manual-img/([^"]+)"', repl, html)
    return html, used


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    img_out = OUT / "images"
    img_out.mkdir(exist_ok=True)

    all_used: set[str] = set()

    for src, out_name, title in DOCS:
        if not src.exists():
            print(f"[跳过] {src.name} 不存在")
            continue
        md = src.read_text(encoding="utf-8")
        html = _render_markdown(md)

        # 去掉原有的「回到编辑器」顶栏（<div class="top"> ... </div><div class="pad"></div>）
        html = re.sub(
            r'<div class="top">.*?</div>\s*<div class="pad"></div>',
            "",
            html,
            count=1,
            flags=re.S,
        )
        # 插回本地导航
        html = html.replace("<body>", "<body>" + build_nav(out_name), 1)
        # 标题
        html = re.sub(
            r"<title>.*?</title>",
            f"<title>{_app_name()} · {title}</title>",
            html, count=1, flags=re.S,
        )

        html, used = rewrite_images(html, OUT)
        all_used |= used

        (OUT / out_name).write_text(html, encoding="utf-8")
        print(f"[生成] {out_name}  ({len(html) // 1024} KB, {len(used)} 张图)")

    # 拷图
    copied = 0
    for rel in sorted(all_used):
        s = IMG_SRC / rel
        if not s.exists():
            print(f"  [警告] 图片缺失: {rel}")
            continue
        shutil.copy2(s, img_out / rel.replace("/", "__"))
        copied += 1
    print(f"[图片] 拷贝 {copied} 张 -> dist/文档/images/")

    # 入口页
    index = [
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>",
        f"<title>{_app_name()} · 文档</title>",
        "<style>body{max-width:760px;margin:0 auto;padding:50px 28px;",
        "background:#14171c;color:#dfe4ec;font:15px/1.8 -apple-system,'Segoe UI','Microsoft YaHei',sans-serif}",
        "h1{color:#4ea3ff;font-size:26px;margin-bottom:6px}",
        "p.sub{color:#8b95a5;margin-top:0}",
        "a.card{display:block;padding:16px 20px;margin:10px 0;background:#1a1f26;",
        "border:1px solid #2e3540;border-radius:8px;text-decoration:none;color:#dfe4ec}",
        "a.card:hover{border-color:#4ea3ff;background:#1f2530}",
        "a.card b{color:#4ea3ff;font-size:16px}",
        "a.card span{display:block;color:#8b95a5;font-size:13px;margin-top:3px}",
        ".tip{margin-top:30px;padding:14px 18px;background:#1a1f26;",
        "border-left:3px solid #ffb454;border-radius:0 6px 6px 0;color:#a8b4c4;font-size:14px}",
        "</style></head><body>",
        f"<h1>{_app_name()}</h1>",
        f"<p class='sub'>路径工艺平台 · 技术文档 · {_ver()}</p>",
    ]
    desc = {
        "1-宝宝级操作手册.html": "从零开始，每一步都写清点哪里、会看到什么。第一次用就看这个。",
        "2-使用手册.html": "完整功能说明，参数含义、结果解读、故障排查。",
        "3-交付说明.html": "启动方式、硬件连接、参数标定、给开发者的信息。",
        "4-网口板测试报告.html": "i.MX RT1170 网口板的实测数据。",
        "5-F1板卡测试报告.html": "STM32F103 串口板的实测数据。",
        "6-架构总览.html": "分层架构、模块职责、核心抽象、扩展点、已知架构债。面向开发。",
        "7-H5协议备忘.html": "H5 板协议，含官方文档与实际不符之处。面向开发。",
        "8-版本管理与发布.html": "SemVer 规则、发版流程、提交规范、CHANGELOG 维护。",
        "9-H5板调试手册.html": "接线、首次上电流程、逐轴验证、故障对照表、禁止事项。",
    }
    index.append("<h2 style='color:#8b95a5;font-size:15px;margin:26px 0 8px'>使用文档</h2>")
    for _, out_name, title in DOCS:
        if out_name.startswith(("6-", "7-", "8-", "9-")):
            continue
        if (OUT / out_name).exists():
            index.append(
                f"<a class='card' href='{out_name}'><b>{title}</b>"
                f"<span>{desc.get(out_name, '')}</span></a>"
            )
    index.append("<h2 style='color:#8b95a5;font-size:15px;margin:26px 0 8px'>开发文档</h2>")
    for _, out_name, title in DOCS:
        if not out_name.startswith(("6-", "7-", "8-", "9-")):
            continue
        if (OUT / out_name).exists():
            index.append(
                f"<a class='card' href='{out_name}'><b>{title}</b>"
                f"<span>{desc.get(out_name, '')}</span></a>"
            )
    index.append("</div>")
    index.append(
        "<div class='tip'><b>先看这个：</b><br>"
        "第一次用 → 看「宝宝级操作手册」。<br>"
        f"没有板子想先试 → 打开 {{_exe_name()}}，在「连接机器」里勾上「用模拟板」，"
        "可以完整跑一遍画图→填充→生成→发送。</div>"
    )
    index.append("</body></html>")
    (OUT / "0-从这里开始.html").write_text("".join(index), encoding="utf-8")
    print("[生成] 0-从这里开始.html")

    # 交付 README（纯文本，收件人先看）
    src_readme = ROOT / "docs" / "交付README.txt"
    if src_readme.exists():
        from app.version import APP_NAME, VERSION
        txt = src_readme.read_text(encoding="utf-8")
        txt = txt.replace("{{EXE}}", f"{APP_NAME}-{VERSION}-dist.exe")
        txt = txt.replace("{{VER}}", VERSION)
        (ROOT / "dist" / "README.txt").write_text(txt, encoding="utf-8")
        print(f"[生成] README.txt（版本 {VERSION}）")

    # 示例模型
    src_samples = ROOT / "samples"
    dst_samples = ROOT / "dist" / "示例模型"
    if src_samples.exists():
        if dst_samples.exists():
            shutil.rmtree(dst_samples)
        shutil.copytree(src_samples, dst_samples)
        n_stl = len(list(dst_samples.glob("*.stl")))
        sz = sum(f.stat().st_size for f in dst_samples.iterdir()) / 1024
        print(f"[拷贝] 示例模型/ {n_stl} 个 STL（{sz:.0f} KB）")


if __name__ == "__main__":
    main()
