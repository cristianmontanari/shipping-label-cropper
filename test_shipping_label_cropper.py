"""Automated tests for Shipping Label Cropper engine, orientation, config, GUI widgets, and OS integration."""

from __future__ import annotations

import plistlib
import sys
import tempfile
import tkinter as tk
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import patch

import pymupdf as fitz
from PIL import Image

from shipping_label_cropper import presets
from shipping_label_cropper.context_menu import (
    build_linux_desktop_entry,
    build_linux_dolphin_servicemenu,
    build_linux_nautilus_script,
    build_windows_menu_command,
    create_macos_quick_action,
    get_context_menu_ui_strings,
    get_persistent_linux_icon_png,
    is_context_menu_installed,
)
from shipping_label_cropper.gui import ShippingLabelCropperApp
from shipping_label_cropper.icons import generate_app_icon, generate_app_icon_png
from shipping_label_cropper.pdf_engine import (
    PT_PER_MM,
    CropRect,
    collect_export_pages,
    detect_label_bounds,
    export_cropped_pdf,
    render_label_preview,
    render_page_image,
)
from shipping_label_cropper.print_dialog import DirectPrintDialog
from shipping_label_cropper.printing import (
    _WIN_CAPABILITIES_SCRIPT,
    _WIN_CLEAR_QUEUE_SCRIPT,
    _build_windows_print_script,
    _parse_cups_lpoptions,
    _parse_cups_printers,
    _run_powershell,
    print_images_to_thermal_printer,
)
from shipping_label_cropper.widgets import (
    MinimalButton,
    MinimalDropdown,
    MinimalSlider,
    _platform_cursor,
    _wheel_steps,
    run_in_background,
    ui_font,
)


def _create_mock_a4_shipping_pdf(path: Path) -> None:
    """Create a realistic 2-page A4 shipping PDF:
    Page 0: Landscape shipping label (150x100mm) in the top half of A4.
    Page 1: Portrait shipping label (100x145mm) in the top-left quadrant of A4.
    """
    doc = fitz.open()
    a4_w = 210 * PT_PER_MM
    a4_h = 297 * PT_PER_MM

    # Page 0: landscape label on top half
    p0 = doc.new_page(width=a4_w, height=a4_h)
    box0 = fitz.Rect(20 * PT_PER_MM, 15 * PT_PER_MM, 170 * PT_PER_MM, 115 * PT_PER_MM)
    p0.draw_rect(box0, color=(0, 0, 0), width=2)
    p0.insert_text((30 * PT_PER_MM, 35 * PT_PER_MM), "EXPRESS COURIER 10x15", fontsize=18)
    p0.insert_text((30 * PT_PER_MM, 55 * PT_PER_MM), "TO: John Doe - 12 Main St, New York", fontsize=12)
    for i in range(30):
        x = (30 + i * 3.5) * PT_PER_MM
        p0.draw_rect(
            fitz.Rect(x, 75 * PT_PER_MM, x + (1.2 if i % 2 == 0 else 2.2) * PT_PER_MM, 105 * PT_PER_MM),
            fill=(0, 0, 0),
        )

    # Page 1: portrait label on top-left
    p1 = doc.new_page(width=a4_w, height=a4_h)
    box1 = fitz.Rect(10 * PT_PER_MM, 10 * PT_PER_MM, 105 * PT_PER_MM, 150 * PT_PER_MM)
    p1.draw_rect(box1, color=(0, 0, 0), width=2)
    p1.insert_text((18 * PT_PER_MM, 30 * PT_PER_MM), "PORTRAIT LABEL", fontsize=14)
    p1.insert_text((18 * PT_PER_MM, 50 * PT_PER_MM), "TRACK: US9988776655", fontsize=11)

    doc.save(str(path))
    doc.close()


