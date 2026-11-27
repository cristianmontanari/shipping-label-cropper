"""App icon generation (.ico and .png) and project asset path helpers."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

from PIL import Image, ImageDraw


def get_project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent.parent


def get_icon_path() -> Path:
    return get_project_root() / "assets" / "icon.ico"


def get_icon_png_path() -> Path:
    return get_project_root() / "assets" / "icon.png"


def _render_app_icon_image(size: int = 256) -> Image.Image:
    """Render the minimal black-and-white 10x15 label app icon with 4x supersampling."""
    scale = 4
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    def px(v: float) -> int:
        return round(v * scale)

    # Pure matte black rounded square
    draw.rounded_rectangle(
        [px(12), px(12), px(size - 12), px(size - 12)],
        radius=px(48),
        fill=(9, 9, 11, 255),
    )

    # Crisp white 10x15 label card centered
    lx0, ly0, lx1, ly1 = 74, 46, 182, 210
    draw.rounded_rectangle(
        [px(lx0), px(ly0), px(lx1), px(ly1)],
        radius=px(8),
        fill=(255, 255, 255, 255),
    )

    # Minimal black content blocks + barcode bars inside label
    draw.rectangle([px(lx0 + 16), px(ly0 + 20), px(lx1 - 16), px(ly0 + 34)], fill=(9, 9, 11, 255))
    draw.rectangle([px(lx0 + 16), px(ly0 + 48), px(lx0 + 68), px(ly0 + 56)], fill=(113, 113, 122, 255))
    draw.rectangle([px(lx0 + 16), px(ly0 + 64), px(lx0 + 84), px(ly0 + 72)], fill=(161, 161, 170, 255))
    draw.rectangle([px(lx0 + 14), px(ly0 + 90), px(lx1 - 14), px(ly0 + 93)], fill=(228, 228, 231, 255))

    bars = [6, 3, 8, 4, 2, 7, 4, 3, 8, 3, 6]
    bx = lx0 + 16
    for i, bw in enumerate(bars):
        if i % 2 == 0 and bx + bw <= lx1 - 14:
            draw.rectangle([px(bx), px(ly0 + 108), px(bx + bw), px(ly1 - 18)], fill=(9, 9, 11, 255))
        bx += bw + 3

    # Minimal white crop corner ticks outside label
    c = (255, 255, 255, 255)
    arm = 24
    thick = 7
    o = 12
    draw.rectangle([px(lx0 - o), px(ly0 - o), px(lx0 - o + arm), px(ly0 - o + thick)], fill=c)
    draw.rectangle([px(lx0 - o), px(ly0 - o), px(lx0 - o + thick), px(ly0 - o + arm)], fill=c)
    draw.rectangle([px(lx1 + o - arm), px(ly0 - o), px(lx1 + o), px(ly0 - o + thick)], fill=c)
    draw.rectangle([px(lx1 + o - thick), px(ly0 - o), px(lx1 + o), px(ly0 - o + arm)], fill=c)
    draw.rectangle([px(lx0 - o), px(ly1 + o - thick), px(lx0 - o + arm), px(ly1 + o)], fill=c)
    draw.rectangle([px(lx0 - o), px(ly1 + o - arm), px(lx0 - o + thick), px(ly1 + o)], fill=c)
    draw.rectangle([px(lx1 + o - arm), px(ly1 + o - thick), px(lx1 + o), px(ly1 + o)], fill=c)
    draw.rectangle([px(lx1 + o - thick), px(ly1 + o - arm), px(lx1 + o), px(ly1 + o)], fill=c)

    return img.resize((size, size), Image.Resampling.LANCZOS)


def _save_image_atomic(img: Image.Image, path: Path, **save_kwargs: Any) -> None:
    """Write an icon file atomically so concurrent launches cannot corrupt or lock it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".icon-", suffix=path.suffix or ".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        os.close(fd)
        img.save(str(tmp_path), **save_kwargs)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def generate_app_icon_png(png_path: Optional[Path] = None) -> Path:
    """Generate a crisp minimal black-and-white PNG icon (for Linux/macOS window & desktop entries)."""
    path = png_path or get_icon_png_path()
    if png_path is None and path.exists():
        return path
    img = _render_app_icon_image(256)
    _save_image_atomic(img, path, format="PNG")
    return path


def generate_app_icon(icon_path: Optional[Path] = None) -> Path:
    """Generate the app .ico file (and accompanying .png when using the default assets path)."""
    path = icon_path or get_icon_path()
    need_main = icon_path is not None or not path.exists()
    png_path = get_icon_png_path() if icon_path is None else None
    need_png = png_path is not None and not png_path.exists()
    if not need_main and not need_png:
        return path

    img = _render_app_icon_image(256)
    if need_main:
        if path.suffix.lower() == ".png":
            _save_image_atomic(img, path, format="PNG")
            return path
        _save_image_atomic(
            img,
            path,
            format="ICO",
            sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
        )
    if need_png and png_path is not None:
        try:
            _save_image_atomic(img, png_path, format="PNG")
        except OSError:
            pass
    return path
