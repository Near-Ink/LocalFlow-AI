#!/usr/bin/env python3
"""LocalFlow AI 发布前图标生成 / 替换脚本

用一张 1024x1024 源 PNG 生成 electron-builder 需要的全部平台图标：
  desktop/build/icon.png   —— Linux AppImage / electron-builder 转换源（512px）
  desktop/build/icon.icns  —— macOS DMG / .app（iconutil 生成多尺寸）
  desktop/build/icon.ico   —— Windows NSIS 安装器（16-256 多尺寸）

未提供源图时，脚本会先生成一张品牌占位图标（渐变底 + 「L」），
之后把正式 logo 放到 1024x1024 PNG 再跑一次即可覆盖。

依赖：Pillow（pip install pillow）；macOS 生成 icns 需要系统自带 iconutil。

用法：
  python3 desktop/scripts/generate-icons.py                     # 用占位图标
  python3 desktop/scripts/generate-icons.py --source logo.png   # 用正式 logo
  python3 desktop/scripts/generate-icons.py --out desktop/build # 自定义输出目录
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ICNS_SIZES = [
    ("icon_16x16.png", 16), ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32), ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128), ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256), ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512), ("icon_512x512@2x.png", 1024),
]
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]
LINUX_SIZE = 512
SOURCE_MIN = 512
SOURCE_RECOMMEND = 1024


def make_placeholder(size: int = 1024) -> Image.Image:
    """生成品牌占位图标：蓝紫渐变圆角底 + 白色「L」"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 圆角矩形背景（左上→右下 蓝→紫 渐变）
    r = int(size * 0.22)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=255)
    grad = Image.new("RGBA", (size, size))
    gd = ImageDraw.Draw(grad)
    top, bottom = (37, 99, 235), (124, 58, 237)
    for y in range(size):
        t = y / (size - 1)
        gd.line([(0, y), (size, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    img.paste(grad, (0, 0), mask)
    # 字母 L
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", int(size * 0.58))
    except Exception:
        font = ImageFont.load_default()
    bbox = d.textbbox((0, 0), "L", font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(((size - w) / 2 - bbox[0], (size - h) / 2 - bbox[1] - int(size * 0.03)),
           "L", font=font, fill=(255, 255, 255, 255))
    return img


def load_source(path: Path | None) -> Image.Image:
    """加载源图；未提供则生成占位图。校验尺寸并居中裁剪为正方形。"""
    if path is None:
        print("[icons] 未提供源图，使用品牌占位图标（替换正式 logo 后重跑本脚本即可）")
        return make_placeholder()
    if not path.exists():
        sys.exit(f"[icons] 源图不存在：{path}")
    img = Image.open(path).convert("RGBA")
    w, h = img.size
    if min(w, h) < SOURCE_MIN:
        sys.exit(f"[icons] 源图太小：{w}x{h}，至少需要 {SOURCE_MIN}x{SOURCE_MIN}（推荐 {SOURCE_RECOMMEND}x{SOURCE_RECOMMEND}）")
    if w != h:
        print(f"[icons] 源图非正方形（{w}x{h}），自动居中裁剪为 {min(w, h)}x{min(w, h)}")
        s = min(w, h)
        img = img.crop(((w - s) // 2, (h - s) // 2, (w + s) // 2, (h + s) // 2))
    print(f"[icons] 使用源图：{path}（{img.size[0]}x{img.size[1]}）")
    return img


def write_icns(src: Image.Image, out_dir: Path) -> Path:
    """生成 macOS icon.icns（iconutil）"""
    iconset = out_dir / "icon.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir(parents=True, exist_ok=True)
    for name, px in ICNS_SIZES:
        src.resize((px, px), Image.LANCZOS).save(iconset / name)
    iconutil = shutil.which("iconutil")
    if not iconutil:
        sys.exit("[icons] 未找到 iconutil（macOS 系统工具），无法生成 icns；请在 macOS 上运行")
    icns = out_dir / "icon.icns"
    subprocess.run([iconutil, "-c", "icns", str(iconset), "-o", str(icns)], check=True)
    shutil.rmtree(iconset)
    print(f"[icons] 生成 macOS 图标：{icns.name}（{icns.stat().st_size // 1024} KB）")
    return icns


def write_ico(src: Image.Image, out_dir: Path) -> Path:
    """生成 Windows icon.ico（多尺寸）"""
    ico = out_dir / "icon.ico"
    src.resize((256, 256), Image.LANCZOS).save(
        ico, format="ICO", sizes=[(s, s) for s in ICO_SIZES]
    )
    print(f"[icons] 生成 Windows 图标：{ico.name}（{ico.stat().st_size // 1024} KB）")
    return ico


def write_png(src: Image.Image, out_dir: Path) -> Path:
    """生成 Linux icon.png（512px，electron-builder 转换源）"""
    png = out_dir / "icon.png"
    src.resize((LINUX_SIZE, LINUX_SIZE), Image.LANCZOS).save(png, format="PNG")
    print(f"[icons] 生成 Linux 图标：{png.name}（{png.stat().st_size // 1024} KB）")
    return png


def main() -> None:
    here = Path(__file__).resolve().parent
    default_out = here.parent / "build"
    ap = argparse.ArgumentParser(description="LocalFlow AI 发布图标生成脚本")
    ap.add_argument("--source", type=Path, default=None,
                    help="源图标 PNG 路径（1024x1024 推荐；缺省生成占位图标）")
    ap.add_argument("--out", type=Path, default=default_out,
                    help="输出目录（默认 desktop/build）")
    args = ap.parse_args()

    out = args.out
    out.mkdir(parents=True, exist_ok=True)

    src = load_source(args.source)
    write_png(src, out)
    write_icns(src, out)
    write_ico(src, out)

    print("\n[icons] 完成！electron-builder 将自动读取 desktop/build/ 下以下文件：")
    print("  icon.png（Linux / 转换源）  icon.icns（macOS）  icon.ico（Windows）")
    print("  正式发布：把品牌 logo 输出为 1024x1024 PNG，执行：")
    print(f"  python3 {here / 'generate-icons.py'} --source <logo.png>")


if __name__ == "__main__":
    main()