def _black_centroid(img: Image.Image) -> tuple[float, float]:
    gray = img.convert("L")
    w, h = gray.size
    xs = []
    ys = []
    for y in range(h):
        for x in range(w):
            if gray.getpixel((x, y)) < 30:
                xs.append(x / w)
                ys.append(y / h)
    assert xs, "Expected dark pixels in rendered image"
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _test_rotated_source_pdfs(tmp_dir: Path) -> None:
    """Verify PDFs with built-in /Rotate 90, 180, 270 export non-blank and properly oriented."""
    for rot in (90, 180, 270):
        src_path = tmp_dir / f"rotated_{rot}.pdf"
        out_path = tmp_dir / f"rotated_{rot}_out.pdf"

        doc = fitz.open()
        p = doc.new_page(width=210 * PT_PER_MM, height=297 * PT_PER_MM)
        # Asymmetric L-shape (black top bar + grey left bar) + text in unrotated top-left
        p.draw_rect(fitz.Rect(10 * PT_PER_MM, 10 * PT_PER_MM, 90 * PT_PER_MM, 30 * PT_PER_MM), fill=(0, 0, 0))
        p.draw_rect(fitz.Rect(10 * PT_PER_MM, 30 * PT_PER_MM, 30 * PT_PER_MM, 100 * PT_PER_MM), fill=(0.5, 0.5, 0.5))
        p.insert_text((20 * PT_PER_MM, 55 * PT_PER_MM), f"ROT_{rot}_LABEL", fontsize=14)
        p.set_rotation(rot)
        doc.save(str(src_path))
        doc.close()

        src_doc = fitz.open(str(src_path))
        try:
            crop = detect_label_bounds(src_doc, 0, padding_mm=2.0)
            clip_vis = crop.to_pdf_rect(src_doc[0].rect)
            ref_pix = src_doc[0].get_pixmap(matrix=fitz.Matrix(1, 1), clip=clip_vis, alpha=False)
            ref_img = Image.frombytes("RGB", (ref_pix.width, ref_pix.height), ref_pix.samples)
            ref_cx, ref_cy = _black_centroid(ref_img)

            export_cropped_pdf(src_doc, out_path, [(0, crop)], manual_rotation=0, margin_mm=0.0)
            out_doc = fitz.open(str(out_path))
            try:
                assert f"ROT_{rot}_LABEL" in out_doc[0].get_text(), f"Text lost on /Rotate {rot} PDF!"
                out_img, _ = render_page_image(out_doc, 0, max_width=300, max_height=450)
                out_cx, out_cy = _black_centroid(out_img)
                assert abs(out_cx - ref_cx) < 0.15 and abs(out_cy - ref_cy) < 0.15, (
                    f"/Rotate {rot} orientation mismatch: expected ({ref_cx:.2f}, {ref_cy:.2f}), "
                    f"got ({out_cx:.2f}, {out_cy:.2f})"
                )
            finally:
                out_doc.close()
        finally:
            src_doc.close()

    # /Rotate 90 and /Rotate 270 A4 PDFs with labels past 210mm (bottom third of unrotated page)
    for rot in (90, 270):
        doc = fitz.open()
        p = doc.new_page(width=210 * PT_PER_MM, height=297 * PT_PER_MM)
        p.draw_rect(fitz.Rect(20 * PT_PER_MM, 220 * PT_PER_MM, 170 * PT_PER_MM, 280 * PT_PER_MM), fill=(0, 0, 0))
        p.insert_text((30 * PT_PER_MM, 250 * PT_PER_MM), f"BOTTOM_THIRD_{rot}", fontsize=14, color=(1, 1, 1))
        p.set_rotation(rot)
        try:
            crop = detect_label_bounds(doc, 0, padding_mm=2.0)
            out_path = tmp_dir / f"rotated_bottom_{rot}.pdf"
            export_cropped_pdf(doc, out_path, [(0, crop)], manual_rotation=0, margin_mm=0.0)
            assert p.rotation == rot, "Source page rotation was not restored!"
            out_doc = fitz.open(str(out_path))
            try:
                assert f"BOTTOM_THIRD_{rot}" in out_doc[0].get_text(), f"Bottom-third text lost on /Rotate {rot}!"
            finally:
                out_doc.close()
        finally:
            doc.close()

    # Rotated PDFs with a non-zero CropBox origin must not export blank
    doc = fitz.open()
    p = doc.new_page(width=210 * PT_PER_MM, height=297 * PT_PER_MM)
    p.set_cropbox(fitz.Rect(100 * PT_PER_MM, 100 * PT_PER_MM, 200 * PT_PER_MM, 250 * PT_PER_MM))
    p.draw_rect(fitz.Rect(10 * PT_PER_MM, 10 * PT_PER_MM, 50 * PT_PER_MM, 50 * PT_PER_MM), fill=(0, 0, 0))
    p.insert_text((15 * PT_PER_MM, 70 * PT_PER_MM), "CROPBOX_OK", fontsize=12)
    try:
        for rot in (0, 90, 180, 270):
            p.set_rotation(rot)
            crop = detect_label_bounds(doc, 0, padding_mm=2.0)
            out_path = tmp_dir / f"cropbox_rot_{rot}.pdf"
            export_cropped_pdf(doc, out_path, [(0, crop)], manual_rotation=0, margin_mm=0.0)
            out_doc = fitz.open(str(out_path))
            try:
                assert "CROPBOX_OK" in out_doc[0].get_text(), f"Offset CropBox text lost at /Rotate {rot}!"
                out_img, _ = render_page_image(out_doc, 0, max_width=200, max_height=300)
                _black_centroid(out_img)
            finally:
                out_doc.close()
    finally:
        doc.close()

    # All 16 combinations of source /Rotate x manual_rotation preserve expected visual quadrant
    doc = fitz.open()
    p = doc.new_page(width=100 * PT_PER_MM, height=150 * PT_PER_MM)
    p.draw_rect(fitz.Rect(10 * PT_PER_MM, 10 * PT_PER_MM, 30 * PT_PER_MM, 30 * PT_PER_MM), fill=(0, 0, 0))
    try:
        for srot in (0, 90, 180, 270):
            p.set_rotation(srot)
            pix_vis = p.get_pixmap(alpha=False)
            vis_img = Image.frombytes("RGB", (pix_vis.width, pix_vis.height), pix_vis.samples)
            for mrot in (0, 90, 180, 270):
                ref_cx, ref_cy = _black_centroid(vis_img.rotate(-mrot, expand=True))
                out_img, _ = render_label_preview(doc, 0, CropRect(0, 0, 1, 1), manual_rotation=mrot, margin_mm=0.0)
                out_cx, out_cy = _black_centroid(out_img)
                assert (ref_cx < 0.5) == (out_cx < 0.5) and (ref_cy < 0.5) == (out_cy < 0.5), (
                    f"Rotation mismatch for src={srot}, manual={mrot}: expected ({ref_cx:.2f},{ref_cy:.2f}), got ({out_cx:.2f},{out_cy:.2f})"
                )
    finally:
        doc.close()

    print("[OK] Rotated source PDFs, offset CropBox, and 16 rotation combos export accurately")


