"""Minimal black-and-white desktop GUI for Shipping Label Cropper."""

from __future__ import annotations

import ctypes
import math
import os
import shutil
import subprocess
import sys
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Any, Callable, Dict, List, Optional, Tuple

import pymupdf as fitz
from PIL import Image, ImageDraw, ImageEnhance, ImageTk

from .context_menu import (
    get_context_menu_ui_strings,
    install_context_menu,
    is_context_menu_installed,
    uninstall_context_menu,
)
from .expanded_preview import ExpandedPreviewFrame
from .icons import generate_app_icon, generate_app_icon_png
from .pdf_engine import (
    FULL_PAGE_CROP,
    LABEL_SIZES_MM,
    PT_PER_MM,
    CropRect,
    collect_export_pages,
    detect_label_bounds,
    export_cropped_pdf,
    render_label_preview,
    render_page_image,
    render_pdf_for_printing,
)
from .presets import QUICK_PRESETS
from .print_dialog import DirectPrintDialog
from .printing import list_printers, print_images_to_thermal_printer
from .widgets import (
    BORDER,
    BORDER_STRONG,
    CANVAS_BG,
    CONTROL_RADIUS,
    DISABLED,
    EDGE_HIT,
    HANDLE_HIT,
    HANDLE_RADIUS,
    HOVER_BG,
    INK,
    MUTED,
    SIDEBAR_WIDTH,
    STATUS_STYLES,
    SUBTLE_BG,
    UI_FONT,
    WHITE,
    ContextMenuBadge,
    ElidedLabel,
    IllustratedOption,
    MinimalButton,
    PageStrip,
    _platform_cursor,
    _rounded_control_image,
    _safe_set_cursor,
    attach_tooltip,
    run_in_background,
    ui_font,
)


