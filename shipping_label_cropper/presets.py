"""Built-in quick crop presets and persistent app configuration."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from .pdf_engine import FULL_PAGE_CROP, CropRect

QUICK_PRESETS: Dict[str, Optional[CropRect]] = {
    "Auto-detect": None,  # Handled dynamically by detect_label_bounds
    "Full Page": FULL_PAGE_CROP,
}


def get_config_dir() -> Path:
    legacy_dirs: List[Path] = []
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        cfg_dir = (Path(base) / "ShippingLabelCropper") if base else (Path.home() / ".shipping-label-cropper")
        if base:
            legacy_dirs.append(Path(base) / "CropPDF10x15")
        legacy_dirs.append(Path.home() / ".croppdf10x15")
    elif sys.platform == "darwin":
        cfg_dir = Path.home() / "Library" / "Application Support" / "ShippingLabelCropper"
        legacy_dirs.append(Path.home() / "Library" / "Application Support" / "CropPDF10x15")
        legacy_dirs.append(Path.home() / ".croppdf10x15")
    else:
        xdg_cfg = os.environ.get("XDG_CONFIG_HOME")
        cfg_dir = (
            (Path(xdg_cfg) / "shipping-label-cropper")
            if xdg_cfg
            else (Path.home() / ".config" / "shipping-label-cropper")
        )
        if xdg_cfg:
            legacy_dirs.append(Path(xdg_cfg) / "croppdf10x15")
        legacy_dirs.append(Path.home() / ".config" / "croppdf10x15")
        legacy_dirs.append(Path.home() / ".croppdf10x15")

    cfg_dir.mkdir(parents=True, exist_ok=True)
    new_cfg = cfg_dir / "config.json"
    if not new_cfg.exists():
        for legacy in legacy_dirs:
            old_cfg = legacy / "config.json"
            if old_cfg.exists():
                try:
                    shutil.copy2(old_cfg, new_cfg)
                except OSError:
                    pass
                break
    return cfg_dir


def get_config_path() -> Path:
    return get_config_dir() / "config.json"


def load_config() -> Dict[str, Any]:
    path = get_config_path()
    if not path.exists():
        return {"last_printer": ""}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return {"last_printer": ""}
        data.setdefault("last_printer", "")
        return data
    except Exception:
        return {"last_printer": ""}


def save_config(data: Dict[str, Any]) -> None:
    """Atomically write app configuration so concurrent instances cannot corrupt it."""
    path = get_config_path()
    fd, tmp_name = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