def _test_pdf_annotations_and_widgets(tmp_dir: Path) -> None:
    """Verify PDF form fields (widgets) and annotations survive vector export."""
    doc = fitz.open()
    p = doc.new_page(width=100 * PT_PER_MM, height=150 * PT_PER_MM)
    widget = fitz.Widget()
    widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    widget.field_name = "tracking"
    widget.field_value = "TRACK987654"
    widget.rect = fitz.Rect(20, 20, 220, 50)
    p.add_widget(widget)
    p.add_freetext_annot(fitz.Rect(20, 70, 220, 100), "STAMP_ANNOT", fontsize=12)
    src_path = tmp_dir / "widgets_src.pdf"
    out_path = tmp_dir / "widgets_out.pdf"
    doc.save(str(src_path))
    doc.close()

    src_doc = fitz.open(str(src_path))
    try:
        export_cropped_pdf(src_doc, out_path, [(0, CropRect(0, 0, 1, 1))], margin_mm=0.0)
        assert src_doc[0].first_widget is not None, "Original source document was mutated by baking!"
        out_doc = fitz.open(str(out_path))
        try:
            text = out_doc[0].get_text()
            assert "TRACK987654" in text, f"Form field widget lost on export: {text!r}"
            assert "STAMP_ANNOT" in text, f"Annotation lost on export: {text!r}"
        finally:
            out_doc.close()
    finally:
        src_doc.close()
    print("[OK] PDF form widgets and annotations preserved on vector export")


def _test_multipage_collect_export_pages(tmp_dir: Path) -> None:
    """Verify 'All pages' preserves per-page crops and auto-detects unvisited pages."""
    src_path = tmp_dir / "multipage.pdf"
    doc = fitz.open()
    a4_w, a4_h = 210 * PT_PER_MM, 297 * PT_PER_MM
    for idx, y_mm in enumerate((15, 160, 80)):
        p = doc.new_page(width=a4_w, height=a4_h)
        p.draw_rect(fitz.Rect(20 * PT_PER_MM, y_mm * PT_PER_MM, 120 * PT_PER_MM, (y_mm + 90) * PT_PER_MM), fill=(0, 0, 0))
    doc.save(str(src_path))
    doc.close()

    doc = fitz.open(str(src_path))
    try:
        custom_p0 = CropRect(0.05, 0.05, 0.55, 0.40)
        custom_p1 = CropRect(0.10, 0.50, 0.65, 0.90)
        page_crops = {0: custom_p0, 1: custom_p1}

        single = collect_export_pages(doc, page_index=1, page_crops=page_crops, apply_all=False)
        assert single == [(1, custom_p1)]

        all_pages = collect_export_pages(doc, page_index=1, page_crops=page_crops, apply_all=True)
        assert len(all_pages) == 3
        assert all_pages[0] == (0, custom_p0), "Page 0 custom crop was overwritten!"
        assert all_pages[1] == (1, custom_p1), "Page 1 custom crop was overwritten!"
        assert all_pages[2][0] == 2 and all_pages[2][1] != custom_p1, "Unvisited page 2 was not auto-detected!"
    finally:
        doc.close()
    print("[OK] Multi-page collect_export_pages preserves per-page crops")


def _test_cups_parsers() -> None:
    printers = _parse_cups_printers(lpstat_e_out="Rollo_X1038\nZebra_GK420d\nRollo_X1038\n")
    assert printers == ["Rollo_X1038", "Zebra_GK420d"], f"Unexpected printers: {printers}"

    printers_p = _parse_cups_printers(
        lpstat_p_out=(
            "printer Thermal_4x6 is idle.  enabled since Mon 01 Jan 2026\n"
            "printer Office_Laser disabled since Tue 02 Jan 2026\n"
        )
    )
    assert printers_p == ["Thermal_4x6", "Office_Laser"], f"Unexpected lpstat -p printers: {printers_p}"

    lpoptions_sample = (
        "PageSize/Media Size: *w288h432 w288h288 Custom.WIDTHxHEIGHT\n"
        "Darkness/Print Darkness: Default 0 1 2 3 4 5 6 7 *8 9 10 11 12 13 14 15\n"
        "zePrintRate/Print Speed: *2 3 4 5 6\n"
    )
    caps = _parse_cups_lpoptions(lpoptions_sample)
    assert caps["supported"] is True
    assert caps["darkness"] == {
        "param_name": "Darkness",
        "min": 0,
        "max": 15,
        "default": 8,
        "current": 8,
    }, f"Unexpected darkness caps: {caps['darkness']}"
    assert caps["speed"] is not None
    assert caps["speed"]["feature_name"] == "zePrintRate"
    assert caps["speed"]["current"] == "2"
    assert len(caps["speed"]["options"]) == 5
    print("[OK] CUPS lpstat / lpoptions parsers OK")