def _enable_windows_app_id() -> None:
    """Assign an explicit AppUserModelID so Windows taskbar uses our icon, not pythonw.exe."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ShippingLabelCropper")
    except Exception:
        pass


def _enable_windows_dpi() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _apply_windows_window_icon(window: tk.Misc, ico_path: Path) -> Tuple[int, int]:
    """Set crisp DPI-matched ICON_SMALL and ICON_BIG directly on the Win32 HWND."""
    if sys.platform != "win32":
        return 0, 0
    try:
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.GetParent.argtypes = [wintypes.HWND]
        user32.GetParent.restype = wintypes.HWND
        user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        user32.GetSystemMetrics.restype = ctypes.c_int
        user32.LoadImageW.argtypes = [
            wintypes.HINSTANCE,
            wintypes.LPCWSTR,
            wintypes.UINT,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.SendMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.SendMessageW.restype = ctypes.c_ssize_t

        window.update_idletasks()
        hwnd = user32.GetParent(window.winfo_id()) or window.winfo_id()
        if not hwnd:
            return 0, 0

        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x0010
        WM_SETICON = 0x0080
        ICON_SMALL = 0
        ICON_BIG = 1
        SM_CXICON, SM_CYICON = 11, 12
        SM_CXSMICON, SM_CYSMICON = 49, 50

        big_w = max(32, user32.GetSystemMetrics(SM_CXICON))
        big_h = max(32, user32.GetSystemMetrics(SM_CYICON))
        sm_w = max(16, user32.GetSystemMetrics(SM_CXSMICON))
        sm_h = max(16, user32.GetSystemMetrics(SM_CYSMICON))

        hicon_big = user32.LoadImageW(None, str(ico_path), IMAGE_ICON, big_w, big_h, LR_LOADFROMFILE)
        hicon_small = user32.LoadImageW(None, str(ico_path), IMAGE_ICON, sm_w, sm_h, LR_LOADFROMFILE)
        if hicon_big:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon_big)
        if hicon_small:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon_small)
        return int(hicon_small or 0), int(hicon_big or 0)
    except Exception:
        return 0, 0


class _WindowsFileDropHandler:
    """Subclasses a Tk top-level HWND on Windows to accept WM_DROPFILES for PDFs."""

    def __init__(self, window: tk.Misc, on_file: Callable[[Path], None]) -> None:
        self._hwnd: int = 0
        self._orig_wndproc: int = 0
        self._proc_ref: Any = None
        if sys.platform != "win32":
            return
        try:
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            shell32 = ctypes.windll.shell32
            is_64 = ctypes.sizeof(ctypes.c_void_p) == 8
            LONG_PTR = ctypes.c_longlong if is_64 else ctypes.c_long
            WNDPROC = ctypes.WINFUNCTYPE(
                LONG_PTR, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
            )
            set_wnd_long = user32.SetWindowLongPtrW if is_64 else user32.SetWindowLongW
            set_wnd_long.argtypes = [wintypes.HWND, ctypes.c_int, LONG_PTR]
            set_wnd_long.restype = LONG_PTR
            call_wnd_proc = user32.CallWindowProcW
            call_wnd_proc.argtypes = [LONG_PTR, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            call_wnd_proc.restype = LONG_PTR
            shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
            shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
            shell32.DragQueryFileW.restype = wintypes.UINT
            shell32.DragFinish.argtypes = [wintypes.HANDLE]

            window.update_idletasks()
            hwnd = user32.GetParent(window.winfo_id()) or window.winfo_id()
            if not hwnd:
                return

            GWLP_WNDPROC = -4
            WM_DROPFILES = 0x0233

            def _wndproc(h: int, msg: int, wparam: int, lparam: int) -> int:
                if msg == WM_DROPFILES:
                    try:
                        count = shell32.DragQueryFileW(wparam, 0xFFFFFFFF, None, 0)
                        for idx in range(count):
                            length = shell32.DragQueryFileW(wparam, idx, None, 0)
                            if length > 0:
                                buf = ctypes.create_unicode_buffer(length + 1)
                                shell32.DragQueryFileW(wparam, idx, buf, length + 1)
                                dropped = Path(buf.value)
                                if dropped.suffix.lower() == ".pdf" and dropped.is_file():
                                    window.after(0, lambda p=dropped: on_file(p))
                                    break
                    finally:
                        shell32.DragFinish(wparam)
                    return 0
                return call_wnd_proc(self._orig_wndproc, h, msg, wparam, lparam)

            self._proc_ref = WNDPROC(_wndproc)
            proc_addr = ctypes.cast(self._proc_ref, ctypes.c_void_p).value
            if not proc_addr:
                return
            self._hwnd = int(hwnd)
            self._orig_wndproc = int(set_wnd_long(hwnd, GWLP_WNDPROC, proc_addr))
            shell32.DragAcceptFiles(hwnd, True)
        except Exception:
            self._hwnd = 0
            self._orig_wndproc = 0
            self._proc_ref = None

    def uninstall(self) -> None:
        if sys.platform != "win32" or not self._hwnd or not self._orig_wndproc:
            return
        try:
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            shell32 = ctypes.windll.shell32
            is_64 = ctypes.sizeof(ctypes.c_void_p) == 8
            LONG_PTR = ctypes.c_longlong if is_64 else ctypes.c_long
            set_wnd_long = user32.SetWindowLongPtrW if is_64 else user32.SetWindowLongW
            set_wnd_long.argtypes = [wintypes.HWND, ctypes.c_int, LONG_PTR]
            set_wnd_long.restype = LONG_PTR
            shell32.DragAcceptFiles(self._hwnd, False)
            set_wnd_long(self._hwnd, -4, self._orig_wndproc)
        except Exception:
            pass
        finally:
            self._hwnd = 0
            self._orig_wndproc = 0
            self._proc_ref = None


class ShippingLabelCropperApp:
    """Minimal monochrome desktop app for cropping shipping label PDFs to 4x6 / 10x15 cm."""

    def __init__(self, initial_pdf: Optional[str] = None) -> None:
        _enable_windows_app_id()
        _enable_windows_dpi()
        if sys.platform.startswith("linux"):
            self.root = tk.Tk(className="ShippingLabelCropper")
        else:
            self.root = tk.Tk()
        self.root.withdraw()
        self._win_icons: Tuple[int, int] = (0, 0)
        self._icon_photo: Optional[ImageTk.PhotoImage] = None
        self._menu_strings = get_context_menu_ui_strings()

        self.root.title("Shipping Label Cropper")
        self.root.configure(bg=WHITE)

        try:
            if sys.platform == "win32":
                ico = generate_app_icon()
                self.root.iconbitmap(default=str(ico))
                self.root.iconbitmap(str(ico))
                self._win_icons = _apply_windows_window_icon(self.root, ico)
            else:
                png = generate_app_icon_png()
                with Image.open(png) as icon_img:
                    self._icon_photo = ImageTk.PhotoImage(icon_img.copy())
                self.root.iconphoto(True, self._icon_photo)
        except Exception:
            pass

        self._configure_styles()

        # Document & crop state
        self.doc: Optional[fitz.Document] = None
        self.pdf_path: Optional[Path] = None
        self.page_index: int = 0
        self.page_crops: Dict[int, CropRect] = {}
        self.page_rotations: Dict[int, int] = {}
        self._crop_preset_by_page: Dict[int, Optional[str]] = {}
        self._undo_stack: Dict[int, List[Tuple[CropRect, Optional[str], int]]] = {}
        self._redo_stack: Dict[int, List[Tuple[CropRect, Optional[str], int]]] = {}
        self.current_page_rect: Optional[fitz.Rect] = None
        self._last_saved_path: Optional[Path] = None
        self._drop_handler: Optional[_WindowsFileDropHandler] = None

        # Canvas cache
        self._orig_page_img: Optional[Image.Image] = None
        self._dim_page_img: Optional[Image.Image] = None
        self._dim_photo: Optional[ImageTk.PhotoImage] = None
        self._bright_photo: Optional[ImageTk.PhotoImage] = None
        self._preview_photo: Optional[ImageTk.PhotoImage] = None
        self._expanded_preview: Optional[ExpandedPreviewFrame] = None
        self._active_dialog: Optional[tk.Toplevel] = None
        self._printer_caps_cache: Dict[str, Dict[str, Any]] = {}
        self._listing_printers: bool = False
        self._print_in_progress: bool = False
        self._preview_hovered: bool = False
        self._img_box: Tuple[int, int, int, int] = (0, 0, 1, 1)

        # Interaction state
        self._drag_mode: Optional[str] = None
        self._drag_new_active: bool = False
        self._drag_start_canvas: Tuple[float, float] = (0.0, 0.0)
        self._drag_start_crop: CropRect = FULL_PAGE_CROP
        self._drag_start_preset: Optional[str] = None
        self._preview_after_id: Optional[str] = None
        self._resize_after_id: Optional[str] = None

        # Minimal settings
        self.apply_all_pages_var = tk.BooleanVar(value=False)
        self.label_size_var = tk.StringVar(value="10 × 15 cm (Standard 4×6\")")
        self.margin_mm_var = tk.DoubleVar(value=2.0)

        self._build_ui()
        self.root.update_idletasks()
        sidebar_width = max(SIDEBAR_WIDTH, self._sidebar.winfo_reqwidth())
        min_width = max(760, self._empty_state.winfo_reqwidth() + sidebar_width + 25)
        self._base_min_height = max(560, self.root.winfo_reqheight())
        self.root.minsize(min_width, self._base_min_height)

        scr_w = self.root.winfo_screenwidth()
        scr_h = self.root.winfo_screenheight()
        base_h = round(min(960, max(660, int(scr_h * 0.86))) * 0.84)
        canvas_pad = 24
        a4_w = round((base_h - 51 - 2 * canvas_pad) * (210.0 / 297.0))
        base_w = a4_w + 2 * canvas_pad + sidebar_width + 1
        win_w = max(min_width, min(int(scr_w * 0.92), round(max(780, base_w, min_width) * 112 / 100)))
        win_h = max(self._base_min_height, min(scr_h - 64, round(max(base_h, self._base_min_height) * 116 / 100)))
        pos_x = max(16, (scr_w - win_w) // 2)
        pos_y = max(0, (scr_h - win_h - 48) // 2)
        self.root.geometry(f"{win_w}x{win_h}+{pos_x}+{pos_y}")
        self.root.deiconify()
        self._drop_handler = _WindowsFileDropHandler(self.root, self.open_pdf)
        self._bind_shortcuts()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        if initial_pdf:
            p = Path(initial_pdf)
            self.root.after(40, lambda: self.open_pdf(p))

    @property
    def manual_rotation(self) -> int:
        return self.page_rotations.get(self.page_index, 0) % 360

    @manual_rotation.setter
    def manual_rotation(self, value: int) -> None:
        self.page_rotations[self.page_index] = int(value) % 360

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        # Ttk repeats the center and edges; a larger tile avoids hundreds of tiny draws.
        tile_size = (128, 48)
        self._measurement_field_images = tuple(
            ImageTk.PhotoImage(
                _rounded_control_image(tile_size, fill, border), master=self.root,
            )
            for fill, border in ((WHITE, BORDER_STRONG), (WHITE, INK), (SUBTLE_BG, BORDER_STRONG))
        )
        normal, focused, disabled = self._measurement_field_images
        style.element_create(
            "CropMeasurement.field", "image", normal, ("disabled", disabled), ("focus", focused),
            border=CONTROL_RADIUS, padding=8, sticky="nsew", width=0, height=0,
        )
        style.layout("CropMeasurement.TEntry", [
            ("CropMeasurement.field", {"sticky": "nsew", "children": [
                ("Entry.textarea", {"sticky": "nsew"}),
            ]}),
        ])
        style.configure(
            "CropMeasurement.TEntry", background=WHITE, foreground=INK,
            selectbackground=INK, selectforeground=WHITE,
        )
        style.map("CropMeasurement.TEntry", foreground=[("disabled", DISABLED)])

    # ------------------------------------------------------------------ UI Build
    def _build_ui(self) -> None:
        header = tk.Frame(self.root, bg=WHITE, padx=16)
        header.pack(side=tk.TOP, fill=tk.X)
        header.columnconfigure(1, weight=1)

        self._open_btn = MinimalButton(
            header,
            text="Open PDF",
            command=self._choose_pdf_dialog,
            variant="secondary",
            font=ui_font(9, semibold=True),
            padx=8,
            pady=5,
            takefocus=True,
            icon="folder",
        )
        self._open_btn.grid(row=0, column=0, pady=10)
        attach_tooltip(self._open_btn, "Open a PDF file (Ctrl+O)")

        self.file_label = ElidedLabel(
            header,
            text="No file",
            bg=WHITE,
            fg=INK,
            font=ui_font(10, semibold=True),
            anchor="w",
        )
        self.file_label.grid(row=0, column=1, sticky="ew", padx=(12, 16))

        # Right side of header: context menu toggle
        right_h = tk.Frame(header, bg=WHITE)
        right_h.grid(row=0, column=2)

        self.menu_badge = ContextMenuBadge(right_h, self._menu_strings, self._toggle_context_menu)
        self.menu_badge.pack(side=tk.RIGHT)
        attach_tooltip(self.menu_badge, "Toggle file-manager right-click menu")
        self._refresh_menu_badge()

        # Hairline divider under header
        tk.Frame(self.root, bg=BORDER, height=1).pack(side=tk.TOP, fill=tk.X)

        self._build_status_bar()
        body = tk.Frame(self.root, bg=BORDER)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, minsize=SIDEBAR_WIDTH)
        body.rowconfigure(0, weight=1)

        original = tk.Frame(body, bg=WHITE)
        original.grid(row=0, column=0, sticky="nsew", padx=(0, 1))
        self._workspace = tk.Frame(original, bg=WHITE)
        self._workspace.columnconfigure(0, weight=1)
        self._workspace.rowconfigure(0, weight=1)
        self._crop_view = tk.Frame(self._workspace, bg=WHITE)
        self._crop_view.grid(row=0, column=0, sticky="nsew")

        original_header = tk.Frame(self._crop_view, bg=WHITE, padx=16, pady=8)
        original_header.pack(side=tk.TOP, fill=tk.X)
        tk.Label(
            original_header, text="Original", bg=WHITE, fg=INK, font=ui_font(9, semibold=True)
        ).pack(side=tk.LEFT)
        self.source_meta = tk.Label(original_header, bg=WHITE, fg=MUTED, font=ui_font(8))
        self.source_meta.pack(side=tk.RIGHT)
        tk.Frame(self._crop_view, bg=BORDER, height=1).pack(side=tk.TOP, fill=tk.X)

        self._workspace.pack(fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(self._crop_view, bg=CANVAS_BG, highlightthickness=0, cursor="arrow")
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.canvas.bind("<ButtonPress-1>", self._on_mouse_down)
        self.canvas.bind("<B1-Motion>", self._on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_mouse_up)
        self.canvas.bind("<Motion>", self._on_mouse_move)
        self.canvas.bind("<Double-Button-1>", lambda _e: self._apply_quick_preset("Auto-detect"))
        self._page_strip = PageStrip(
            original,
            self._go_to_page,
            self.apply_all_pages_var,
            on_scope_change=lambda _all: self._sync_action_labels(),
        )
        self._empty_state = self._build_empty_state()
        self._empty_state.place(relx=0.5, rely=0.5, anchor="center")

        self._sidebar = tk.Frame(body, bg=WHITE)
        self._sidebar.grid(row=0, column=1, sticky="nsew")
        self._build_panel(self._sidebar)

    def _build_status_bar(self) -> None:
        footer = tk.Frame(self.root, bg=SUBTLE_BG)
        footer.pack(side=tk.BOTTOM, fill=tk.X)
        tk.Frame(footer, bg=BORDER, height=1).pack(fill=tk.X)

        status = tk.Frame(footer, bg=SUBTLE_BG, padx=16, pady=5)
        status.pack(fill=tk.X)
        status.columnconfigure(0, weight=1)
        feedback = tk.Frame(status, bg=SUBTLE_BG)
        feedback.grid(row=0, column=0, sticky="ew")

        size = max(12, round(status.winfo_fpixels("9p")))
        scale = 4
        self._feedback_icons = {}
        for kind, (color, paths) in STATUS_STYLES.items():
            image = Image.new("RGBA", (16 * scale, 16 * scale))
            draw = ImageDraw.Draw(image)
            draw.ellipse((scale, scale, 15 * scale, 15 * scale), outline=color, width=6)
            for path in paths:
                draw.line([(x * scale, y * scale) for x, y in path], fill=color, width=6, joint="curve")
            self._feedback_icons[kind] = ImageTk.PhotoImage(
                image.resize((size, size), Image.Resampling.LANCZOS), master=status,
            )
        self._feedback_icon = tk.Label(
            feedback, image=self._feedback_icons["info"], bg=SUBTLE_BG, bd=0, padx=0, pady=0,
        )
        # Align the icon with the visible text within the font's line box.
        self._feedback_icon.pack(side=tk.LEFT, padx=(0, 5), pady=(2, 0))
        self.feedback_label = ElidedLabel(
            feedback, text="Open a PDF to get started.", bg=SUBTLE_BG, fg=MUTED, font=ui_font(8), anchor="w",
        )
        self.feedback_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._feedback_action = tk.Button(
            status, bg=SUBTLE_BG, activebackground=HOVER_BG, fg="#235A83", relief=tk.FLAT,
            bd=0, padx=4, pady=0, font=ui_font(8, semibold=True), takefocus=True,
            highlightbackground=SUBTLE_BG, highlightcolor=INK, cursor="hand2",
        )
        self._feedback_action.bind("<Return>", self._invoke_feedback_action)
        self.crop_meta = tk.Label(status, bg=SUBTLE_BG, fg=MUTED, font=ui_font(8))
        self.crop_meta.grid(row=0, column=2, padx=(12, 0))
        self._status_format = tk.Label(status, bg=SUBTLE_BG, fg=MUTED, font=ui_font(8))
        self._sync_status_format()

    def _build_empty_state(self) -> tk.Frame:
        frame = tk.Frame(self.canvas, bg=CANVAS_BG)
        tk.Label(
            frame, text="From A4 to a label", bg=CANVAS_BG, fg=INK,
            font=ui_font(20, semibold=True), wraplength=376,
        ).pack()
        subtitle = (
            "Drop a PDF here or open a file to fit a thermal printer."
            if sys.platform == "win32"
            else "Crop the label from your PDF to fit a thermal printer."
        )
        tk.Label(
            frame, text=subtitle,
            bg=CANVAS_BG, fg=MUTED, font=ui_font(9), wraplength=320,
        ).pack(pady=(4, 14))

        comparison = tk.Frame(frame, bg=CANVAS_BG)
        comparison.pack()
        comparison.columnconfigure((0, 1), weight=1, uniform="before_after")
        artwork = tk.Canvas(comparison, width=344, height=180, bg=CANVAS_BG, highlightthickness=0)
        artwork.grid(row=0, column=0, columnspan=2)
        self._draw_empty_illustration(artwork)
        self._empty_after_size: Optional[tk.Label] = None
        for column, (caption, size) in enumerate((
            ("Before", "A4 document"), ("After", f"{self._format_label_text()} label"),
        )):
            tk.Label(
                comparison, text=caption, bg=CANVAS_BG, fg=INK, font=ui_font(9, semibold=True),
            ).grid(row=1, column=column, pady=(6, 0))
            lbl = tk.Label(
                comparison, text=size, bg=CANVAS_BG, fg=MUTED, font=ui_font(8),
            )
            lbl.grid(row=2, column=column)
            if column == 1:
                self._empty_after_size = lbl

        actions = tk.Frame(frame, bg=CANVAS_BG)
        actions.pack(pady=(14, 0))
        MinimalButton(
            actions, text="Open PDF", command=self._choose_pdf_dialog, variant="primary",
            font=ui_font(9, semibold=True), padx=8, takefocus=True, icon="folder",
        ).pack(side=tk.LEFT, padx=(0, 8))
        MinimalButton(
            actions, text="Open from file manager", command=self._show_file_manager_help, variant="link",
            font=(*ui_font(8), "underline"), takefocus=True,
        ).pack(side=tk.LEFT)
        return frame

    @staticmethod
    def _draw_empty_illustration(canvas: tk.Canvas) -> None:
        def draw_label(x: float, y: float, scale: float) -> None:
            canvas.create_rectangle(x, y, x + 100 * scale, y + 150 * scale, fill=WHITE, outline=BORDER_STRONG)
            canvas.create_rectangle(x, y, x + 100 * scale, y + 22 * scale, fill=INK, outline="")
            for top, text, size, color in (
                (6, "EXPRESS PARCEL", 9, WHITE),
                (31, "SHIP TO", 7, MUTED),
                (44, "ELENA ROSSI", 10, INK),
                (60, "BOLOGNA · IT", 7, INK),
                (82, "IT-BO-04", 12, INK),
            ):
                canvas.create_text(
                    x + 8 * scale, y + top * scale, anchor="nw", text=text,
                    font=(UI_FONT, -max(1, round(size * scale)), "bold"), fill=color,
                )
            for top in (74, 104):
                canvas.create_line(x, y + top * scale, x + 100 * scale, y + top * scale, fill=BORDER_STRONG)
            bars = (
                (8, 3), (14, 4), (22, 2), (27, 5), (35, 3), (41, 4), (49, 2),
                (54, 5), (62, 3), (68, 2), (73, 4), (81, 3), (87, 5),
            )
            for start, width in bars:
                canvas.create_rectangle(
                    x + start * scale, y + 113 * scale,
                    x + (start + width) * scale, y + 139 * scale, fill=INK, outline="",
                )

        paper_height = 174
        paper_width = paper_height * 210 / 297
        left = 86 - paper_width / 2
        canvas.create_rectangle(left, 3, left + paper_width, 3 + paper_height, fill=WHITE, outline=BORDER_STRONG)
        scale = paper_height / 297
        x, y = left + 31 * scale, 3 + 27 * scale
        draw_label(x, y, scale)
        x0, y0, x1, y1 = x - 2, y - 2, x + 100 * scale + 2, y + 150 * scale + 2
        canvas.create_rectangle(x0, y0, x1, y1, outline=INK)
        for hx, hy in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
            canvas.create_rectangle(hx - 2, hy - 2, hx + 2, hy + 2, fill=WHITE, outline=INK)
        for top, length in ((194, 97), (203, 132), (212, 108)):
            canvas.create_line(x, 3 + top * scale, x + length * scale, 3 + top * scale, fill=BORDER_STRONG)

        canvas.create_line(164, 90, 180, 90, arrow=tk.LAST, arrowshape=(5, 6, 3), fill=MUTED)
        label_scale = 0.86
        draw_label(258 - 50 * label_scale, 90 - 75 * label_scale, label_scale)

    def _build_panel(self, panel: tk.Frame) -> None:
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(1, minsize=120, weight=1)

        self._preview_header = tk.Frame(panel, bg=WHITE, padx=16, pady=8)
        self._preview_header.grid(row=0, column=0, sticky="ew")
        tk.Label(
            self._preview_header, text="Preview", bg=WHITE, fg=INK, font=ui_font(9, semibold=True)
        ).pack(side=tk.LEFT)
        self.preview_meta = tk.Label(
            self._preview_header, text="10 × 15 cm", bg=WHITE, fg=MUTED, font=ui_font(8)
        )
        self.preview_meta.pack(side=tk.RIGHT)

        self._preview_wrap = tk.Frame(panel, bg=CANVAS_BG, highlightthickness=0, bd=0)
        self._preview_wrap.grid(row=1, column=0, sticky="nsew")
        self._preview_top_border = tk.Frame(self._preview_wrap, bg=BORDER, height=1)
        self._preview_top_border.pack(side=tk.TOP, fill=tk.X)
        self._preview_bottom_border = tk.Frame(self._preview_wrap, bg=BORDER, height=1)
        self._preview_bottom_border.pack(side=tk.BOTTOM, fill=tk.X)
        self.preview_canvas = tk.Canvas(
            self._preview_wrap, bg=CANVAS_BG, width=1, height=1, highlightthickness=0
        )
        self.preview_canvas.pack(fill=tk.BOTH, expand=True)
        self.preview_canvas.bind("<Configure>", lambda _e: self._schedule_preview_update())
        self.preview_canvas.bind("<Enter>", self._on_preview_enter)
        self.preview_canvas.bind("<Leave>", self._on_preview_leave)
        for sequence in ("<ButtonRelease-1>", "<Return>", "<KP_Enter>", "<space>"):
            self.preview_canvas.bind(sequence, self._toggle_expanded_preview)
        self.preview_canvas.bind("<FocusIn>", lambda _e: self._sync_preview_border())
        self.preview_canvas.bind("<FocusOut>", lambda _e: self._sync_preview_border())

        content = tk.Frame(panel, bg=WHITE, padx=16, pady=10)
        content.grid(row=2, column=0, sticky="nsew")
        self._build_crop_controls(content)

        tk.Frame(panel, bg=BORDER, height=1).grid(row=3, column=0, sticky="ew")
        footer = tk.Frame(panel, bg=WHITE, padx=16, pady=8)
        footer.grid(row=4, column=0, sticky="ew")
        self._save_btn = MinimalButton(
            footer,
            text="Save",
            command=self._save_quick,
            variant="primary",
            font=ui_font(10, semibold=True),
            padx=8,
            pady=8,
            takefocus=True,
            icon="save",
        )
        self._save_btn.pack(fill=tk.X, pady=(0, 4))
        self._save_btn.set_enabled(False)
        attach_tooltip(self._save_btn, "Save cropped PDF (Ctrl+S · Ctrl+Shift+S for Save As)")

        self._direct_print_btn = MinimalButton(
            footer,
            text="Direct Print",
            command=self._direct_print_dialog,
            variant="outline",
            font=ui_font(9, semibold=True),
            padx=8,
            pady=7,
            takefocus=True,
            icon="printer",
        )
        self._direct_print_btn.pack(fill=tk.X)
        self._direct_print_btn.set_enabled(False)
        attach_tooltip(self._direct_print_btn, "Print directly to thermal printer (Ctrl+P)")

    def _build_crop_controls(self, content: tk.Frame) -> None:
        tk.Label(
            content, text="Crop", bg=WHITE, fg=INK, font=ui_font(9, semibold=True)
        ).pack(anchor="w", pady=(0, 4))

        preset_grid = tk.Frame(content, bg=WHITE)
        preset_grid.pack(fill=tk.X, pady=(0, 8))
        preset_grid.columnconfigure(0, weight=1, uniform="preset")
        preset_grid.columnconfigure(1, weight=1, uniform="preset")

        self._crop_preset_buttons: Dict[str, MinimalButton] = {}
        for column, (preset, text, icon, tip) in enumerate((
            ("Auto-detect", "Auto", "auto-crop", "Auto-detect label bounds (A or double-click)"),
            ("Full Page", "Full page", "full-page", "Select the full source page"),
        )):
            button = MinimalButton(
                preset_grid, text=text, command=lambda name=preset: self._apply_quick_preset(name),
                font=ui_font(9, semibold=True), padx=8, pady=5, takefocus=True, icon=icon,
            )
            button.grid(row=0, column=column, sticky="ew", padx=(0, 3) if column == 0 else (3, 0))
            button.set_enabled(False)
            attach_tooltip(button, tip)
            self._crop_preset_buttons[preset] = button

        self._build_crop_measurements(content)

        self._label_settings = tk.Frame(content, bg=WHITE)
        self._label_settings.pack(fill=tk.X)
        tk.Frame(self._label_settings, bg=BORDER, height=1).pack(fill=tk.X, pady=(2, 6))
        tk.Label(
            self._label_settings, text="Label settings", bg=WHITE, fg=INK, font=ui_font(9, semibold=True)
        ).pack(anchor="w", pady=(0, 4))
        self._build_format_options(self._label_settings)
        self._build_orientation_options(self._label_settings)
        self._build_margin_options(self._label_settings)

    def _build_orientation_options(self, parent: tk.Frame) -> None:
        hdr = tk.Frame(parent, bg=WHITE)
        hdr.pack(fill=tk.X, pady=(0, 4))
        tk.Label(
            hdr, text="Orientation", bg=WHITE, fg=MUTED, font=ui_font(8, semibold=True)
        ).pack(side=tk.LEFT)
        self._rotation_reset_btn = MinimalButton(
            hdr, text="Reset", command=self._reset_rotation, variant="ghost",
            font=ui_font(8, semibold=True), padx=4, pady=0, takefocus=True,
        )
        self._rotation_meta = tk.Label(
            hdr, text="0°", bg=WHITE, fg=MUTED, font=ui_font(8)
        )
        self._rotation_meta.pack(side=tk.RIGHT)

        ctrl_row = tk.Frame(parent, bg=WHITE)
        ctrl_row.pack(fill=tk.X, pady=(0, 6))
        ctrl_row.columnconfigure((0, 1), weight=1, uniform="rotation")

        self._rotate_left_btn = MinimalButton(
            ctrl_row, text="Rotate left", command=lambda: self._rotate_by(-90),
            padx=6, pady=6, takefocus=True, icon="rotate-left",
        )
        self._rotate_left_btn.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self._rotate_left_btn.set_enabled(False)
        attach_tooltip(self._rotate_left_btn, "Rotate 90° counter-clockwise (Shift+R)")

        self._rotate_right_btn = MinimalButton(
            ctrl_row, text="Rotate right", command=lambda: self._rotate_by(90),
            padx=6, pady=6, takefocus=True, icon="rotate-right",
        )
        self._rotate_right_btn.grid(row=0, column=1, sticky="ew", padx=(3, 0))
        self._rotate_right_btn.set_enabled(False)
        attach_tooltip(self._rotate_right_btn, "Rotate 90° clockwise (R)")

    def _build_crop_measurements(self, parent: tk.Frame) -> None:
        section = tk.Frame(parent, bg=WHITE)
        section.pack(fill=tk.X, pady=(0, 6))
        heading = tk.Frame(section, bg=WHITE)
        heading.pack(fill=tk.X)
        hint = tk.Label(heading, text="Enter to apply", bg=WHITE, fg=MUTED, font=ui_font(8))
        row = tk.Frame(section, bg=WHITE)

        def toggle_measurements() -> None:
            toggle.focus_set()
            if row.winfo_manager():
                row.pack_forget()
                hint.pack_forget()
                toggle.set_text("▸ Crop measurements (mm)")
            else:
                row.pack(fill=tk.X, pady=(2, 0))
                hint.pack(side=tk.RIGHT)
                toggle.set_text("▾ Crop measurements (mm)")
            self.root.update_idletasks()
            base_min = getattr(self, "_base_min_height", 560)
            self.root.minsize(self.root.minsize()[0], max(base_min, self.root.winfo_reqheight()))

        toggle = MinimalButton(
            heading, text="▸ Crop measurements (mm)", command=toggle_measurements,
            variant="ghost", font=ui_font(8, semibold=True), padx=2, pady=3, takefocus=True,
        )
        toggle.pack(side=tk.LEFT)
        self._crop_entries: Dict[ttk.Entry, Tuple[str, tk.StringVar]] = {}
        for column, field in enumerate(("X", "Y", "Width", "Height")):
            row.columnconfigure(column, weight=1, uniform="measurement")
            padx = (0, 8) if column < 3 else 0
            tk.Label(row, text=field, bg=WHITE, fg=MUTED, font=ui_font(8)).grid(
                row=0, column=column, sticky="w", padx=padx, pady=(0, 2),
            )
            value = tk.StringVar()
            entry = ttk.Entry(
                row, textvariable=value, width=1, state=tk.DISABLED, justify=tk.RIGHT,
                font=ui_font(8), style="CropMeasurement.TEntry",
            )
            entry.grid(row=1, column=column, sticky="ew", padx=padx)
            self._crop_entries[entry] = field, value
            # Let the native entry handle caret keys before suppressing canvas shortcuts.
            widget, widget_class, toplevel, all_widgets = entry.bindtags()
            entry.bindtags((widget, widget_class, "CropMeasurement", toplevel, all_widgets))
            entry.bind("<Return>", lambda e: self._on_crop_measurement_commit(e.widget))
            entry.bind("<KP_Enter>", lambda e: self._on_crop_measurement_commit(e.widget))
            entry.bind("<FocusOut>", lambda e: self._on_crop_measurement_commit(e.widget))
            entry.bind("<Escape>", self._cancel_crop_measurement)

    def _build_format_options(self, parent: tk.Frame) -> None:
        tk.Label(
            parent, text="Format", bg=WHITE, fg=MUTED, font=ui_font(8, semibold=True)
        ).pack(anchor="w", pady=(0, 4))
        row = tk.Frame(parent, bg=WHITE)
        row.pack(fill=tk.X, pady=(0, 6))
        self._format_buttons: Dict[str, IllustratedOption] = {}
        for column, (key, (width, height)) in enumerate(LABEL_SIZES_MM.items()):
            if (width, height) == (101.6, 152.4):
                text = "4×6″ Exact"
            else:
                text = f"{width / 10:g}×{height / 10:g} cm"
            option = IllustratedOption(
                row, text, command=lambda value=key: self._set_label_size(value),
                paper_size=(round(width * 0.14), round(height * 0.14)),
            )
            row.columnconfigure(column, weight=1, uniform="format")
            option.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 4, 0))
            self._format_buttons[key] = option
        self._sync_format_buttons()

    def _build_margin_options(self, parent: tk.Frame) -> None:
        tk.Label(
            parent, text="Margin", bg=WHITE, fg=MUTED, font=ui_font(8, semibold=True)
        ).pack(anchor="w", pady=(0, 4))

        row = tk.Frame(parent, bg=WHITE)
        row.pack(fill=tk.X)
        self._margin_btns: Dict[float, IllustratedOption] = {}
        for column, mm in enumerate((0.0, 2.0, 4.0)):
            option = IllustratedOption(
                row, f"{mm:g} mm", command=lambda value=mm: self._set_margin(value),
                paper_size=(20, 14), margin_inset=1 + int(mm),
            )
            row.columnconfigure(column, weight=1, uniform="margin")
            option.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 4, 0))
            self._margin_btns[mm] = option
        self._sync_margin_buttons()

    def _bind_shortcuts(self) -> None:
        modifiers = ("Control", "Command") if sys.platform == "darwin" else ("Control",)
        for modifier in modifiers:
            for key in ("o", "O"):
                self.root.bind(f"<{modifier}-{key}>", lambda _e: self._choose_pdf_dialog())
            self.root.bind(f"<{modifier}-s>", lambda _e: self._save_quick())
            self.root.bind(f"<{modifier}-Shift-S>", lambda _e: self._save_as())
            self.root.bind(f"<{modifier}-S>", lambda _e: self._save_as())
            for key in ("p", "P"):
                self.root.bind(f"<{modifier}-{key}>", lambda _e: self._direct_print_dialog())
            self.root.bind(f"<{modifier}-z>", lambda _e: self._undo())
            self.root.bind(f"<{modifier}-Shift-Z>", lambda _e: self._redo())
            self.root.bind(f"<{modifier}-Z>", lambda _e: self._redo())
            for key in ("y", "Y"):
                self.root.bind(f"<{modifier}-{key}>", lambda _e: self._redo())
        self.root.bind("<Return>", lambda _e: self._save_quick())
        self.root.bind("<Escape>", lambda _e: self._on_escape())
        self.root.bind("r", lambda _e: self._rotate_by(90))
        self.root.bind("R", lambda _e: self._rotate_by(-90))
        self.root.bind("a", lambda _e: self._shortcut_auto_detect())
        self.root.bind("A", lambda _e: self._shortcut_auto_detect())
        self.root.bind("<Prior>", lambda _e: self._prev_page())
        self.root.bind("<Next>", lambda _e: self._next_page())
        self.root.bind("<Left>", lambda e: self._handle_arrow(-1, 0, e.state))
        self.root.bind("<Right>", lambda e: self._handle_arrow(1, 0, e.state))
        self.root.bind("<Up>", lambda e: self._handle_arrow(0, -1, e.state))
        self.root.bind("<Down>", lambda e: self._handle_arrow(0, 1, e.state))
        for key in ("+", "="):
            self.root.bind(key, lambda _e: self._expanded_preview.zoom_in() if self._expanded_preview else None)
        self.root.bind("-", lambda _e: self._expanded_preview.zoom_out() if self._expanded_preview else None)
        self.root.bind("0", lambda _e: self._expanded_preview.reset_zoom() if self._expanded_preview else None)
        for sequence in (
            "a", "A", "r", "R", "+", "=", "-", "0",
            "<Left>", "<Right>", "<Up>", "<Down>", "<Prior>", "<Next>",
        ):
            self.root.bind_class("CropMeasurement", sequence, "break")
        for modifier in modifiers:
            for key in ("o", "O", "s", "S", "Shift-S", "p", "P", "z", "Z", "Shift-Z", "y", "Y"):
                self.root.bind_class("CropMeasurement", f"<{modifier}-{key}>", self._finish_crop_measurement)
        self.root.bind("<ButtonPress-1>", self._finish_crop_measurement, add="+")

    def _shortcut_auto_detect(self) -> None:
        if self._expanded_preview is not None:
            return
        self._apply_quick_preset("Auto-detect")

    def _handle_arrow(self, dx: int, dy: int, state: int) -> None:
        if self._expanded_preview is not None:
            self._expanded_preview.pan(-dx * 32, -dy * 32)
            return
        self._nudge_crop(dx, dy, state)

    # ------------------------------------------------------------- PDF Loading
    def _choose_pdf_dialog(self) -> None:
        path_str = filedialog.askopenfilename(
            title="Open PDF",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        if path_str:
            self.open_pdf(Path(path_str))

    def open_pdf(self, path: Path) -> None:
        try:
            new_doc = fitz.open(str(path))
            problem = ""
            if new_doc.needs_pass:
                problem = "The PDF is password-protected. Open an unlocked copy."
            elif len(new_doc) == 0:
                problem = "The PDF contains no pages. Open a different file."
            elif new_doc[0].rect.width <= 0 or new_doc[0].rect.height <= 0:
                problem = "The PDF page has invalid dimensions. Open a different file."
            if problem:
                new_doc.close()
                self._show_operation_error("Unable to open PDF", problem)
                return
        except Exception as exc:
            self._show_operation_error("Unable to open PDF", f"Check the file or open a different PDF.\n\n{exc}")
            return

        self._close_expanded_preview(restore_focus=False)
        if self.doc is not None and not self.doc.is_closed:
            self.doc.close()

        self.doc = new_doc
        self.pdf_path = path
        self.page_index = 0
        self.page_crops = {}
        self.page_rotations = {}
        self._crop_preset_by_page = {}
        self._undo_stack = {}
        self._redo_stack = {}
        self.root.title(f"{path.name} — Shipping Label Cropper")
        self.file_label.set_text(path.name)
        self.preview_canvas.configure(takefocus=True)
        self._save_btn.set_enabled(True)
        if not (self._print_in_progress or self._listing_printers):
            self._direct_print_btn.set_enabled(True)
        self._rotate_left_btn.set_enabled(True)
        self._rotate_right_btn.set_enabled(True)

        self._load_current_page()
        self._set_feedback("Drag edges to adjust · Shift+Drag locks ratio · Enter to save")

    def _current_crop(self) -> CropRect:
        return self.page_crops.get(self.page_index, FULL_PAGE_CROP)

    def _set_current_crop(self, crop: CropRect, preset: Optional[str] = None) -> None:
        crop = crop.normalized()
        if preset is not None or crop != self._current_crop():
            if self._crop_preset_by_page.get(self.page_index) != preset:
                self._crop_preset_by_page[self.page_index] = preset
                self._sync_crop_presets()
        self.page_crops[self.page_index] = crop
        self._sync_crop_measurements()

    def _load_current_page(self) -> None:
        if self.doc is None:
            return
        if len(self.doc) > 1:
            self._page_strip.pack(side=tk.BOTTOM, fill=tk.X, before=self._workspace)
        else:
            focus = self.root.focus_get()
            if focus is not None and str(focus).startswith(f"{self._page_strip}."):
                self.canvas.focus_set()
            self._page_strip.pack_forget()
            self.apply_all_pages_var.set(False)
        self._page_strip.show_page(self.doc, self.page_index)

        if self.page_index not in self.page_crops:
            self.page_crops[self.page_index] = detect_label_bounds(
                self.doc, self.page_index, padding_mm=3.0
            )
            self._crop_preset_by_page[self.page_index] = "Auto-detect"

        self.current_page_rect = self.doc[self.page_index].rect
        self._render_canvas_page()
        if self._empty_state.winfo_manager():
            self._empty_state.place_forget()
            self.canvas.configure(cursor="crosshair")
            self.canvas.focus_set()
        for button in self._crop_preset_buttons.values():
            button.set_enabled(True)
        for entry in self._crop_entries:
            entry.configure(state=tk.NORMAL)
        self._sync_crop_presets()
        self._sync_crop_measurements()
        self._sync_rotation_ui()
        self._sync_action_labels()
        self._schedule_preview_update(immediate=True)

    def _prev_page(self) -> None:
        self._go_to_page(self.page_index - 1)

    def _next_page(self) -> None:
        self._go_to_page(self.page_index + 1)

    def _go_to_page(self, index: int) -> None:
        if self.doc is None or self.doc.is_closed or not 0 <= index < len(self.doc) or index == self.page_index:
            return
        self._drag_mode = None
        self.page_index = index
        self._load_current_page()

    # --------------------------------------------------------- Canvas Rendering
    def _on_canvas_resize(self, _event=None) -> None:
        if self.doc is None:
            return
        if self._resize_after_id:
            self.root.after_cancel(self._resize_after_id)
        self._resize_after_id = self.root.after(60, self._render_canvas_page)

    def _render_canvas_page(self) -> None:
        if self._resize_after_id is not None:
            self.root.after_cancel(self._resize_after_id)
            self._resize_after_id = None
        if self.doc is None:
            return
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if cw <= 1 or ch <= 1:
            return
        pad = 24
        avail_w = max(120, cw - 2 * pad)
        avail_h = max(120, ch - 2 * pad)

        img, rect = render_page_image(
            self.doc, self.page_index, max_width=avail_w, max_height=avail_h
        )
        img.thumbnail((avail_w, avail_h), Image.Resampling.LANCZOS)
        w_mm, h_mm = FULL_PAGE_CROP.to_mm(rect)
        self.source_meta.configure(text=f"{w_mm:.0f} × {h_mm:.0f} mm")
        self._orig_page_img = img
        self._dim_page_img = ImageEnhance.Brightness(img).enhance(0.60)
        self._dim_photo = ImageTk.PhotoImage(self._dim_page_img)

        ix0 = (cw - img.width) // 2
        iy0 = (ch - img.height) // 2
        self._img_box = (ix0, iy0, img.width, img.height)
        self._redraw_overlay()

    def _crop_to_canvas_px(self, crop: CropRect) -> Tuple[float, float, float, float]:
        ix0, iy0, iw, ih = self._img_box
        return (
            ix0 + crop.x0 * iw,
            iy0 + crop.y0 * ih,
            ix0 + crop.x1 * iw,
            iy0 + crop.y1 * ih,
        )

    def _canvas_to_norm(self, x: float, y: float) -> Tuple[float, float]:
        ix0, iy0, iw, ih = self._img_box
        nx = (x - ix0) / max(1, iw)
        ny = (y - iy0) / max(1, ih)
        return max(0.0, min(1.0, nx)), max(0.0, min(1.0, ny))

    def _redraw_overlay(self) -> None:
        if self.doc is None or self._orig_page_img is None or self._dim_photo is None:
            return

        self.canvas.delete("all")
        ix0, iy0, iw, ih = self._img_box
        crop = self._current_crop()

        # Subtle page sheet border
        self.canvas.create_rectangle(
            ix0 - 1, iy0 - 1, ix0 + iw + 1, iy0 + ih + 1, outline=BORDER_STRONG, width=1
        )
        self.canvas.create_image(ix0, iy0, anchor="nw", image=self._dim_photo)

        # Bright active crop area
        rx0 = max(0, min(iw - 1, int(round(crop.x0 * iw))))
        ry0 = max(0, min(ih - 1, int(round(crop.y0 * ih))))
        rx1 = max(rx0 + 1, min(iw, int(round(crop.x1 * iw))))
        ry1 = max(ry0 + 1, min(ih, int(round(crop.y1 * ih))))
        bright_crop = self._orig_page_img.crop((rx0, ry0, rx1, ry1))
        self._bright_photo = ImageTk.PhotoImage(bright_crop)
        self.canvas.create_image(ix0 + rx0, iy0 + ry0, anchor="nw", image=self._bright_photo)

        cx0, cy0, cx1, cy1 = ix0 + rx0, iy0 + ry0, ix0 + rx1, iy0 + ry1

        # High-contrast double hairline (white inner + black outer) — visible on any background
        self.canvas.create_rectangle(cx0 + 1, cy0 + 1, cx1 - 1, cy1 - 1, outline=WHITE, width=1)
        self.canvas.create_rectangle(cx0, cy0, cx1, cy1, outline=INK, width=2)

        # 8 crisp monochrome square handles
        mx = (cx0 + cx1) / 2.0
        my = (cy0 + cy1) / 2.0
        for hx, hy in [
            (cx0, cy0), (mx, cy0), (cx1, cy0),
            (cx0, my),             (cx1, my),
            (cx0, cy1), (mx, cy1), (cx1, cy1),
        ]:
            self.canvas.create_rectangle(
                hx - HANDLE_RADIUS,
                hy - HANDLE_RADIUS,
                hx + HANDLE_RADIUS,
                hy + HANDLE_RADIUS,
                fill=WHITE,
                outline=INK,
                width=2,
            )

        if self.current_page_rect is not None:
            w_mm, h_mm = crop.to_mm(self.current_page_rect)
            self.crop_meta.configure(text=f"Selection · {w_mm:.1f} × {h_mm:.1f} mm")
            place_inside = (cy0 - HANDLE_RADIUS - 22) < 4
            if place_inside:
                text = self.canvas.create_text(
                    mx, cy0 + HANDLE_RADIUS + 8, anchor="n", text=f"{w_mm:.1f} × {h_mm:.1f} mm",
                    fill=WHITE, font=ui_font(8, semibold=True),
                )
            else:
                text = self.canvas.create_text(
                    mx, cy0 - HANDLE_RADIUS - 4, anchor="s", text=f"{w_mm:.1f} × {h_mm:.1f} mm",
                    fill=WHITE, font=ui_font(8, semibold=True),
                )
            left, top, right, bottom = self.canvas.bbox(text)
            dx = max(0, 8 - left) - max(0, right + 8 - self.canvas.winfo_width())
            dy = max(0, 4 - top)
            self.canvas.move(text, dx, dy)
            badge = self.canvas.create_rectangle(
                left + dx - 5, top + dy - 2, right + dx + 5, bottom + dy + 2,
                fill=INK, outline="",
            )
            self.canvas.tag_lower(badge, text)

    # ------------------------------------------------------- Mouse Interaction
    def _hit_test(self, x: float, y: float) -> str:
        cx0, cy0, cx1, cy1 = self._crop_to_canvas_px(self._current_crop())
        mx = (cx0 + cx1) / 2.0
        my = (cy0 + cy1) / 2.0
        corners = [
            ("nw", cx0, cy0), ("ne", cx1, cy0), ("sw", cx0, cy1), ("se", cx1, cy1),
        ]
        nearby_corners = [
            ((x - hx) ** 2 + (y - hy) ** 2, name)
            for name, hx, hy in corners
            if abs(x - hx) <= HANDLE_HIT and abs(y - hy) <= HANDLE_HIT
        ]
        if nearby_corners:
            return min(nearby_corners)[1]
        mids = [
            ("n", mx, cy0), ("s", mx, cy1), ("w", cx0, my), ("e", cx1, my),
        ]
        nearby_mids = [
            ((x - hx) ** 2 + (y - hy) ** 2, name)
            for name, hx, hy in mids
            if abs(x - hx) <= HANDLE_HIT and abs(y - hy) <= HANDLE_HIT
        ]
        if nearby_mids:
            return min(nearby_mids)[1]
        # Full edge-segment hit-testing so dragging anywhere along the border resizes that edge.
        edge_hits: List[Tuple[float, str]] = []
        if cx0 - EDGE_HIT <= x <= cx1 + EDGE_HIT:
            if abs(y - cy0) <= EDGE_HIT:
                edge_hits.append((abs(y - cy0), "n"))
            if abs(y - cy1) <= EDGE_HIT:
                edge_hits.append((abs(y - cy1), "s"))
        if cy0 - EDGE_HIT <= y <= cy1 + EDGE_HIT:
            if abs(x - cx0) <= EDGE_HIT:
                edge_hits.append((abs(x - cx0), "w"))
            if abs(x - cx1) <= EDGE_HIT:
                edge_hits.append((abs(x - cx1), "e"))
        if edge_hits:
            return min(edge_hits)[1]
        if cx0 <= x <= cx1 and cy0 <= y <= cy1:
            return "move"
        return "new"

    def _on_mouse_move(self, event) -> None:
        if self.doc is None:
            return
        _safe_set_cursor(self.canvas, _platform_cursor(self._hit_test(event.x, event.y)))

    def _on_mouse_down(self, event) -> None:
        if self.doc is None:
            return
        self._finish_crop_measurement(event)
        self.canvas.focus_set()
        self._drag_mode = self._hit_test(event.x, event.y)
        self._drag_new_active = False
        self._drag_start_canvas = (event.x, event.y)
        self._drag_start_crop = self._current_crop()
        self._drag_start_preset = self._crop_preset_by_page.get(self.page_index)

    def _locked_ratio_phys(self, shift_pressed: bool = False) -> Optional[float]:
        if not shift_pressed:
            return None
        w_mm, h_mm = LABEL_SIZES_MM.get(self.label_size_var.get(), (100.0, 150.0))
        if self.manual_rotation in (90, 270):
            return h_mm / w_mm
        # Match current crop orientation if already wide, else vertical 10:15
        c = self._drag_start_crop
        if self._drag_mode != "new" and self.current_page_rect is not None and c.width > 0.02 and c.height > 0.02:
            cw, ch = c.to_mm(self.current_page_rect)
            if cw > ch * 1.1:
                return h_mm / w_mm
        return w_mm / h_mm

    def _on_mouse_drag(self, event) -> None:
        if self.doc is None or self._drag_mode is None or self.current_page_rect is None:
            return
        _, _, iw, ih = self._img_box
        if iw <= 1 or ih <= 1:
            return

        shift = bool(event.state & 0x0001)
        ratio_phys = self._locked_ratio_phys(shift_pressed=shift)
        page_ratio = self.current_page_rect.width / max(1.0, self.current_page_rect.height)
        ratio_norm = (ratio_phys / page_ratio) if ratio_phys else None

        sx, sy = self._drag_start_canvas
        dx = (event.x - sx) / iw
        dy = (event.y - sy) / ih
        c = self._drag_start_crop
        mode = self._drag_mode

        if mode == "move":
            w, h = c.width, c.height
            nx0 = max(0.0, min(1.0 - w, c.x0 + dx))
            ny0 = max(0.0, min(1.0 - h, c.y0 + dy))
            new_crop = CropRect(nx0, ny0, nx0 + w, ny0 + h)
        elif mode == "new":
            if not self._drag_new_active:
                if math.hypot(event.x - sx, event.y - sy) < 5.0:
                    return
                self._drag_new_active = True
            ax, ay = self._canvas_to_norm(sx, sy)
            bx, by = self._canvas_to_norm(event.x, event.y)
            if ratio_norm:
                span_x, span_y = bx - ax, by - ay
                sign_x = 1.0 if span_x >= 0 else -1.0
                sign_y = 1.0 if span_y >= 0 else -1.0
                if abs(span_x) / max(1e-6, ratio_norm) >= abs(span_y):
                    by = ay + sign_y * (abs(span_x) / ratio_norm)
                else:
                    bx = ax + sign_x * (abs(span_y) * ratio_norm)
                bx, by = max(0.0, min(1.0, bx)), max(0.0, min(1.0, by))
            new_crop = CropRect(ax, ay, bx, by).normalized()
        else:
            x0, y0, x1, y1 = c.x0, c.y0, c.x1, c.y1
            if "w" in mode:
                x0 = max(0.0, min(x1 - 0.02, c.x0 + dx))
            if "e" in mode:
                x1 = min(1.0, max(x0 + 0.02, c.x1 + dx))
            if "n" in mode:
                y0 = max(0.0, min(y1 - 0.02, c.y0 + dy))
            if "s" in mode:
                y1 = min(1.0, max(y0 + 0.02, c.y1 + dy))

            if ratio_norm and mode in ("nw", "ne", "sw", "se"):
                h_target = (x1 - x0) / ratio_norm
                if "n" in mode:
                    y0 = max(0.0, y1 - h_target)
                else:
                    y1 = min(1.0, y0 + h_target)
                w_adj = (y1 - y0) * ratio_norm
                if "w" in mode:
                    x0 = max(0.0, x1 - w_adj)
                else:
                    x1 = min(1.0, x0 + w_adj)

            new_crop = CropRect(x0, y0, x1, y1).normalized()

        self._set_current_crop(new_crop)
        self._redraw_overlay()
        self._schedule_preview_update(immediate=False)

    def _on_mouse_up(self, _event) -> None:
        if self._drag_mode is not None:
            self._drag_mode = None
            self._drag_new_active = False
            self._schedule_preview_update(immediate=True)
            if self._current_crop() != self._drag_start_crop:
                self._push_undo_snapshot(
                    self._drag_start_crop, self._drag_start_preset, self.manual_rotation
                )
                self._set_work_hint("Crop adjusted · Ctrl+Z to undo · Enter to save")

    def _push_undo_snapshot(
        self,
        crop: Optional[CropRect] = None,
        preset: Optional[str] = None,
        rotation: Optional[int] = None,
    ) -> None:
        if self.doc is None:
            return
        snap = (
            crop if crop is not None else self._current_crop(),
            preset if crop is not None else self._crop_preset_by_page.get(self.page_index),
            self.manual_rotation if rotation is None else rotation % 360,
        )
        stack = self._undo_stack.setdefault(self.page_index, [])
        if stack and stack[-1] == snap:
            return
        stack.append(snap)
        if len(stack) > 50:
            del stack[0]
        self._redo_stack.pop(self.page_index, None)

    def _undo(self) -> None:
        if self.doc is None:
            return
        stack = self._undo_stack.get(self.page_index)
        if not stack:
            self._set_work_hint("Nothing to undo.")
            return
        cur = (
            self._current_crop(),
            self._crop_preset_by_page.get(self.page_index),
            self.manual_rotation,
        )
        self._redo_stack.setdefault(self.page_index, []).append(cur)
        crop, preset, rot = stack.pop()
        self.manual_rotation = rot
        self._set_current_crop(crop, preset=preset)
        self._redraw_overlay()
        self._sync_rotation_ui()
        self._schedule_preview_update(immediate=True)
        self._set_work_hint("Undone.")

    def _redo(self) -> None:
        if self.doc is None:
            return
        stack = self._redo_stack.get(self.page_index)
        if not stack:
            self._set_work_hint("Nothing to redo.")
            return
        cur = (
            self._current_crop(),
            self._crop_preset_by_page.get(self.page_index),
            self.manual_rotation,
        )
        self._undo_stack.setdefault(self.page_index, []).append(cur)
        crop, preset, rot = stack.pop()
        self.manual_rotation = rot
        self._set_current_crop(crop, preset=preset)
        self._redraw_overlay()
        self._sync_rotation_ui()
        self._schedule_preview_update(immediate=True)
        self._set_work_hint("Redone.")

    def _nudge_crop(self, dx_mm: float, dy_mm: float, state: int) -> None:
        if self.doc is None or self.current_page_rect is None:
            return
        mult = 5.0 if (state & 0x0001) else 1.0
        step_x = (dx_mm * mult * PT_PER_MM) / self.current_page_rect.width
        step_y = (dy_mm * mult * PT_PER_MM) / self.current_page_rect.height
        c = self._current_crop()
        w, h = c.width, c.height
        nx0 = max(0.0, min(1.0 - w, c.x0 + step_x))
        ny0 = max(0.0, min(1.0 - h, c.y0 + step_y))
        new_crop = CropRect(nx0, ny0, nx0 + w, ny0 + h)
        if new_crop != c:
            self._push_undo_snapshot()
        self._set_current_crop(new_crop)
        self._redraw_overlay()
        self._schedule_preview_update(immediate=False)
        self._set_work_hint("Crop adjusted · Ctrl+Z to undo · Enter to save")

    # ------------------------------------------------------- Controls & Presets
    def _sync_crop_presets(self) -> None:
        preset = self._crop_preset_by_page.get(self.page_index)
        for name, button in self._crop_preset_buttons.items():
            button.set_selected(name == preset)

    def _crop_measurements(self) -> Dict[str, float]:
        assert self.current_page_rect is not None
        crop = self._current_crop()
        width, height = FULL_PAGE_CROP.to_mm(self.current_page_rect)
        return {
            "X": crop.x0 * width, "Y": crop.y0 * height,
            "Width": crop.width * width, "Height": crop.height * height,
        }

    def _sync_crop_measurements(self) -> None:
        if self.current_page_rect is None:
            return
        values = self._crop_measurements()
        for field, variable in self._crop_entries.values():
            text = f"{values[field]:.1f}"
            if variable.get() != text:
                variable.set(text)

    def _on_crop_measurement_commit(self, entry: ttk.Entry) -> str:
        self._apply_crop_measurement(entry)
        return "break"

    def _apply_crop_measurement(self, entry: ttk.Entry) -> bool:
        if self.current_page_rect is None:
            return True
        field, variable = self._crop_entries[entry]
        text = variable.get().strip()
        try:
            value_mm = float(text.replace(",", "."))
        except ValueError:
            value_mm = math.nan
        if not math.isfinite(value_mm):
            self._sync_crop_measurements()
            self._set_feedback(f"Enter a number for {field.lower()} (mm).", warning=True)
            self.root.bell()
            return False
        if value_mm == round(self._crop_measurements()[field], 1):
            self._sync_crop_measurements()
            return True

        crop = self._current_crop()
        horizontal = field in ("X", "Width")
        if horizontal:
            start, end = crop.x0, crop.x1
            extent_mm = self.current_page_rect.width / PT_PER_MM
        else:
            start, end = crop.y0, crop.y1
            extent_mm = self.current_page_rect.height / PT_PER_MM
        if field in ("X", "Y"):
            span = end - start
            start = min(1.0 - span, max(0.0, value_mm / extent_mm))
            end = start + span
        else:
            end = min(1.0, start + max(0.0, value_mm / extent_mm))
        new_crop = (
            CropRect(start, crop.y0, end, crop.y1) if horizontal
            else CropRect(crop.x0, start, crop.x1, end)
        ).normalized()
        if new_crop != crop:
            self._push_undo_snapshot()
        self._set_current_crop(new_crop)
        if self._current_crop() != crop:
            self._redraw_overlay()
            self._schedule_preview_update(immediate=True)
            self._set_work_hint("Crop adjusted · Ctrl+Z to undo · Enter to save")
        return True

    def _cancel_crop_measurement(self, _event) -> str:
        self._sync_crop_measurements()
        (self._expanded_preview or self.canvas).focus_set()
        return "break"

    def _finish_crop_measurement(self, event=None) -> Optional[str]:
        if event is not None and event.type == tk.EventType.ButtonPress and event.widget in self._crop_entries:
            return None
        entry = self.root.focus_get()
        if entry in self._crop_entries:
            if not self._apply_crop_measurement(entry):
                return "break"
            (self._expanded_preview or self.canvas).focus_set()
        return None

    def _validate_active_measurement(self) -> bool:
        entry = self.root.focus_get()
        if entry in self._crop_entries:
            if not self._apply_crop_measurement(entry):
                return False
            (self._expanded_preview or self.canvas).focus_set()
        return True

    def _format_label_text(self) -> str:
        width, height = LABEL_SIZES_MM.get(self.label_size_var.get(), (100.0, 150.0))
        if (width, height) == (101.6, 152.4):
            return "4×6″"
        return f"{width / 10:g} × {height / 10:g} cm"

    def _set_label_size(self, key: str) -> None:
        self.label_size_var.set(key)
        self._sync_format_buttons()
        self._sync_status_format()
        if getattr(self, "_empty_after_size", None) is not None:
            self._empty_after_size.configure(text=f"{self._format_label_text()} label")
        self._set_work_hint(f"Output format set to {self._format_label_text()}.")
        self._on_settings_changed()

    def _sync_status_format(self) -> None:
        self._status_format.configure(text=self._format_label_text())

    def _sync_format_buttons(self) -> None:
        selected = self.label_size_var.get()
        for key, button in self._format_buttons.items():
            button.set_selected(key == selected)

    def _set_margin(self, mm: float) -> None:
        self.margin_mm_var.set(mm)
        self._sync_margin_buttons()
        self._set_work_hint(f"Margin set to {mm:g} mm.")
        self._on_settings_changed()

    def _sync_margin_buttons(self) -> None:
        cur = round(self.margin_mm_var.get())
        for val, btn in self._margin_btns.items():
            btn.set_selected(int(val) == int(cur))

    def _sync_rotation_ui(self) -> None:
        rot = self.manual_rotation
        self._rotation_meta.configure(text=f"{rot}°", fg=INK if rot else MUTED)
        if rot and self.doc is not None:
            if not self._rotation_reset_btn.winfo_manager():
                self._rotation_reset_btn.pack(side=tk.RIGHT, padx=(0, 6))
        else:
            if self._rotation_reset_btn.winfo_manager():
                self._rotation_reset_btn.pack_forget()

    def _sync_action_labels(self) -> None:
        if self.doc is not None and len(self.doc) > 1 and self.apply_all_pages_var.get():
            count = len(self.doc)
            self._save_btn.set_text(f"Save ({count} pages)")
            self._direct_print_btn.set_text(f"Direct Print ({count})")
        else:
            self._save_btn.set_text("Save")
            self._direct_print_btn.set_text("Direct Print")

    def _reset_rotation(self) -> None:
        if self.doc is None or self.manual_rotation == 0:
            return
        self._push_undo_snapshot()
        self.manual_rotation = 0
        self._sync_rotation_ui()
        self._schedule_preview_update(immediate=True)
        self._set_work_hint("Rotation reset to 0°.")

    def _rotate_by(self, delta: int) -> None:
        if self.doc is None:
            return
        self._push_undo_snapshot()
        self.manual_rotation = (self.manual_rotation + delta) % 360
        self._sync_rotation_ui()
        self._schedule_preview_update(immediate=True)
        self._set_work_hint(f"Rotated to {self.manual_rotation}°.")

    def _on_settings_changed(self) -> None:
        self._schedule_preview_update(immediate=True)

    def _apply_quick_preset(self, name: str) -> None:
        if self.doc is None:
            return
        self._drag_mode = None
        self._drag_new_active = False
        if name == "Auto-detect":
            crop = detect_label_bounds(self.doc, self.page_index, padding_mm=3.0)
        else:
            crop = QUICK_PRESETS[name] or FULL_PAGE_CROP
        if crop != self._current_crop():
            self._push_undo_snapshot()
        self._set_current_crop(crop, preset=name)
        self._redraw_overlay()
        self._schedule_preview_update(immediate=True)
        self._set_work_hint("Label bounds detected." if name == "Auto-detect" else f"Crop set to {name.lower()}.")

    # ------------------------------------------------------------- Live Preview
    def _schedule_preview_update(self, immediate: bool = False) -> None:
        if self._preview_after_id:
            self.root.after_cancel(self._preview_after_id)
            self._preview_after_id = None
        if immediate:
            self._update_preview()
        else:
            self._preview_after_id = self.root.after(85, self._update_preview)

    def _update_preview(self) -> None:
        self._preview_after_id = None
        if self._expanded_preview is not None:
            self._expanded_preview.refresh_source()
        width = self.preview_canvas.winfo_width()
        height = self.preview_canvas.winfo_height()
        w_mm, h_mm = LABEL_SIZES_MM.get(self.label_size_var.get(), (100.0, 150.0))
        size_text = f"{w_mm / 10:g} × {h_mm / 10:g} cm"
        if self.doc is None:
            self.preview_meta.configure(text=size_text)
            self.preview_canvas.delete("all")
            self.preview_canvas.create_text(
                width // 2, height // 2, text="—", fill=DISABLED, font=ui_font(14)
            )
            return
        if width <= 32 or height <= 28:
            return
        try:
            img, eff_rot = render_label_preview(
                src_doc=self.doc,
                page_index=self.page_index,
                crop=self._current_crop(),
                manual_rotation=self.manual_rotation,
                margin_mm=self.margin_mm_var.get(),
                label_w_mm=w_mm,
                label_h_mm=h_mm,
                max_width=width - 32,
                max_height=height - 28,
            )
        except Exception:
            return

        rot_txt = f" · {eff_rot}°" if eff_rot else ""
        self.preview_meta.configure(text=f"{size_text}{rot_txt}")

        self._preview_photo = ImageTk.PhotoImage(img)
        self.preview_canvas.delete("all")
        self.preview_canvas.configure(cursor="hand2")
        px0 = (width - img.width) // 2
        py0 = (height - img.height) // 2
        self.preview_canvas.create_image(px0, py0, anchor="nw", image=self._preview_photo)
        self.preview_canvas.create_rectangle(
            px0 - 1, py0 - 1, px0 + img.width, py0 + img.height, outline=BORDER_STRONG, width=1
        )
        if self._preview_hovered:
            self._draw_preview_hover_hint()
        self._sync_preview_border()

    def _is_preview_active(self) -> bool:
        if self.doc is None:
            return False
        focused = self.root.focus_get() == self.preview_canvas
        return self._preview_hovered or focused or self._expanded_preview is not None

    def _sync_preview_border(self) -> None:
        active = self._is_preview_active()
        color = INK if active else BORDER
        self._preview_top_border.configure(bg=color)
        self._preview_bottom_border.configure(bg=color)
        self.preview_canvas.delete("active_sides")
        if active:
            w = self.preview_canvas.winfo_width()
            h = self.preview_canvas.winfo_height()
            if w > 2 and h > 2:
                self.preview_canvas.create_line(0, 0, 0, h, fill=INK, width=1, tags="active_sides")
                self.preview_canvas.create_line(w - 1, 0, w - 1, h, fill=INK, width=1, tags="active_sides")

    def _draw_preview_hover_hint(self) -> None:
        self.preview_canvas.delete("hover_hint")
        hint_text = " Click to close " if self._expanded_preview is not None else " Click to enlarge "
        txt_id = self.preview_canvas.create_text(
            self.preview_canvas.winfo_width() // 2,
            self.preview_canvas.winfo_height() - 16,
            text=hint_text,
            fill=WHITE,
            font=ui_font(8, semibold=True),
            tags="hover_hint",
        )
        tb = self.preview_canvas.bbox(txt_id)
        if tb:
            bg_id = self.preview_canvas.create_rectangle(
                tb[0] - 5,
                tb[1] - 2,
                tb[2] + 5,
                tb[3] + 2,
                fill=INK,
                outline="",
                tags="hover_hint",
            )
            self.preview_canvas.tag_lower(bg_id, txt_id)

    def _on_preview_enter(self, _event=None) -> None:
        self._preview_hovered = True
        if self.doc is None:
            return
        self.preview_canvas.configure(cursor="hand2")
        self._draw_preview_hover_hint()
        self._sync_preview_border()

    def _on_preview_leave(self, _event=None) -> None:
        self._preview_hovered = False
        self.preview_canvas.delete("hover_hint")
        self._sync_preview_border()

    def _toggle_expanded_preview(self, event) -> str:
        if self._expanded_preview is not None:
            self._close_expanded_preview()
        elif self.doc is not None:
            self._open_expanded_preview(focus_canvas=event.type == tk.EventType.KeyPress)
        return "break"

    def _expanded_preview_state(self):
        if self.doc is None:
            return None
        w_mm, h_mm = LABEL_SIZES_MM.get(self.label_size_var.get(), (100.0, 150.0))
        return (
            self.doc,
            self.page_index,
            self._current_crop(),
            self.manual_rotation,
            w_mm,
            h_mm,
            self.margin_mm_var.get(),
        )

    def _open_expanded_preview(self, *, focus_canvas: bool) -> None:
        view = ExpandedPreviewFrame(
            self._workspace,
            header_height=self._preview_header.winfo_reqheight(),
            get_state=self._expanded_preview_state,
            on_rotate=self._rotate_by,
            on_close=self._close_expanded_preview,
            on_canvas_down=self._finish_crop_measurement,
        )
        self._expanded_preview = view

        # Lay out and paint the new view behind the crop before revealing it.
        view.lower(self._crop_view)
        view.grid(row=0, column=0, sticky="nsew")
        self._workspace.update_idletasks()
        view.refresh_source()
        self._workspace.update_idletasks()
        view.bind_configure()
        self._crop_view.grid_remove()
        self._sync_preview_border()
        if self._preview_hovered:
            self._draw_preview_hover_hint()
        view.focus_initial(focus_canvas=focus_canvas)

    def _close_expanded_preview(self, *, restore_focus: bool = True) -> None:
        if self._expanded_preview is None:
            return
        self._expanded_preview.destroy()
        self._expanded_preview = None
        self._crop_view.grid()
        if restore_focus:
            self.preview_canvas.focus_set()
        self._sync_preview_border()
        if self._preview_hovered:
            self._draw_preview_hover_hint()

    def _on_escape(self) -> None:
        if self._drag_mode is not None:
            self._drag_mode = None
            self._drag_new_active = False
            self._set_current_crop(self._drag_start_crop, preset=self._drag_start_preset)
            self._redraw_overlay()
            self._schedule_preview_update(immediate=True)
            self._set_work_hint("Crop drag canceled.")
            return
        if self._active_dialog is not None and self._active_dialog.winfo_exists():
            self._active_dialog.destroy()
            self._active_dialog = None
            return
        if self._expanded_preview is not None:
            self._close_expanded_preview()
            return
        self.canvas.focus_set()

    def _show_dialog(
        self,
        title: str,
        heading: str,
        message: str,
        *,
        confirm_text: str = "OK",
        cancel_text: Optional[str] = None,
        destructive: bool = False,
    ) -> bool:
        """Minimal custom modal dialog matching the app's monochrome design."""
        result = [False]
        previous_dialog = self._active_dialog
        previous_focus = self.root.focus_get()
        previous_grab = self.root.grab_current()
        parent = previous_dialog if previous_dialog is not None and previous_dialog.winfo_exists() else self.root

        dlg = tk.Toplevel(parent)
        self._active_dialog = dlg
        dlg.title(title)
        dlg.configure(bg=WHITE)
        dlg.resizable(False, False)
        dlg.transient(parent)

        body = tk.Frame(dlg, bg=WHITE)
        body.pack(fill=tk.BOTH, expand=True, padx=22, pady=(20, 18))

        tk.Label(
            body,
            text=heading,
            bg=WHITE,
            fg=INK,
            font=ui_font(11, semibold=True),
            anchor="w",
            justify=tk.LEFT,
            wraplength=296,
        ).pack(fill=tk.X, pady=(0, 6) if message else (0, 0))

        if message:
            tk.Label(
                body,
                text=message,
                bg=WHITE,
                fg=MUTED,
                font=ui_font(9),
                anchor="w",
                justify=tk.LEFT,
                wraplength=296,
            ).pack(fill=tk.X)

        footer = tk.Frame(dlg, bg=WHITE)
        footer.pack(fill=tk.X, padx=22, pady=(0, 18))

        def _finish(val: bool) -> str:
            result[0] = val
            if dlg.winfo_exists():
                dlg.destroy()
            return "break"

        accept = MinimalButton(
            footer, text=confirm_text, command=lambda: _finish(True),
            variant="danger" if destructive else "primary",
            font=ui_font(9, semibold=True), pady=7, takefocus=True,
        )
        default_button = accept
        if cancel_text:
            footer.columnconfigure(0, weight=1, uniform="dlg_btn")
            footer.columnconfigure(1, weight=1, uniform="dlg_btn")
            cancel = MinimalButton(
                footer,
                text=cancel_text,
                command=lambda: _finish(False),
                variant="secondary",
                font=ui_font(9),
                pady=7,
                takefocus=True,
            )
            cancel.grid(row=0, column=0, sticky="ew", padx=(0, 6))
            accept.grid(row=0, column=1, sticky="ew", padx=(6, 0))
            default_button = cancel if destructive else accept
        else:
            accept.pack(fill=tk.X)

        dlg.update_idletasks()
        w = max(332, dlg.winfo_reqwidth())
        h = dlg.winfo_reqheight()
        self.root.update_idletasks()
        scr_w = self.root.winfo_screenwidth()
        scr_h = self.root.winfo_screenheight()
        rx = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
        ry = self.root.winfo_rooty() + (self.root.winfo_height() - h) // 2
        rx = max(20, min(scr_w - w - 20, rx))
        ry = max(20, min(scr_h - h - 40, ry))
        dlg.geometry(f"{w}x{h}+{rx}+{ry}")

        dlg.protocol("WM_DELETE_WINDOW", lambda: _finish(False))
        dlg.bind("<Escape>", lambda _e: _finish(False))
        dlg.bind("<Return>", lambda _e: _finish(not destructive))
        dlg.grab_set()
        default_button.focus_set()
        self.root.wait_window(dlg)
        self._active_dialog = previous_dialog
        if previous_grab is not None and previous_grab.winfo_exists():
            previous_grab.grab_set()
        if previous_focus is not None and previous_focus.winfo_exists() and previous_focus.winfo_viewable():
            previous_focus.focus_set()
        return result[0]

    def _show_operation_error(self, heading: str, message: str) -> None:
        self._set_feedback(heading, warning=True)
        self._show_dialog("Error", heading, message)

    # ------------------------------------------------------------ Export & Print
    def _collect_export_pages(self) -> List[Tuple[int, CropRect]]:
        assert self.doc is not None
        known_pages = set(self.page_crops.keys())
        pages = collect_export_pages(
            self.doc, self.page_index, self.page_crops, self.apply_all_pages_var.get()
        )
        for idx, _ in pages:
            if idx not in known_pages:
                self._crop_preset_by_page[idx] = "Auto-detect"
        return pages

    def _run_export(
        self,
        output_path: Path,
        *,
        confirm_replace: bool = True,
        for_print: bool = False,
        page_crops: Optional[List[Tuple[int, CropRect]]] = None,
    ) -> Optional[Path]:
        if self.doc is None or self.pdf_path is None:
            self._set_feedback("Open a PDF file first.")
            return None

        w_mm, h_mm = LABEL_SIZES_MM.get(self.label_size_var.get(), (100.0, 150.0))
        destination = output_path
        preparing = "Unable to prepare PDF for printing" if for_print else "Unable to save PDF"
        try:
            destination = output_path.resolve()
            if confirm_replace and destination.is_file():
                prefix = "Direct Print also saves a PDF copy.\n\n" if for_print else ""
                confirmed = self._show_dialog(
                    "Replace PDF", "Replace the existing PDF?",
                    f"{prefix}{output_path.name} already exists in {output_path.parent}.\n"
                    "Replacing it will overwrite the previous version.",
                    confirm_text="Replace", cancel_text="Cancel", destructive=True,
                )
                if not confirmed:
                    self._set_feedback("Printing canceled." if for_print else "Save canceled.")
                    return None

            self._set_feedback("Preparing PDF for printing…" if for_print else "Saving PDF…")
            self.root.update_idletasks()

            replacing_source = destination == self.pdf_path.resolve()
            pages = page_crops if page_crops is not None else self._collect_export_pages()
            # Stage beside the destination so a failed export leaves the previous file intact.
            with tempfile.TemporaryDirectory(prefix=".crop-", dir=destination.parent) as folder:
                saved = export_cropped_pdf(
                    src_doc=self.doc,
                    output_path=Path(folder) / "label.pdf",
                    page_crops=pages,
                    manual_rotation=self.manual_rotation,
                    margin_mm=self.margin_mm_var.get(),
                    label_w_mm=w_mm,
                    label_h_mm=h_mm,
                    page_rotations=dict(self.page_rotations),
                )
                if replacing_source:
                    self.doc.close()
                try:
                    os.replace(saved, destination)
                finally:
                    if replacing_source:
                        self.doc = fitz.open(str(self.pdf_path))
            if replacing_source:
                self.page_rotations = {}
                self.page_index = 0
                self.page_crops = {0: FULL_PAGE_CROP}
                self._crop_preset_by_page = {0: "Full Page"}
                self._undo_stack = {}
                self._redo_stack = {}
                self._load_current_page()
            return output_path
        except (PermissionError, FileNotFoundError, IsADirectoryError, NotADirectoryError):
            if not for_print:
                self._set_feedback(
                    "PDF not saved · Couldn’t write to the destination.", warning=True,
                    action=("Save As…", self._save_as),
                )
                return None
            self._show_operation_error(
                preparing,
                f"Direct Print needs write access to {destination.parent}. "
                "Check the folder permissions and try again.",
            )
        except Exception as exc:
            self._show_operation_error(preparing, f"Check the PDF and the destination, then try again.\n\n{exc}")
        return None

    def _output_format_suffix(self) -> str:
        w_mm, h_mm = LABEL_SIZES_MM.get(self.label_size_var.get(), (100.0, 150.0))
        if (w_mm, h_mm) == (101.6, 152.4):
            return "_4x6"
        return f"_{w_mm / 10:g}x{h_mm / 10:g}"

    def _default_output_path(self) -> Path:
        assert self.pdf_path is not None
        stem = self.pdf_path.stem
        suffix = self._output_format_suffix()
        if stem.endswith(suffix):
            return self.pdf_path.with_name(f"{stem}_crop.pdf")
        return self.pdf_path.with_name(f"{stem}{suffix}.pdf")

    def _reveal_in_file_manager(self, path: Path) -> None:
        try:
            resolved = path.resolve()
            if sys.platform == "win32":
                subprocess.Popen(["explorer", f"/select,{resolved}"])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(resolved)])
            else:
                subprocess.Popen(["xdg-open", str(resolved.parent)])
        except Exception:
            self._set_feedback("Could not open file manager.", warning=True)

    def _save_quick(self) -> None:
        if self.doc is None or self.pdf_path is None:
            self._set_feedback("Open a PDF file first.")
            return
        if not self._validate_active_measurement():
            return
        saved = self._run_export(self._default_output_path())
        if saved:
            self._last_saved_path = saved
            self._set_feedback(
                f"Saved: {saved.name}",
                bold=True,
                action=("Show in folder", lambda p=saved: self._reveal_in_file_manager(p)),
            )

    def _save_as(self) -> None:
        if self.doc is None or self.pdf_path is None:
            self._set_feedback("Open a PDF file first.")
            return
        if not self._validate_active_measurement():
            return
        default = self._default_output_path()
        chosen = filedialog.asksaveasfilename(
            title="Save As",
            parent=self.root,
            initialdir=str(default.parent),
            initialfile=default.name,
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf")],
        )
        if not chosen:
            return
        saved = self._run_export(Path(chosen), confirm_replace=False)
        if saved:
            self._last_saved_path = saved
            self._set_feedback(
                f"Saved: {saved.name}",
                bold=True,
                action=("Show in folder", lambda p=saved: self._reveal_in_file_manager(p)),
            )

    def _direct_print_dialog(self) -> None:
        if self._print_in_progress or self._listing_printers:
            return
        if self.doc is None or self.pdf_path is None:
            self._set_feedback("Open a PDF file first.")
            return
        if not self._validate_active_measurement():
            return

        self._listing_printers = True
        self._direct_print_btn.set_enabled(False)
        self._set_feedback("Finding printers…")

        def _on_printers_loaded(printers: Any, exc: Optional[BaseException]) -> None:
            self._listing_printers = False
            if not self._print_in_progress and self.doc is not None:
                self._direct_print_btn.set_enabled(True)
            if self.doc is None or self.pdf_path is None:
                return
            if exc is not None or not printers:
                self._show_operation_error(
                    "No printers found", "Add a printer in your system settings, then try Direct Print again.",
                )
                return

            self._set_feedback("Drag edges to adjust · Shift+Drag locks ratio · Enter to save")

            def _set_active_dialog(dlg: Optional[tk.Toplevel]) -> None:
                self._active_dialog = dlg

            page_count = len(self.doc) if self.apply_all_pages_var.get() else 1
            label_word = "label" if page_count == 1 else "labels"
            summary = f"{page_count} {label_word}  ·  {self._format_label_text()}"
            DirectPrintDialog(
                self.root,
                printers,
                self._printer_caps_cache,
                on_active_dialog=_set_active_dialog,
                show_dialog=self._show_dialog,
                show_error=self._show_operation_error,
                on_confirm_print=self._start_print_job,
                job_summary=summary,
                page_count=page_count,
            )

        run_in_background(self.root, list_printers, _on_printers_loaded)

    def _start_print_job(self, job: Dict[str, Any]) -> bool:
        if self.doc is None or self.pdf_path is None:
            return False
        export_pages = self._collect_export_pages()
        w_mm, h_mm = LABEL_SIZES_MM.get(self.label_size_var.get(), (100.0, 150.0))

        try:
            fd, tmp_pdf_name = tempfile.mkstemp(prefix="croppdf_job_", suffix=".pdf")
            os.close(fd)
            job_pdf = Path(tmp_pdf_name)
        except OSError as exc:
            self._show_operation_error(
                "Unable to prepare PDF for printing",
                f"Check temporary folder permissions and try again.\n\n{exc}",
            )
            return False

        try:
            self._set_feedback("Preparing PDF for printing…")
            self.root.update_idletasks()
            export_cropped_pdf(
                src_doc=self.doc,
                output_path=job_pdf,
                page_crops=export_pages,
                manual_rotation=self.manual_rotation,
                margin_mm=self.margin_mm_var.get(),
                label_w_mm=w_mm,
                label_h_mm=h_mm,
                page_rotations=dict(self.page_rotations),
            )
        except Exception as exc:
            job_pdf.unlink(missing_ok=True)
            self._show_operation_error(
                "Unable to prepare PDF for printing",
                f"Check the PDF and try again.\n\n{exc}",
            )
            return False

        chosen = str(job["printer_name"])
        copies = max(1, min(99, int(job.get("copies", 1))))
        page_count = len(export_pages)

        self._print_in_progress = True
        self._direct_print_btn.set_enabled(False)
        self._set_feedback(f"Sending to {chosen}…")

        def _work() -> Tuple[bool, str]:
            try:
                imgs = render_pdf_for_printing(job_pdf, dpi=300) if sys.platform == "win32" else []
                return print_images_to_thermal_printer(
                    imgs,
                    chosen,
                    label_w_mm=w_mm,
                    label_h_mm=h_mm,
                    pdf_path=job_pdf,
                    page_count=page_count,
                    copies=copies,
                    darkness=job["darkness"],
                    speed_option=job["speed_option"],
                    darkness_param=job["darkness_param"],
                    speed_feature=job["speed_feature"],
                    use_current_feature=job["use_current_feature"],
                    use_current_no_option=job["use_current_no_option"],
                )
            finally:
                try:
                    job_pdf.unlink(missing_ok=True)
                except OSError:
                    pass

        def _on_done(result: Any, exc: Optional[BaseException]) -> None:
            self._print_in_progress = False
            if self.doc is not None:
                self._direct_print_btn.set_enabled(True)
            if exc is not None:
                self._show_operation_error(
                    "Unable to send print job", f"Check the printer queue before trying again.\n\n{exc}",
                )
                return
            ok, msg = result
            if ok:
                self._set_feedback(msg or f"Sent to {chosen}.", bold=True)
            else:
                reason = msg or f"Check the queue for {chosen}."
                self._set_feedback(
                    f"Print failed · {reason}", warning=True,
                    action=("Change printer", self._direct_print_dialog),
                )

        run_in_background(self.root, _work, _on_done)
        return True

    # ---------------------------------------------------- Context Menu Toggle
    def _refresh_menu_badge(self) -> None:
        self.menu_badge.set_active(is_context_menu_installed())

    def _show_file_manager_help(self) -> None:
        self._refresh_menu_badge()
        can_enable = not (self.menu_badge.is_active or self.menu_badge.is_busy)
        if self.menu_badge.is_busy:
            status = "The file manager menu is being updated."
        elif self.menu_badge.is_active:
            status = "The file manager menu is active."
        else:
            status = "Enable the file manager menu to open PDFs directly."
        confirmed = self._show_dialog(
            self._menu_strings["dialog_title"],
            "Open from file manager",
            f'{status}\n\n{self._menu_strings["open_hint"]}',
            confirm_text="Enable" if can_enable else "Close",
            cancel_text="Cancel" if can_enable else None,
        )
        if confirmed and can_enable:
            self._toggle_context_menu(enable_only=True)

    def _toggle_context_menu(self, *, enable_only: bool = False) -> None:
        if self.menu_badge.is_busy:
            return
        if is_context_menu_installed():
            if enable_only:
                self._refresh_menu_badge()
                return
            if not self._show_dialog(
                self._menu_strings["dialog_title"],
                self._menu_strings["remove_heading"],
                self._menu_strings["remove_message"],
                confirm_text="Remove",
                cancel_text="Cancel",
                destructive=True,
            ):
                return
            self.menu_badge.start_spinner("Removing...")

            def _finish_remove(result: Any, exc: Optional[BaseException]) -> None:
                self.menu_badge.stop_spinner()
                self._refresh_menu_badge()
                ok, msg = (False, str(exc)) if exc is not None else result
                if ok:
                    self._set_feedback("Right-click menu removed.", bold=True)
                else:
                    self._show_dialog("Error", "Unable to remove menu", msg)

            run_in_background(self.root, uninstall_context_menu, _finish_remove)
        else:
            self.menu_badge.start_spinner("Enabling...")

            def _finish_install(result: Any, exc: Optional[BaseException]) -> None:
                self.menu_badge.stop_spinner()
                self._refresh_menu_badge()
                ok, msg = (False, str(exc)) if exc is not None else result
                if ok:
                    self._set_feedback("Right-click menu enabled.", bold=True)
                else:
                    self._show_dialog("Error", "Unable to enable menu", msg)

            run_in_background(self.root, install_context_menu, _finish_install)

    def _invoke_feedback_action(self, _event) -> str:
        self._feedback_action.invoke()
        return "break"

    def _set_work_hint(self, text: str) -> None:
        if not self._feedback_action.winfo_manager():
            self._set_feedback(text)

    def _set_feedback(
        self, text: str, bold: bool = False, *, warning: bool = False,
        action: Optional[Tuple[str, Callable[[], None]]] = None,
    ) -> None:
        kind = "error" if warning else "success" if bold else "info"
        self._feedback_icon.configure(image=self._feedback_icons[kind])
        self.feedback_label.configure(
            fg=STATUS_STYLES[kind][0],
            font=ui_font(8, semibold=bold or warning),
        )
        self.feedback_label.set_text(" ".join(text.splitlines()))
        if action:
            label, command = action
            self._feedback_action.configure(text=label, command=command)
            self._feedback_action.grid(row=0, column=1, padx=(8, 0))
        else:
            if self.root.focus_get() == self._feedback_action:
                self.canvas.focus_set()
            self._feedback_action.grid_remove()

    def _on_close(self) -> None:
        self.menu_badge.stop_spinner()
        if self._drop_handler is not None:
            self._drop_handler.uninstall()
            self._drop_handler = None
        self._close_expanded_preview(restore_focus=False)
        if sys.platform == "win32":
            for hicon in self._win_icons:
                if hicon:
                    try:
                        ctypes.windll.user32.DestroyIcon(ctypes.c_void_p(hicon))
                    except Exception:
                        pass
            self._win_icons = (0, 0)
        if self.doc is not None and not self.doc.is_closed:
            try:
                self.doc.close()
            except Exception:
                pass
        self.root.destroy()

    def run(self) -> None:
        try:
            self.root.mainloop()
        finally:
            if self.doc is not None and not self.doc.is_closed:
                try:
                    self.doc.close()
                except Exception:
                    pass