def _test_windows_powershell_safety() -> None:
    script_plain = _build_windows_print_script(400, 600, apply_devmode=False)
    script_dm = _build_windows_print_script(400, 600, apply_devmode=True)
    script_land = _build_windows_print_script(600, 400, apply_devmode=False)
    for script in (script_plain, script_dm):
        assert "$env:CROPPDF_PRINTER_NAME" in script
        assert 'throw "Invalid printer:' not in script
        assert "throw ('Invalid printer: ' + $printerName)" in script
        assert "$pd.DefaultPageSettings.Landscape = $false" in script
        assert "[Math]::Min(" in script
    assert "$pd.DefaultPageSettings.Landscape = $true" in script_land
    assert "-ErrorAction SilentlyContinue" not in _WIN_CLEAR_QUEUE_SCRIPT
    assert "Remove-PrintJob -InputObject $j -ErrorAction Stop" in _WIN_CLEAR_QUEUE_SCRIPT
    assert "try {" in _WIN_CAPABILITIES_SCRIPT and "} catch {" in _WIN_CAPABILITIES_SCRIPT

    if sys.platform == "win32":
        res = _run_powershell("Write-Output 'Drucker Büro – Label'", timeout=10)
        assert res.returncode == 0 and res.stdout.strip() == "Drucker Büro – Label", (
            f"UTF-8 PowerShell helper mangled non-ASCII text: {res.stdout!r}"
        )
        tiny_img = Image.new("RGB", (16, 16), (255, 255, 255))
        injected_name = 'NoSuchPrinter$(1+1)"Test'
        ok, msg = print_images_to_thermal_printer([tiny_img], injected_name)
        assert not ok, "Expected invalid printer to fail"
        assert f"Invalid printer: {injected_name}" in msg, f"PowerShell did not treat printer name literally: {msg}"
    print("[OK] Windows PowerShell scripts avoid injection, preserve aspect ratio, and use UTF-8")


def _test_linux_and_macos_menu_generators(tmp_dir: Path) -> None:
    fake_py = PurePosixPath("/opt/crop pdf 100%/.venv/bin/python3")
    fake_main = PurePosixPath("/opt/crop pdf 100%/main.py")
    fake_icon = PurePosixPath("/opt/crop pdf 100%/assets/icon.png")

    desktop = build_linux_desktop_entry(fake_py, fake_main, fake_icon)
    assert "MimeType=application/pdf;" in desktop
    assert "StartupWMClass=ShippingLabelCropper" in desktop
    assert '"/opt/crop pdf 100%%/.venv/bin/python3" "/opt/crop pdf 100%%/main.py" %f' in desktop

    dolphin = build_linux_dolphin_servicemenu(fake_py, fake_main, fake_icon)
    assert "Type=Service" in dolphin and "CropShippingLabel" in dolphin

    nautilus = build_linux_nautilus_script(fake_py, fake_main)
    assert nautilus.startswith("#!/usr/bin/env bash")
    assert "'/opt/crop pdf 100%/.venv/bin/python3'" in nautilus

    wf_dir = create_macos_quick_action(fake_py, fake_main, services_dir=tmp_dir)
    info_plist = plistlib.loads((wf_dir / "Contents" / "Info.plist").read_bytes())
    doc_wflow = plistlib.loads((wf_dir / "Contents" / "document.wflow").read_bytes())
    assert info_plist["NSServices"][0]["NSSendFileTypes"] == ["com.adobe.pdf"]
    assert doc_wflow["workflowMetaData"]["serviceInputTypeIdentifier"] == "com.apple.Automator.fileSystemObject.PDF"
    cmd_str = doc_wflow["actions"][0]["action"]["ActionParameters"]["COMMAND_STRING"]
    assert "'/opt/crop pdf 100%/.venv/bin/python3'" in cmd_str

    # Frozen standalone executable command generation (no main.py argument)
    win_script_cmd = build_windows_menu_command(
        PurePosixPath("C:/Apps/pythonw.exe"), PurePosixPath("C:/Apps/main.py")
    )
    assert win_script_cmd == '"C:/Apps/pythonw.exe" "C:/Apps/main.py" "%1"'
    win_frozen_cmd = build_windows_menu_command(PurePosixPath("C:/Apps/ShippingLabelCropper.exe"), None)
    assert win_frozen_cmd == '"C:/Apps/ShippingLabelCropper.exe" "%1"'

    frozen_desktop = build_linux_desktop_entry(
        PurePosixPath("/opt/ShippingLabelCropper"), None, fake_icon
    )
    assert 'Exec="/opt/ShippingLabelCropper" %f' in frozen_desktop
    frozen_nautilus = build_linux_nautilus_script(PurePosixPath("/opt/ShippingLabelCropper"), None)
    assert 'nohup /opt/ShippingLabelCropper "$f"' in frozen_nautilus

    # Frozen Linux icon must live in the persistent config dir, not a temp bundle dir
    frozen_cfg = tmp_dir / "frozen_cfg"
    frozen_cfg.mkdir(parents=True, exist_ok=True)
    with patch("shipping_label_cropper.context_menu.is_frozen", return_value=True), patch(
        "shipping_label_cropper.context_menu.get_config_dir", return_value=frozen_cfg
    ):
        persistent_icon = get_persistent_linux_icon_png()
        assert persistent_icon == frozen_cfg / "icon.png" and persistent_icon.exists()
    print("[OK] Linux (.desktop/Dolphin/Nautilus), macOS (Quick Action), and frozen exe generators OK")


def _test_atomic_config_persistence(tmp_dir: Path) -> None:
    cfg_dir = tmp_dir / "cfg"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    with patch.object(presets, "get_config_dir", return_value=cfg_dir):
        assert presets.load_config() == {"last_printer": ""}
        presets.save_config({"last_printer": "Zebra_GK420d", "printer_settings": {"Zebra_GK420d": {"darkness": 12}}})
        loaded = presets.load_config()
        assert loaded["last_printer"] == "Zebra_GK420d"
        assert loaded["printer_settings"]["Zebra_GK420d"]["darkness"] == 12
        # Verify no leftover temp files remain after atomic replace
        assert [p.name for p in cfg_dir.iterdir()] == ["config.json"]
    print("[OK] Atomic config persistence OK (isolated temp dir)")


def _test_gui_helpers_and_widgets(tmp_dir: Path) -> None:
    assert isinstance(ui_font(9), tuple) and len(ui_font(9)) >= 2
    assert isinstance(ui_font(9, semibold=True), tuple)
    assert _wheel_steps(SimpleNamespace(num=4, delta=0)) == 1.0
    assert _wheel_steps(SimpleNamespace(num=5, delta=0)) == -1.0
    for hit in ("nw", "ne", "sw", "se", "n", "s", "e", "w", "move", "new"):
        assert _platform_cursor(hit), f"Empty cursor for {hit}"
    ui_strs = get_context_menu_ui_strings()
    assert "active" in ui_strs and "inactive" in ui_strs and "open_hint" in ui_strs

    # Test MinimalButton drag-off cancellation, face cache, and run_in_background
    root = tk.Tk()
    root.withdraw()
    try:
        clicks = []
        btn = MinimalButton(root, "Test", lambda: clicks.append(1))
        btn.pack()
        root.update_idletasks()
        bx = btn.winfo_rootx()
        by = btn.winfo_rooty()

        # Press then release outside button -> must NOT fire
        btn._on_press()
        btn._on_release(SimpleNamespace(x_root=bx - 50, y_root=by - 50))
        assert clicks == [], "Button fired when released outside bounds!"

        # Press then release inside button -> MUST fire
        btn._on_press()
        btn._on_release(SimpleNamespace(x_root=bx, y_root=by))
        assert clicks == [1], "Button did not fire when released inside bounds!"

        # Face cache reuses rendered PhotoImage across repeated hover cycles
        btn._apply_style(hovered=True)
        first_hover_img = btn._face_image
        btn._apply_style(hovered=False)
        btn._apply_style(hovered=True)
        assert btn._face_image is first_hover_img, "MinimalButton did not reuse cached hover PhotoImage!"

        # Icon-only MinimalButton does not append text gap padding to its bitmap
        icon_only = MinimalButton(root, "", lambda: None, padx=10, icon="rotate-left")
        icon_with_text = MinimalButton(root, "Left", lambda: None, padx=10, icon="rotate-left")
        assert icon_only._action_icon is not None and icon_with_text._action_icon is not None
        assert icon_only._action_icon.width() == icon_with_text._action_icon.width() - 10

        # MinimalDropdown and MinimalSlider support keyboard focus and arrow adjustments
        dd_var = tk.StringVar(value="One")
        dd_events = []
        dd = MinimalDropdown(root, dd_var, ["One", "Two", "Three"], command=dd_events.append)
        assert str(dd.cget("takefocus")) in ("1", "True")
        dd._step(1)
        assert dd_var.get() == "Two" and dd_events == ["Two"]
        dd._step(-1)
        assert dd_var.get() == "One" and dd_events == ["Two", "One"]
        # Open popup: Up/Down moves highlight without closing; Return commits selection
        dd.pack()
        root.update_idletasks()
        dd._open_popup()
        assert dd._popup is not None and dd._popup.winfo_exists()
        dd._popup_move(1)
        assert dd._popup is not None and dd._popup.winfo_exists(), "Dropdown closed on arrow navigation!"
        assert dd._popup_index == 1 and dd_var.get() == "One"
        dd._popup_commit()
        assert dd._popup is None and dd_var.get() == "Two"

        sl_var = tk.IntVar(value=5)
        sl_events = []
        sl = MinimalSlider(root, sl_var, 0, 10, command=sl_events.append)
        assert str(sl.cget("takefocus")) in ("1", "True")
        sl._nudge(1)
        assert sl_var.get() == 6 and sl_events == [6]
        sl._set_value(10)
        assert sl_var.get() == 10 and sl_events == [6, 10]

        # run_in_background delivers result safely on main thread
        done = []
        run_in_background(root, lambda: "ok", lambda val, exc: done.append((val, exc)), poll_ms=10)
        for _ in range(50):
            root.update()
            if done:
                break
            root.after(10)
        assert done == [("ok", None)], f"Unexpected background result: {done}"
    finally:
        root.destroy()

    # Test replacing source resets manual_rotation, double-click feedback, expanded preview & DirectPrintDialog
    src_pdf = tmp_dir / "replace_me.pdf"
    _create_mock_a4_shipping_pdf(src_pdf)
    app = ShippingLabelCropperApp()
    app.root.withdraw()
    try:
        # Empty state disables Save, Direct Print, and Rotate buttons, and all primary controls take focus
        assert app._save_btn._enabled is False
        assert app._direct_print_btn._enabled is False
        assert app._rotate_left_btn._enabled is False
        assert app._rotate_right_btn._enabled is False
        for ctrl in (app._open_btn, app._save_btn, app._direct_print_btn, app.menu_badge):
            assert str(ctrl.cget("takefocus")) in ("1", "True"), f"Control {ctrl} missing takefocus!"

        # Escape on main window must NOT close the app
        app._on_escape()
        assert app.root.winfo_exists(), "Escape closed the main application window!"

        app.open_pdf(src_pdf)
        assert app._save_btn._enabled is True
        assert app._direct_print_btn._enabled is True
        assert app._rotate_left_btn._enabled is True
        assert app._rotate_right_btn._enabled is True

        # Per-page rotation & Undo / Redo
        app._go_to_page(0)
        app._rotate_by(90)
        assert app.manual_rotation == 90
        app._go_to_page(1)
        assert app.manual_rotation == 0, "Page 0 rotation leaked onto page 1!"
        app._rotate_by(180)
        assert app.manual_rotation == 180
        app._undo()
        assert app.manual_rotation == 0, "Undo did not restore page 1 rotation!"
        app._redo()
        assert app.manual_rotation == 180, "Redo did not reapply page 1 rotation!"
        app._go_to_page(0)
        assert app.manual_rotation == 90, "Page 0 rotation was not preserved!"

        # Format-aware default output path
        app._set_label_size("10 × 10 cm (Square)")
        assert app._default_output_path().name == "replace_me_10x10.pdf"
        app._set_label_size("10.2 × 15.2 cm (Exact 4×6\")")
        assert app._default_output_path().name == "replace_me_4x6.pdf"
        app._set_label_size("10 × 15 cm (Standard 4×6\")")
        assert app._default_output_path().name == "replace_me_10x15.pdf"

        # Edge-segment hit-testing + 5px drag threshold on "new" + Escape cancels drag
        app._img_box = (50, 50, 400, 500)
        app._set_current_crop(CropRect(0.2, 0.2, 0.8, 0.8))
        # Top edge away from corner and midpoint -> "n"
        assert app._hit_test(50 + 0.35 * 400, 50 + 0.2 * 500) == "n"
        # Left edge away from corner and midpoint -> "w"
        assert app._hit_test(50 + 0.2 * 400, 50 + 0.35 * 500) == "w"
        # Micro-click outside crop (< 5px drag) must NOT destroy current crop
        before_crop = app._current_crop()
        app._on_mouse_down(SimpleNamespace(x=10, y=10, type=tk.EventType.ButtonPress, widget=app.canvas))
        app._on_mouse_drag(SimpleNamespace(x=12, y=11, state=0))
        app._on_mouse_up(SimpleNamespace(x=12, y=11))
        assert app._current_crop() == before_crop, "Sub-threshold click outside crop wiped selection!"
        # Escape during active drag restores original crop without closing app
        app._on_mouse_down(SimpleNamespace(x=50 + 0.35 * 400, y=50 + 0.2 * 500, type=tk.EventType.ButtonPress, widget=app.canvas))
        app._on_mouse_drag(SimpleNamespace(x=50 + 0.35 * 400, y=50 + 0.35 * 500, state=0))
        assert app._current_crop() != before_crop
        app._on_escape()
        assert app._current_crop() == before_crop and app._drag_mode is None

        app.manual_rotation = 90
        saved = app._run_export(src_pdf, confirm_replace=False)
        assert saved == src_pdf
        assert app.manual_rotation == 0, "manual_rotation was not reset after overwriting open PDF!"

        # Double-clicking canvas keeps "Label bounds detected." after ButtonRelease-1
        app._set_current_crop(CropRect(0, 0, 1, 1))
        app._on_mouse_down(SimpleNamespace(x=10, y=10, type=tk.EventType.ButtonPress, widget=app.canvas))
        app._apply_quick_preset("Auto-detect")
        app._on_mouse_up(SimpleNamespace(x=10, y=10))
        assert app.feedback_label._text == "Label bounds detected.", (
            f"Double-click feedback was overwritten: {app.feedback_label._text!r}"
        )

        # Expanded preview opens, refreshes, aligns header height with Preview, and closes cleanly
        app._open_expanded_preview(focus_canvas=False)
        assert app._expanded_preview is not None
        app.root.update_idletasks()
        ep_header = app._expanded_preview.winfo_children()[0]
        assert ep_header.winfo_reqheight() == app._preview_header.winfo_reqheight(), (
            f"Expanded header height ({ep_header.winfo_reqheight()}) != Preview header height ({app._preview_header.winfo_reqheight()})"
        )
        app._rotate_by(90)
        app._close_expanded_preview(restore_focus=False)
        assert app._expanded_preview is None

        # Opening a PDF with invalid page dimensions shows an error instead of crashing
        errors = []

        class _ZeroSizeDoc:
            needs_pass = False

            def __len__(self) -> int:
                return 1

            def __getitem__(self, _i: int) -> SimpleNamespace:
                return SimpleNamespace(rect=fitz.Rect(0, 0, 0, 0))

            def close(self) -> None:
                pass

        with (
            patch.object(fitz, "open", return_value=_ZeroSizeDoc()),
            patch.object(app, "_show_operation_error", side_effect=lambda h, m: errors.append((h, m))),
        ):
            app.open_pdf(tmp_dir / "zero_size.pdf")
        assert errors and "invalid dimensions" in errors[0][1].lower(), f"Unexpected error: {errors}"

        # Invalid measurement entry returns "break" on _finish_crop_measurement and blocks _save_quick
        entry, (_, var) = next(iter(app._crop_entries.items()))
        var.set("not-a-number")
        with (
            patch.object(app.root, "focus_get", return_value=entry),
            patch.object(app.root, "bell"),
            patch.object(app, "_run_export") as mock_export,
        ):
            res = app._finish_crop_measurement(SimpleNamespace(type=tk.EventType.KeyPress, widget=entry))
            assert res == "break", "Invalid crop measurement did not return 'break' to block Ctrl+S!"
            var.set("still-bad")
            app._save_quick()
            mock_export.assert_not_called()

        # DirectPrintDialog: disables Print while loading, stays healthy when switching printer during clear queue,
        # and reveals controls when a previously unsupported cached printer refreshes with real capabilities.
        import threading
        from shipping_label_cropper import print_dialog as pd_mod

        gate = threading.Event()
        fake_caps = {
            "supported": True,
            "darkness": {"param_name": "Darkness", "min": 0, "max": 15, "default": 8, "current": 8},
            "speed": None,
            "queue_error": False,
            "queue_status": "Idle",
            "job_count": 0,
        }

        def slow_caps(_name: str) -> dict:
            gate.wait(timeout=2.0)
            return fake_caps

        cfg_dir = tmp_dir / "pd_cfg"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cache: dict = {}
        with (
            patch.object(presets, "get_config_dir", return_value=cfg_dir),
            patch.object(pd_mod, "get_printer_capabilities", side_effect=slow_caps),
        ):
            presets.save_config({"last_printer": "Thermal_Test", "printer_settings": {"Thermal_Test": {"darkness": 13}}})
            dialog = DirectPrintDialog(
                app.root,
                ["Thermal_Test"],
                cache,
                on_active_dialog=lambda _d: None,
                show_dialog=lambda *_a, **_k: False,
                show_error=lambda *_a, **_k: None,
                on_confirm_print=lambda _job: True,
            )
            assert dialog._print_btn._enabled is False, "Print button should be disabled while loading settings!"
            gate.set()
            for _ in range(50):
                app.root.update()
                if dialog._print_btn._enabled:
                    break
                app.root.after(15)
            assert dialog._print_btn._enabled is True, "Print button did not re-enable after loading!"
            assert dialog._darkness_var is not None and dialog._darkness_var.get() == 13
            # Copies stepper bounds and updates
            dialog._step_copies(2)
            assert dialog._copies_var.get() == 3
            dialog._step_copies(-10)
            assert dialog._copies_var.get() == 1
            dialog.close()

        # Previously cached supported=False printer updates controls when live refresh succeeds
        stale_cache: dict = {"Thermal_Test": {"supported": False}}
        with (
            patch.object(presets, "get_config_dir", return_value=cfg_dir),
            patch.object(pd_mod, "get_printer_capabilities", return_value=fake_caps),
        ):
            dialog2 = DirectPrintDialog(
                app.root,
                ["Thermal_Test"],
                stale_cache,
                on_active_dialog=lambda _d: None,
                show_dialog=lambda *_a, **_k: False,
                show_error=lambda *_a, **_k: None,
                on_confirm_print=lambda _job: True,
            )
            for _ in range(50):
                app.root.update()
                if dialog2._darkness_var is not None:
                    break
                app.root.after(15)
            assert dialog2._darkness_var is not None and dialog2._darkness_var.get() == 13, (
                "Stale supported=False cache prevented controls from rendering on refresh!"
            )
            dialog2.close()

        # Switching printer while clearing queue must not wedge Print button
        gate_clear = threading.Event()
        caps_a = {"supported": False, "queue_error": True, "queue_status": "Error", "job_count": 2}
        caps_b = {"supported": True, "queue_error": False, "queue_status": "Idle", "job_count": 0, "darkness": fake_caps["darkness"], "speed": None}
        with (
            patch.object(presets, "get_config_dir", return_value=cfg_dir),
            patch.object(pd_mod, "get_printer_capabilities", side_effect=lambda n: caps_a if n == "PrinterA" else caps_b),
            patch.object(pd_mod, "clear_printer_queue", side_effect=lambda _n: (gate_clear.wait(2.0), (True, "OK"))[1]),
        ):
            presets.save_config({"last_printer": "PrinterA"})
            dialog3 = DirectPrintDialog(
                app.root,
                ["PrinterA", "PrinterB"],
                {"PrinterA": caps_a, "PrinterB": caps_b},
                on_active_dialog=lambda _d: None,
                show_dialog=lambda *_a, **_k: False,
                show_error=lambda *_a, **_k: None,
                on_confirm_print=lambda _job: True,
            )
            clear_btn = dialog3._queue_frame.winfo_children()[0].winfo_children()[0].winfo_children()[1]
            clear_btn._command()
            dialog3._printer_var.set("PrinterB")
            dialog3._load_printer_settings("PrinterB")
            gate_clear.set()
            for _ in range(40):
                app.root.update()
                app.root.after(15)
            assert dialog3._print_btn._enabled is True, "Print button stayed disabled after switching printer during clear queue!"
            assert dialog3._darkness_var is not None, "PrinterB controls were wiped by PrinterA clear callback!"
            dialog3.close()
    finally:
        app._on_close()
    print("[OK] Cross-platform GUI helpers, widgets, and export/measurement guards OK")


def run_all_tests() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)

        # 1. Icon generation into temp directory (does not touch tracked assets/)
        ico = generate_app_icon(tmp_dir / "icon.ico")
        png = generate_app_icon_png(tmp_dir / "icon.png")
        assert ico.exists() and ico.stat().st_size > 1000, ".ico icon was not generated properly"
        assert png.exists() and png.stat().st_size > 1000, ".png icon was not generated properly"
        print(f"[OK] Icons generated in temp dir: {ico.name} ({ico.stat().st_size} B), {png.name} ({png.stat().st_size} B)")

        src_pdf = tmp_dir / "test_label.pdf"
        out_pdf = tmp_dir / "test_label_10x15.pdf"
        _create_mock_a4_shipping_pdf(src_pdf)

        doc = fitz.open(str(src_pdf))
        try:
            # 2. Auto-detect on page 0 (landscape label on top half of A4)
            crop0 = detect_label_bounds(doc, 0, padding_mm=2.0)
            w_mm, h_mm = crop0.to_mm(doc[0].rect)
            print(f"[OK] Auto-detect Page 0: {w_mm:.1f} x {h_mm:.1f} mm (expected ~154 x 104 mm)")
            assert 145 <= w_mm <= 165, f"Detected width out of range: {w_mm}"
            assert 95 <= h_mm <= 115, f"Detected height out of range: {h_mm}"

            # 3. A landscape crop keeps its orientation inside the portrait output format.
            prev_img, eff_rot = render_label_preview(doc, 0, crop0)
            assert eff_rot == 0, f"Expected rotation 0°, got {eff_rot}°"
            assert prev_img.height > prev_img.width, "10x15 preview must be portrait"
            print(f"[OK] Preview Page 0: {prev_img.size}, rotation={eff_rot}°")

            # 4. A portrait crop can be rotated sideways within the same output format.
            crop1 = detect_label_bounds(doc, 1, padding_mm=2.0)
            prev_img1, eff_rot1 = render_label_preview(doc, 1, crop1, manual_rotation=90)
            assert eff_rot1 == 90, f"Expected rotation 90°, got {eff_rot1}°"
            print(f"[OK] Preview Page 1: {prev_img1.size}, rotation={eff_rot1}°")

            # 5. Export vector 10x15 PDF with both pages
            saved = export_cropped_pdf(
                src_doc=doc,
                output_path=out_pdf,
                page_crops=[(0, crop0), (1, crop1)],
                manual_rotation=0,
                margin_mm=2.0,
                label_w_mm=100.0,
                label_h_mm=150.0,
            )
            assert saved.exists(), "10x15 PDF was not created"

            out_doc = fitz.open(str(saved))
            try:
                assert len(out_doc) == 2
                for idx, page in enumerate(out_doc):
                    pw_mm = page.rect.width / PT_PER_MM
                    ph_mm = page.rect.height / PT_PER_MM
                    assert abs(pw_mm - 100.0) < 0.1, f"Page {idx} width {pw_mm} != 100mm"
                    assert abs(ph_mm - 150.0) < 0.1, f"Page {idx} height {ph_mm} != 150mm"
                txt0 = out_doc[0].get_text()
                txt1 = out_doc[1].get_text()
                assert "EXPRESS COURIER" in txt0, "Vector text lost on page 0!"
                assert "US9988776655" in txt1, "Vector text lost on page 1!"
                first_line = out_doc[0].get_text("dict")["blocks"][0]["lines"][0]
                assert first_line["dir"] == (1.0, 0.0), "Landscape label text was rotated during export!"
                print(
                    f"[OK] 10x15 PDF exported: {len(out_doc)} pages, 100.0 x 150.0 mm, vector text preserved!"
                )
            finally:
                out_doc.close()

            # 6. Check that the exported PDF renders non-blank.
            out_doc2 = fitz.open(str(saved))
            try:
                img_out, _ = render_page_image(out_doc2, 0, max_width=400, max_height=600)
                extrema = img_out.convert("L").getextrema()
                assert extrema[0] < 50, f"Exported page appears blank! extrema={extrema}"
                print(f"[OK] Exported page render check: min_luma={extrema[0]} (sharp)")
            finally:
                out_doc2.close()
        finally:
            doc.close()

        # 7. Rotated PDFs, annotations/widgets, multi-page export, CUPS/Windows printing, OS menus, config, and GUI tests
        _test_rotated_source_pdfs(tmp_dir)
        _test_pdf_annotations_and_widgets(tmp_dir)
        _test_multipage_collect_export_pages(tmp_dir)
        _test_cups_parsers()
        _test_windows_powershell_safety()
        _test_linux_and_macos_menu_generators(tmp_dir)
        _test_atomic_config_persistence(tmp_dir)
        _test_gui_helpers_and_widgets(tmp_dir)

    # 8. Context menu status check (platform-aware)
    installed = is_context_menu_installed()
    assert isinstance(installed, bool)
    print(f"[OK] Context menu status ({sys.platform}): installed={installed}")

    print("\nALL TESTS PASSED SUCCESSFULLY!")


if __name__ == "__main__":
    run_all_tests()
