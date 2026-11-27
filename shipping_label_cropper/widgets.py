"""Reusable monochrome Tkinter widgets, theme constants, and UI helpers."""

from __future__ import annotations

import math
import queue
import sys
import threading
import tkinter as tk
from bisect import bisect_right
from tkinter import ttk
from typing import Any, Callable, Dict, List, Optional, Tuple

import pymupdf as fitz
from PIL import Image, ImageDraw, ImageTk

from .pdf_engine import render_page_image

# Minimalist Monochrome Palette (Black & White)
WHITE = "#FFFFFF"
CANVAS_BG = "#F4F4F5"
SUBTLE_BG = "#FAFAFA"
HOVER_BG = "#F0F0F2"
PRESSED_BG = "#E4E4E7"
BORDER = "#E4E4E7"
BORDER_STRONG = "#D4D4D8"
INK = "#09090B"
INK_HOVER = "#27272A"
INK_PRESSED = "#3F3F46"
MUTED = "#52525B"
DISABLED = "#A1A1AA"

STATUS_STYLES = {
    "info": (MUTED, (((8, 5), (8, 6)), ((8, 8), (8, 11)))),
    "success": ("#216345", (((5, 8), (7, 10), (11, 6)),)),
    "error": ("#923323", (((6, 6), (10, 10)), ((10, 6), (6, 10)))),
}

ACTION_ICON_PATHS = {
    "folder": (
        ((2, 13), (2, 3), (6, 3), (8, 5), (13, 5), (13, 7)),
        ((2, 13), (4, 7), (15, 7), (13, 13), (2, 13)),
    ),
    "save": (
        ((2, 2), (11, 2), (14, 5), (14, 14), (2, 14), (2, 2)),
        ((5, 2), (5, 6), (10, 6), (10, 2)),
        ((5, 14), (5, 9), (11, 9), (11, 14)),
    ),
    "printer": (
        ((4, 6), (4, 2), (12, 2), (12, 6)),
        ((4, 11), (1, 11), (1, 6), (15, 6), (15, 11), (12, 11)),
        ((4, 9), (12, 9), (12, 14), (4, 14), (4, 9)),
    ),
    "auto-crop": (
        ((2, 6), (2, 3), (5, 3)),
        ((11, 3), (14, 3), (14, 6)),
        ((14, 10), (14, 13), (11, 13)),
        ((5, 13), (2, 13), (2, 10)),
        ((5, 8), (11, 8)),
    ),
    "full-page": (
        ((9, 3), (13, 3), (13, 7)),
        ((13, 3), (9, 7)),
        ((3, 9), (3, 13), (7, 13)),
        ((3, 13), (7, 9)),
    ),
    "rotate-left": (
        ((2, 2), (2, 6), (6, 6)),
        ((2, 6), (5, 3), (8, 2), (11, 3), (13, 5), (14, 8),
         (13, 11), (11, 13), (8, 14), (5, 13), (3, 11)),
    ),
    "rotate-right": (
        ((14, 2), (14, 6), (10, 6)),
        ((14, 6), (11, 3), (8, 2), (5, 3), (3, 5), (2, 8),
         (3, 11), (5, 13), (8, 14), (11, 13), (13, 11)),
    ),
}

SIDEBAR_WIDTH = 304
CONTROL_RADIUS = 4
HANDLE_RADIUS = 5
HANDLE_HIT = 13
EDGE_HIT = 7


def _ui_font_family() -> str:
    if sys.platform == "win32":
        return "Segoe UI"
    if sys.platform == "darwin":
        return "Helvetica Neue"
    return "DejaVu Sans"


UI_FONT = _ui_font_family()


def ui_font(size: int, semibold: bool = False) -> Tuple[Any, ...]:
    """Return a platform-appropriate Tkinter font tuple."""
    scaled = size + 2 if sys.platform == "darwin" else size
    if semibold:
        if sys.platform == "win32":
            return ("Segoe UI Semibold", scaled)
        return (UI_FONT, scaled, "bold")
    return (UI_FONT, scaled)


def _wheel_steps(event: Any) -> float:
    """Normalize mouse wheel / trackpad scroll events across Windows, macOS, and Linux X11."""
    num = getattr(event, "num", None)
    if num == 4:
        return 1.0
    if num == 5:
        return -1.0
    delta = getattr(event, "delta", 0)
    if not delta:
        return 0.0
    if sys.platform == "darwin":
        return float(delta)
    return float(delta) / 120.0


def _platform_cursor(hit: str) -> str:
    """Return a valid Tk cursor name for crop-handle hit tests on Windows, macOS, and Linux."""
    if sys.platform == "win32":
        return {
            "nw": "size_nw_se",
            "se": "size_nw_se",
            "ne": "size_ne_sw",
            "sw": "size_ne_sw",
            "n": "size_ns",
            "s": "size_ns",
            "e": "size_we",
            "w": "size_we",
            "move": "fleur",
            "new": "crosshair",
        }.get(hit, "crosshair")
    return {
        "nw": "top_left_corner",
        "se": "bottom_right_corner",
        "ne": "top_right_corner",
        "sw": "bottom_left_corner",
        "n": "sb_v_double_arrow",
        "s": "sb_v_double_arrow",
        "e": "sb_h_double_arrow",
        "w": "sb_h_double_arrow",
        "move": "fleur",
        "new": "crosshair",
    }.get(hit, "crosshair")


def _safe_set_cursor(widget: tk.Misc, cursor: str) -> None:
    try:
        widget.configure(cursor=cursor)  # type: ignore[call-arg]
    except tk.TclError:
        try:
            widget.configure(cursor="arrow")  # type: ignore[call-arg]
        except tk.TclError:
            pass


def run_in_background(
    widget: tk.Misc,
    work: Callable[[], Any],
    on_done: Callable[[Any, Optional[BaseException]], None],
    *,
    poll_ms: int = 40,
) -> None:
    """Run `work` on a daemon thread and deliver `(result, error)` safely on the Tk main thread."""
    result_queue: "queue.Queue[Tuple[Any, Optional[BaseException]]]" = queue.Queue(maxsize=1)

    def _worker() -> None:
        try:
            result_queue.put((work(), None))
        except BaseException as exc:
            result_queue.put((None, exc))

    def _poll() -> None:
        if not widget.winfo_exists():
            return
        try:
            value, exc = result_queue.get_nowait()
        except queue.Empty:
            widget.after(poll_ms, _poll)
            return
        on_done(value, exc)

    threading.Thread(target=_worker, daemon=True).start()
    widget.after(poll_ms, _poll)


def _rounded_control_image(
    size: Tuple[int, int],
    fill: str,
    border: Optional[str],
    *,
    inset: int = 0,
    focused: bool = False,
) -> Image.Image:
    # Supersample the corners while keeping the straight border one pixel wide.
    scale = 4
    radius = CONTROL_RADIUS * scale
    inset_px = inset * scale
    width, height = size
    image = Image.new("RGBA", (width * scale, height * scale))
    draw = ImageDraw.Draw(image)
    right, bottom = image.width - 1, image.height - 1
    if focused:
        if inset >= 2:
            draw.rounded_rectangle(
                (0, 0, right, bottom),
                radius + inset_px,
                outline=INK,
                width=scale,
            )
        else:
            draw.rounded_rectangle((0, 0, right, bottom), radius + inset_px, fill=INK)
    draw.rounded_rectangle(
        (inset_px, inset_px, right - inset_px, bottom - inset_px),
        radius, fill=fill, outline=border, width=scale,
    )
    return image.resize(size, Image.Resampling.BOX)


def _render_action_icon_image(
    icon: str,
    size: int,
    gap: int,
    color: str,
) -> Image.Image:
    """Render an anti-aliased 4x supersampled monochrome action icon as RGBA."""
    scale = 4
    canvas_w = (size + gap) * scale
    canvas_h = size * scale
    img = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    stroke = max(scale, round((size / 12.0) * scale))
    for path in ACTION_ICON_PATHS[icon]:
        points = [(x * size * scale / 16.0, y * size * scale / 16.0) for x, y in path]
        draw.line(points, fill=color, width=stroke, joint="curve")
    return img.resize((size + gap, size), Image.Resampling.LANCZOS)


class Tooltip:
    """Minimal monochrome hover tooltip with delay and screen-edge clamping."""

    def __init__(self, widget: tk.Widget, text: str, *, delay_ms: int = 450) -> None:
        self._widget = widget
        self._text = text
        self._delay_ms = delay_ms
        self._after_id: Optional[str] = None
        self._popup: Optional[tk.Toplevel] = None
        self._bind_widget(widget)

    def _bind_widget(self, w: tk.Widget) -> None:
        w.bind("<Enter>", self._schedule, add="+")
        w.bind("<Leave>", self._hide, add="+")
        w.bind("<ButtonPress>", self._hide, add="+")
        w.bind("<Destroy>", self._on_destroy, add="+")
        for child in w.winfo_children():
            self._bind_widget(child)

    def set_text(self, text: str) -> None:
        self._text = text
        if not text:
            self._hide()

    def _schedule(self, _e=None) -> None:
        if not self._text:
            return
        self._cancel()
        if self._widget.winfo_exists():
            self._after_id = self._widget.after(self._delay_ms, self._show)

    def _cancel(self) -> None:
        if self._after_id is not None:
            try:
                self._widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self) -> None:
        self._after_id = None
        if not self._text or not self._widget.winfo_exists() or not self._widget.winfo_viewable():
            return
        self._hide()
        popup = tk.Toplevel(self._widget.winfo_toplevel())
        popup.overrideredirect(True)
        popup.configure(bg=INK)
        if sys.platform == "darwin":
            try:
                popup.attributes("-topmost", True)
            except tk.TclError:
                pass
        tk.Label(
            popup,
            text=self._text,
            bg=INK,
            fg=WHITE,
            font=ui_font(8),
            padx=7,
            pady=3,
        ).pack()
        popup.update_idletasks()
        pw, ph = popup.winfo_reqwidth(), popup.winfo_reqheight()
        wx = self._widget.winfo_rootx()
        wy = self._widget.winfo_rooty()
        ww = self._widget.winfo_width()
        wh = self._widget.winfo_height()
        scr_w = self._widget.winfo_screenwidth()
        scr_h = self._widget.winfo_screenheight()
        x = max(8, min(scr_w - pw - 8, wx + (ww - pw) // 2))
        y_below = wy + wh + 6
        y = y_below if y_below + ph <= scr_h - 12 else max(8, wy - ph - 6)
        popup.geometry(f"{pw}x{ph}+{x}+{y}")
        popup.lift()
        self._popup = popup

    def _hide(self, _e=None) -> None:
        self._cancel()
        if self._popup is not None:
            try:
                if self._popup.winfo_exists():
                    self._popup.destroy()
            except Exception:
                pass
            self._popup = None

    def _on_destroy(self, event=None) -> None:
        if event is None or event.widget is self._widget:
            self._hide()


def attach_tooltip(widget: tk.Widget, text: str, *, delay_ms: int = 450) -> Tooltip:
    """Attach a hover tooltip to a widget and its children."""
    tip = Tooltip(widget, text, delay_ms=delay_ms)
    setattr(widget, "_tooltip", tip)
    return tip


class MinimalButton(tk.Frame):
    """Rounded monochrome button with an optional 1px border."""

    def __init__(
        self,
        parent: tk.Widget,
        text: str,
        command,
        variant: str = "secondary",
        font: Optional[Tuple[Any, ...]] = None,
        padx: int = 12,
        pady: int = 7,
        takefocus: bool = False,
        icon: Optional[str] = None,
    ) -> None:
        self._variant = variant
        self._command = command
        self._selected = False
        self._enabled = True
        self._hovered = False
        self._pressed = False
        self._icon_name = icon
        self._icon_size = 0
        self._icon_gap = 0
        self._icon_cache: Dict[str, ImageTk.PhotoImage] = {}
        self._face_cache: Dict[Tuple[int, int, str, Optional[str], bool], ImageTk.PhotoImage] = {}
        self._face_cache_size: Tuple[int, int] = (0, 0)
        font = font or ui_font(9)

        bg, fg, self._border, _, _ = self._palette()
        parent_bg = parent.cget("bg")
        super().__init__(parent, bg=parent_bg, cursor="hand2")
        self._focus_inset = 2 if (takefocus and variant != "ghost") else 0
        self._face = tk.Canvas(
            self, bg=parent_bg, highlightthickness=0, bd=0, takefocus=False,
        )
        self._face.place(relwidth=1, relheight=1)
        self._face_item = self._face.create_image(0, 0, anchor=tk.NW)
        self._face.bind("<Configure>", self._draw_face)

        self._label = tk.Label(
            self,
            text=text,
            bg=bg,
            fg=fg,
            font=font,
            padx=0,
            pady=0,
        )
        self._action_icon: Optional[ImageTk.PhotoImage] = None
        if icon is not None:
            self._icon_size = max(16, round(self.winfo_fpixels("12p")))
            self._icon_gap = padx if text else 0
            self._action_icon = self._get_icon_photo(fg)
            self._label.configure(image=self._action_icon, compound=tk.LEFT)
        # Keep the label clear of the painted corners while retaining the button's padding.
        inset = int(self._border is not None) + self._focus_inset
        self._label.pack(expand=True, padx=padx + inset, pady=pady + inset)

        for w in (self, self._face, self._label):
            w.bind("<Enter>", self._on_enter)
            w.bind("<Leave>", self._on_leave)
            w.bind("<ButtonPress-1>", self._on_press)
            w.bind("<ButtonRelease-1>", self._on_release)

        if takefocus:
            self.configure(takefocus=True)
            if variant == "ghost":
                self.bind("<FocusIn>", lambda _e: self._label.configure(font=(*font, "underline")))
                self.bind("<FocusOut>", lambda _e: self._label.configure(font=font))
            else:
                self.bind("<FocusIn>", self._draw_face)
                self.bind("<FocusOut>", self._draw_face)
            for key in ("<Return>", "<KP_Enter>", "<space>"):
                self.bind(key, self._on_key)

    def _get_icon_photo(self, fg: str) -> ImageTk.PhotoImage:
        assert self._icon_name is not None
        photo = self._icon_cache.get(fg)
        if photo is None:
            img = _render_action_icon_image(self._icon_name, self._icon_size, self._icon_gap, fg)
            photo = ImageTk.PhotoImage(img, master=self)
            self._icon_cache[fg] = photo
        return photo

    def _palette(self) -> Tuple[str, str, Optional[str], str, str]:
        if self._selected or self._variant == "primary":
            return INK, WHITE, INK, INK_HOVER, INK_PRESSED
        if self._variant == "danger":
            return "#DC2626", WHITE, "#DC2626", "#B91C1C", "#991B1B"
        if self._variant == "outline":
            return WHITE, INK, INK, HOVER_BG, PRESSED_BG
        if self._variant == "ghost":
            return WHITE, MUTED, None, HOVER_BG, PRESSED_BG
        if self._variant == "link":
            return CANVAS_BG, INK_HOVER, None, HOVER_BG, PRESSED_BG
        return WHITE, INK, BORDER_STRONG, HOVER_BG, PRESSED_BG

    def _apply_style(self, hovered: bool = False, pressed: bool = False) -> None:
        bg, fg, border, hover_bg, pressed_bg = self._palette()
        cursor = "hand2"
        if self._enabled:
            bg = pressed_bg if pressed else (hover_bg if hovered else bg)
        else:
            bg, fg = WHITE, DISABLED
            border = BORDER if border else None
            cursor = "arrow"
        if self._icon_name is not None:
            self._action_icon = self._get_icon_photo(fg)
            self._label.configure(image=self._action_icon)
        self._border = border
        self.configure(cursor=cursor)
        self._label.configure(bg=bg, fg=fg)
        self._draw_face()

    def _draw_face(self, _event=None) -> None:
        width, height = self._face.winfo_width(), self._face.winfo_height()
        if min(width, height) <= 2 * self._focus_inset:
            return
        if (width, height) != self._face_cache_size:
            self._face_cache.clear()
            self._face_cache_size = (width, height)

        bg = str(self._label.cget("bg"))
        focused = bool(self._focus_inset and self.focus_get() is self)
        key = (width, height, bg, self._border, focused)
        photo = self._face_cache.get(key)
        if photo is None:
            image = _rounded_control_image(
                (width, height), bg, self._border,
                inset=self._focus_inset,
                focused=focused,
            )
            photo = ImageTk.PhotoImage(image, master=self)
            self._face_cache[key] = photo
        self._face_image = photo
        self._face.itemconfigure(self._face_item, image=photo)

    def _contains_root_point(self, x_root: int, y_root: int) -> bool:
        x0 = self.winfo_rootx()
        y0 = self.winfo_rooty()
        return x0 <= x_root < x0 + self.winfo_width() and y0 <= y_root < y0 + self.winfo_height()

    def _on_enter(self, _e=None) -> None:
        self._hovered = True
        if self._enabled:
            self._apply_style(hovered=True, pressed=self._pressed)

    def _on_leave(self, _e=None) -> None:
        self._hovered = False
        if self._enabled:
            self._apply_style(hovered=False, pressed=False)

    def _on_press(self, _e=None) -> None:
        if self._enabled:
            self._pressed = True
            self._apply_style(hovered=self._hovered, pressed=True)

    def _on_release(self, event=None) -> None:
        was_pressed = self._pressed
        self._pressed = False
        if self._enabled:
            inside = (
                event is None or self._contains_root_point(event.x_root, event.y_root)
            )
            self._apply_style(hovered=inside, pressed=False)
            if was_pressed and self._command and inside:
                self._command()

    def _on_key(self, _event) -> str:
        if self._enabled and self._command:
            self._command()
        return "break"

    def set_text(self, text: str) -> None:
        self._label.configure(text=text)

    def set_variant(self, variant: str) -> None:
        if self._variant != variant:
            self._variant = variant
            self._apply_style(hovered=self._hovered, pressed=False)

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._apply_style(hovered=self._hovered, pressed=False)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        if not enabled:
            self._pressed = False
        self._apply_style(hovered=self._hovered if enabled else False, pressed=False)


class IllustratedOption(MinimalButton):
    """A monochrome choice with a paper silhouette and a selection checkmark."""

    def __init__(
        self,
        parent: tk.Widget,
        text: str,
        command,
        paper_size: Tuple[int, int],
        margin_inset: Optional[int] = None,
    ) -> None:
        padding = 3
        super().__init__(
            parent, text, command, font=ui_font(8, semibold=True), padx=padding, pady=padding, takefocus=True,
        )
        self._icons: Dict[bool, ImageTk.PhotoImage] = {}
        icon_width = 48
        icon_height = 30 if margin_inset is None else 18
        paper_w, paper_h = paper_size
        left = (icon_width - paper_w) // 2
        top = icon_height - paper_h - 1
        right, bottom = left + paper_w, top + paper_h
        for selected, color in ((False, INK), (True, WHITE)):
            image = Image.new("RGBA", (icon_width, icon_height + padding))
            draw = ImageDraw.Draw(image)
            draw.rectangle((left, top, right, bottom), outline=color)
            if margin_inset is not None:
                draw.rectangle(
                    (left + margin_inset, top + margin_inset, right - margin_inset, bottom - margin_inset),
                    fill=DISABLED if selected else MUTED,
                )
            if selected:
                draw.line(((38, 4), (41, 7), (46, 2)), fill=color, width=2)
            self._icons[selected] = ImageTk.PhotoImage(image, master=self)
        self._label.configure(image=self._icons[False], compound=tk.TOP, justify=tk.CENTER)

    def _apply_style(self, hovered: bool = False, pressed: bool = False) -> None:
        super()._apply_style(hovered, pressed)
        self._label.configure(image=self._icons[self._selected])


class PageStrip(tk.Frame):
    """Scrollable source-page thumbnails with a shared output scope."""

    _TILE_WIDTH = 57
    _TILE_HEIGHT = 77

    def __init__(
        self,
        parent: tk.Widget,
        select_page: Callable[[int], None],
        apply_all: tk.BooleanVar,
        on_scope_change: Optional[Callable[[bool], None]] = None,
    ) -> None:
        super().__init__(parent, bg=SUBTLE_BG)
        self._doc: Optional[fitz.Document] = None
        self._page_index = 0
        self._hover_index: Optional[int] = None
        self._photos: Dict[int, Optional[ImageTk.PhotoImage]] = {}
        self._draw_id: Optional[str] = None
        self._select_page = select_page
        self._apply_all = apply_all
        self._on_scope_change = on_scope_change

        tk.Frame(self, bg=BORDER, height=1).pack(side=tk.TOP, fill=tk.X)
        scope = tk.Frame(self, bg=SUBTLE_BG)
        scope.pack(side=tk.RIGHT, padx=12)
        self._scope_count = tk.Label(scope, bg=SUBTLE_BG, fg=MUTED, font=ui_font(8))
        self._scope_count.pack(anchor="w", pady=(0, 3))
        self._this_page = MinimalButton(
            scope, "This page", lambda: self._set_scope(False), padx=8, pady=4, takefocus=True,
        )
        self._this_page.pack(side=tk.LEFT, padx=(0, 4))
        self._all_pages = MinimalButton(
            scope, "All pages", lambda: self._set_scope(True), padx=8, pady=4, takefocus=True,
        )
        self._all_pages.pack(side=tk.LEFT)
        tk.Frame(self, bg=BORDER, width=1).pack(side=tk.RIGHT, fill=tk.Y, pady=8)

        thumbnails = tk.Frame(self, bg=SUBTLE_BG)
        thumbnails.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 6), pady=4)
        self._canvas = tk.Canvas(
            thumbnails, width=1, height=self._TILE_HEIGHT, bg=SUBTLE_BG, bd=0,
            highlightthickness=1, highlightbackground=SUBTLE_BG, highlightcolor=INK,
            xscrollincrement=1, takefocus=True,
        )
        self._canvas.pack(fill=tk.X, expand=True)
        # Keep a fixed-height slot so showing/hiding the scrollbar never causes vertical layout shift.
        self._scroll_slot = tk.Frame(thumbnails, bg=SUBTLE_BG, height=14)
        self._scroll_slot.pack_propagate(False)
        self._scroll_slot.pack(fill=tk.X, pady=(2, 0))
        self._scrollbar = ttk.Scrollbar(self._scroll_slot, orient=tk.HORIZONTAL, command=self._canvas.xview)
        self._canvas.configure(xscrollcommand=self._on_scroll)
        self._canvas.bind("<Configure>", self._on_resize)
        self._canvas.bind("<FocusIn>", lambda _e: self._reveal_current())
        self._canvas.bind("<Motion>", self._on_motion)
        self._canvas.bind("<Leave>", self._on_leave)
        self._canvas.bind("<Button-1>", self._on_click)
        for sequence in ("<MouseWheel>", "<Shift-MouseWheel>", "<Button-4>", "<Button-5>"):
            self._canvas.bind(sequence, self._on_wheel)
        self._canvas.bind("<Left>", lambda _e: self._jump(self._page_index - 1))
        self._canvas.bind("<Right>", lambda _e: self._jump(self._page_index + 1))
        self._canvas.bind("<Home>", lambda _e: self._jump(0))
        self._canvas.bind("<End>", lambda _e: self._jump(self._page_count - 1))

    @property
    def _page_count(self) -> int:
        return len(self._doc) if self._doc is not None and not self._doc.is_closed else 0

    def show_page(self, doc: fitz.Document, page_index: int) -> None:
        if self._doc is not doc:
            self._doc = doc
            self._canvas.delete("all")
            self._photos.clear()
            self._hover_index = None
            self._canvas.configure(
                scrollregion=(0, 0, self._page_count * self._TILE_WIDTH, self._TILE_HEIGHT),
            )
            self._canvas.xview_moveto(0)
        self._page_index = page_index
        self._reveal_current()
        self._sync_scope()
        self._schedule_draw()

    def _set_scope(self, apply_all: bool) -> None:
        self._apply_all.set(apply_all)
        self._sync_scope()
        if self._on_scope_change is not None:
            self._on_scope_change(apply_all)

    def _sync_scope(self) -> None:
        apply_all = self._apply_all.get()
        total = self._page_count
        labels = f"{total} labels" if apply_all else "1 label"
        self._this_page.set_selected(not apply_all)
        self._all_pages.set_selected(apply_all)
        self._scope_count.configure(text=f"Page {self._page_index + 1} / {total}  ·  {labels}")

    def _on_scroll(self, first: str, last: str) -> None:
        self._scrollbar.set(first, last)
        if float(first) > 0 or float(last) < 1:
            if not self._scrollbar.winfo_manager():
                self._scrollbar.pack(fill=tk.BOTH, expand=True)
        else:
            if self._scrollbar.winfo_manager():
                self._scrollbar.pack_forget()
        self._schedule_draw()

    def _on_resize(self, _event) -> None:
        self._reveal_current()
        self._schedule_draw()

    def _reveal_current(self) -> None:
        content_width = self._page_count * self._TILE_WIDTH
        canvas = self._canvas
        viewport = canvas.winfo_width() - 2
        if not content_width or viewport < self._TILE_WIDTH:
            return
        start = canvas.canvasx(1)
        tile_start = self._page_index * self._TILE_WIDTH
        tile_end = tile_start + self._TILE_WIDTH
        if tile_start < start:
            canvas.xview_moveto(tile_start / content_width)
        elif tile_end > start + viewport:
            canvas.xview_moveto((tile_end - viewport) / content_width)

    def _on_click(self, event) -> None:
        index = self._index_at(event.x)
        if 0 <= index < self._page_count:
            self._select_page(index)
            self._canvas.focus_set()

    def _on_motion(self, event) -> None:
        idx = self._index_at(event.x)
        hovered = idx if 0 <= idx < self._page_count else None
        cursor = "hand2" if hovered is not None else "arrow"
        if self._canvas.cget("cursor") != cursor:
            self._canvas.configure(cursor=cursor)
        if hovered != self._hover_index:
            self._hover_index = hovered
            self._schedule_draw()

    def _on_leave(self, _event=None) -> None:
        if self._hover_index is not None:
            self._hover_index = None
            self._schedule_draw()

    def _index_at(self, x: int) -> int:
        return int(self._canvas.canvasx(x) // self._TILE_WIDTH)

    def _on_wheel(self, event) -> str:
        steps = _wheel_steps(event)
        self._canvas.xview_scroll(round(-steps * self._TILE_WIDTH), "units")
        return "break"

    def _jump(self, index: int) -> str:
        self._select_page(index)
        return "break"

    def _schedule_draw(self) -> None:
        if self._draw_id is None:
            self._draw_id = self.after_idle(self._draw_visible)

    def _draw_visible(self) -> None:
        self._draw_id = None
        canvas = self._canvas
        if self._page_count < 2 or canvas.winfo_width() <= 2:
            return
        doc = self._doc
        assert doc is not None
        first = max(0, int(canvas.canvasx(1)) // self._TILE_WIDTH)
        right = canvas.canvasx(canvas.winfo_width() - 1)
        stop = min(self._page_count, math.ceil(right / self._TILE_WIDTH))
        canvas.delete("all")
        photos = {}
        for index in range(first, stop):
            if index not in self._photos:
                try:
                    image, _ = render_page_image(doc, index, 37, 46, min_scale=0.0)
                    photos[index] = ImageTk.PhotoImage(image, master=canvas)
                except (ValueError, RuntimeError):
                    photos[index] = None
            else:
                photos[index] = self._photos[index]
            photo = photos[index]
            left = index * self._TILE_WIDTH
            center = left + self._TILE_WIDTH / 2
            selected = index == self._page_index
            hovered = (index == self._hover_index) and not selected
            tile_fill = WHITE if selected else (HOVER_BG if hovered else SUBTLE_BG)
            tile_outline = INK if selected else (BORDER_STRONG if hovered else "")
            canvas.create_rectangle(
                left + 2, 3, left + self._TILE_WIDTH - 2, self._TILE_HEIGHT - 3,
                fill=tile_fill, outline=tile_outline,
            )
            width, height = (photo.width(), photo.height()) if photo else (32, 46)
            canvas.create_rectangle(
                center - width / 2 - 1, 30 - height / 2 - 1,
                center + width / 2 + 1, 30 + height / 2 + 1, fill=WHITE, outline=BORDER_STRONG,
            )
            if photo:
                canvas.create_image(center, 30, image=photo)
            if selected:
                canvas.create_rectangle(center - 23, 55, center + 23, self._TILE_HEIGHT - 6, fill=INK, outline="")
            canvas.create_text(
                center, 63, text=str(index + 1), font=ui_font(8, semibold=selected),
                fill=WHITE if selected else (INK if hovered else MUTED),
            )
        self._photos = photos

    def destroy(self) -> None:
        if self._draw_id is not None:
            self.after_cancel(self._draw_id)
        super().destroy()


class ElidedLabel(tk.Label):
    """Keep the start of a single line visible when its column shrinks."""

    def __init__(self, parent: tk.Widget, text: str = "", **kwargs: Any) -> None:
        super().__init__(parent, width=1, **kwargs)
        self._text = text
        self.bind("<Configure>", self._fit_text)

    def set_text(self, text: str) -> None:
        self._text = text
        self._fit_text()

    def _fit_text(self, _event=None) -> None:
        width = self.winfo_width() - 2 * self.winfo_pixels(self.cget("padx"))
        font = self.cget("font")

        def measure(text: str) -> int:
            return int(self.tk.call("font", "measure", font, "-displayof", self._w, text))

        text = self._text
        if measure(text) > width:
            fits = bisect_right(range(len(text)), width, key=lambda i: measure(text[:i] + "…"))
            text = text[: fits - 1] + "…" if fits else ""
        if text != self.cget("text"):
            self.configure(text=text)


class MinimalDropdown(tk.Frame):
    """Crisp flat monochrome dropdown select with custom scrollable floating menu."""

    _MAX_VISIBLE_ROWS = 8

    def __init__(
        self,
        parent: tk.Widget,
        variable: tk.StringVar,
        values: List[str],
        command=None,
        font: Optional[Tuple[Any, ...]] = None,
    ) -> None:
        super().__init__(parent, bg=BORDER_STRONG, padx=1, pady=1, cursor="hand2", takefocus=True)
        self._var = variable
        self._values = list(values)
        self._command = command
        self._font = font or ui_font(8)
        self._hovered = False
        self._focused = False
        self._popup: Optional[tk.Toplevel] = None
        self._prev_grab: Optional[tk.Misc] = None
        self._popup_rows: List[Tuple[tk.Frame, tk.Label, tk.Label, str]] = []
        self._popup_index: int = 0
        self._popup_canvas: Optional[tk.Canvas] = None

        self._inner = tk.Frame(self, bg=WHITE, cursor="hand2")
        self._inner.pack(fill=tk.BOTH, expand=True)

        self._label = ElidedLabel(
            self._inner,
            text=self._var.get(),
            bg=WHITE,
            fg=INK,
            font=self._font,
            anchor="w",
            padx=9,
            pady=5,
            cursor="hand2",
        )
        self._label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self._arrow = tk.Canvas(
            self._inner, width=12, height=12, bg=WHITE, highlightthickness=0, bd=0, cursor="hand2"
        )
        self._arrow.pack(side=tk.RIGHT, padx=(0, 8))
        self._arrow_id = self._arrow.create_line(
            2.5, 4.5, 6.0, 8.0, 9.5, 4.5, fill=MUTED, width=1.4, capstyle=tk.ROUND, joinstyle=tk.ROUND
        )

        for w in (self, self._inner, self._label, self._arrow):
            w.bind("<Enter>", self._on_enter)
            w.bind("<Leave>", self._on_leave)
            w.bind("<ButtonRelease-1>", self._toggle_popup)

        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        for key in ("<Return>", "<KP_Enter>", "<space>"):
            self.bind(key, self._on_key_toggle)
        self.bind("<Up>", lambda _e: self._step(-1))
        self.bind("<Down>", lambda _e: self._step(1))

        self._trace_id: Optional[str] = self._var.trace_add("write", lambda *_: self._sync_label())
        self.bind("<Destroy>", self._on_destroy)

    def _on_destroy(self, event=None) -> None:
        if event is not None and event.widget is not self:
            return
        self._close_popup()
        if self._trace_id is not None:
            try:
                self._var.trace_remove("write", self._trace_id)
            except Exception:
                pass
            self._trace_id = None

    def _sync_label(self) -> None:
        if self.winfo_exists():
            self._label.set_text(self._var.get())

    def _apply_style(self) -> None:
        if not self.winfo_exists():
            return
        is_open = self._popup is not None and self._popup.winfo_exists()
        bg = HOVER_BG if (self._hovered and not is_open) else WHITE
        border = INK if (is_open or self._focused) else (BORDER_STRONG if not self._hovered else MUTED)
        self.configure(bg=border)
        self._inner.configure(bg=bg)
        self._label.configure(bg=bg)
        self._arrow.configure(bg=bg)
        if is_open:
            self._arrow.coords(self._arrow_id, 2.5, 8.0, 6.0, 4.5, 9.5, 8.0)
            self._arrow.itemconfigure(self._arrow_id, fill=INK)
        else:
            self._arrow.coords(self._arrow_id, 2.5, 4.5, 6.0, 8.0, 9.5, 4.5)
            self._arrow.itemconfigure(self._arrow_id, fill=MUTED)

    def _on_enter(self, _e=None) -> None:
        self._hovered = True
        self._apply_style()

    def _on_leave(self, _e=None) -> None:
        self._hovered = False
        self._apply_style()

    def _on_focus_in(self, _e=None) -> None:
        self._focused = True
        self._apply_style()

    def _on_focus_out(self, _e=None) -> None:
        self._focused = False
        self._apply_style()

    def _on_key_toggle(self, _e=None) -> str:
        self._toggle_popup()
        return "break"

    def _step(self, delta: int) -> str:
        if not self._values:
            return "break"
        current = self._var.get()
        idx = self._values.index(current) if current in self._values else 0
        next_idx = max(0, min(len(self._values) - 1, idx + delta))
        self._select(self._values[next_idx])
        return "break"

    def _highlight_popup_index(self, index: int) -> None:
        if not self._popup_rows:
            return
        self._popup_index = max(0, min(len(self._popup_rows) - 1, index))
        for i, (row, lbl, chk, base_bg) in enumerate(self._popup_rows):
            bg = HOVER_BG if i == self._popup_index else base_bg
            row.configure(bg=bg)
            lbl.configure(bg=bg)
            chk.configure(bg=bg)
        if self._popup_canvas is not None and self._popup_canvas.winfo_exists():
            row_w = self._popup_rows[self._popup_index][0]
            y0 = row_w.winfo_y()
            y1 = y0 + row_w.winfo_height()
            total_h = max(1, row_w.master.winfo_reqheight())
            view_top = self._popup_canvas.canvasy(0)
            view_h = self._popup_canvas.winfo_height()
            if y0 < view_top:
                self._popup_canvas.yview_moveto(y0 / total_h)
            elif y1 > view_top + view_h:
                self._popup_canvas.yview_moveto(max(0.0, (y1 - view_h) / total_h))

    def _popup_move(self, delta: int) -> str:
        self._highlight_popup_index(self._popup_index + delta)
        return "break"

    def _popup_commit(self, _e=None) -> str:
        if 0 <= self._popup_index < len(self._values):
            self._select(self._values[self._popup_index])
        else:
            self._close_popup()
        return "break"

    def _toggle_popup(self, _e=None) -> None:
        if self._popup is not None and self._popup.winfo_exists():
            self._close_popup()
            return
        self._open_popup()

    def _open_popup(self) -> None:
        if not self._values:
            return
        self._prev_grab = self.grab_current()
        popup = tk.Toplevel(self.winfo_toplevel())
        self._popup = popup
        self._popup_rows = []
        self._popup_canvas = None
        popup.overrideredirect(True)
        popup.configure(bg=INK, padx=1, pady=1)

        outer = tk.Frame(popup, bg=WHITE)
        outer.pack(fill=tk.BOTH, expand=True)

        scrollable = len(self._values) > self._MAX_VISIBLE_ROWS
        if scrollable:
            canvas = tk.Canvas(outer, bg=WHITE, highlightthickness=0, bd=0)
            scrollbar = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=canvas.yview)
            canvas.configure(yscrollcommand=scrollbar.set)
            scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
            canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            menu = tk.Frame(canvas, bg=WHITE, pady=3)
            menu_win = canvas.create_window((0, 0), window=menu, anchor="nw")
            self._popup_canvas = canvas
        else:
            menu = tk.Frame(outer, bg=WHITE, pady=3)
            menu.pack(fill=tk.BOTH, expand=True)

        current = self._var.get()
        self._popup_index = self._values.index(current) if current in self._values else 0
        raw_size = int(self._font[1]) if isinstance(self._font, tuple) and len(self._font) > 1 else 8
        base_size = (raw_size - 2) if sys.platform == "darwin" else raw_size
        bold_font = ui_font(base_size, semibold=True)

        for idx, val in enumerate(self._values):
            is_sel = val == current
            base_bg = SUBTLE_BG if is_sel else WHITE
            row = tk.Frame(menu, bg=base_bg, cursor="hand2")
            row.pack(fill=tk.X)

            lbl = tk.Label(
                row,
                text=val,
                bg=base_bg,
                fg=INK,
                font=bold_font if is_sel else self._font,
                anchor="w",
                padx=9,
                pady=6,
                cursor="hand2",
            )
            lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)

            chk = tk.Label(
                row,
                text="✓" if is_sel else "",
                bg=base_bg,
                fg=INK,
                font=bold_font,
                padx=8,
                pady=6,
                cursor="hand2",
            )
            chk.pack(side=tk.RIGHT)
            self._popup_rows.append((row, lbl, chk, base_bg))

            def _enter(_e, i=idx) -> None:
                self._highlight_popup_index(i)

            def _pick(_e, v=val) -> str:
                self._select(v)
                return "break"

            for w in (row, lbl, chk):
                w.bind("<Enter>", _enter)
                w.bind("<ButtonRelease-1>", _pick)

        self.update_idletasks()
        menu.update_idletasks()
        scr_w = self.winfo_screenwidth()
        scr_h = self.winfo_screenheight()
        max_w = max(self.winfo_width(), min(scr_w - 40, 480))
        req_w = menu.winfo_reqwidth() + (18 if scrollable else 0)
        w = max(self.winfo_width(), min(max_w, req_w))

        if scrollable and self._popup_canvas is not None:
            row_h = max(24, self._popup_rows[0][0].winfo_reqheight())
            h = min(scr_h - 80, row_h * self._MAX_VISIBLE_ROWS + 8)
            self._popup_canvas.configure(width=w - 18, height=h)
            self._popup_canvas.itemconfigure(menu_win, width=w - 18)
            self._popup_canvas.configure(scrollregion=(0, 0, w - 18, menu.winfo_reqheight()))

            def _on_wheel(event) -> str:
                if self._popup_canvas is not None:
                    self._popup_canvas.yview_scroll(round(-_wheel_steps(event) * 3), "units")
                return "break"

            for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                popup.bind(seq, _on_wheel)
        else:
            popup.update_idletasks()
            h = popup.winfo_reqheight()

        x = max(8, min(scr_w - w - 8, self.winfo_rootx()))
        y_below = self.winfo_rooty() + self.winfo_height() + 2
        y = y_below if (y_below + h <= scr_h - 40) else max(10, self.winfo_rooty() - h - 2)
        if sys.platform == "darwin":
            try:
                popup.attributes("-topmost", True)
            except tk.TclError:
                pass
        popup.geometry(f"{w}x{h}+{x}+{y}")
        popup.lift()
        popup.grab_set()
        popup.focus_set()
        self._highlight_popup_index(self._popup_index)

        def _on_press(event) -> Optional[str]:
            if not popup.winfo_exists():
                return None
            px0 = popup.winfo_rootx()
            py0 = popup.winfo_rooty()
            px1 = px0 + popup.winfo_width()
            py1 = py0 + popup.winfo_height()
            if not (px0 <= event.x_root <= px1 and py0 <= event.y_root <= py1):
                self._close_popup()
                return "break"
            return None

        popup.bind("<ButtonPress-1>", _on_press)
        popup.bind("<Escape>", lambda _e: self._close_popup())
        popup.bind("<Up>", lambda _e: self._popup_move(-1))
        popup.bind("<Down>", lambda _e: self._popup_move(1))
        popup.bind("<Home>", lambda _e: (self._highlight_popup_index(0), "break")[1])
        popup.bind("<End>", lambda _e: (self._highlight_popup_index(len(self._values) - 1), "break")[1])
        for key in ("<Return>", "<KP_Enter>", "<space>"):
            popup.bind(key, self._popup_commit)
        self._apply_style()

    def _select(self, value: str) -> None:
        self._close_popup()
        if self._var.get() != value:
            self._var.set(value)
            if self._command:
                self._command(value)

    def _close_popup(self) -> str:
        popup = self._popup
        self._popup = None
        self._popup_rows = []
        self._popup_canvas = None
        if popup is not None and popup.winfo_exists():
            try:
                popup.grab_release()
            except Exception:
                pass
            popup.destroy()
        prev = self._prev_grab
        self._prev_grab = None
        if prev is not None and prev.winfo_exists():
            try:
                prev.grab_set()
            except Exception:
                pass
        if self.winfo_exists():
            try:
                self.focus_set()
            except tk.TclError:
                pass
        self._apply_style()
        return "break"


class MinimalSlider(tk.Canvas):
    """Crisp flat monochrome integer slider matching MinimalButton and MinimalDropdown."""

    def __init__(
        self,
        parent: tk.Widget,
        variable: tk.IntVar,
        min_val: int,
        max_val: int,
        command=None,
        bg: str = WHITE,
    ) -> None:
        super().__init__(
            parent, width=200, height=22, bg=bg, highlightthickness=0, bd=0, cursor="hand2", takefocus=True
        )
        self._var = variable
        self._min = min_val
        self._max = max(min_val + 1, max_val)
        self._command = command
        self._hovered = False
        self._focused = False
        self._pad = 8

        self.bind("<Configure>", lambda _e: self._redraw())
        self.bind("<ButtonPress-1>", self._on_pointer)
        self.bind("<B1-Motion>", self._on_pointer)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        self.bind("<Left>", lambda _e: self._nudge(-1))
        self.bind("<Down>", lambda _e: self._nudge(-1))
        self.bind("<Right>", lambda _e: self._nudge(1))
        self.bind("<Up>", lambda _e: self._nudge(1))
        self.bind("<Home>", lambda _e: self._set_value(self._min))
        self.bind("<End>", lambda _e: self._set_value(self._max))
        self._trace_id: Optional[str] = self._var.trace_add("write", lambda *_: self._redraw())
        self.bind("<Destroy>", self._on_destroy)

    def _on_destroy(self, event=None) -> None:
        if event is not None and event.widget is not self:
            return
        if self._trace_id is not None:
            try:
                self._var.trace_remove("write", self._trace_id)
            except Exception:
                pass
            self._trace_id = None

    def _on_enter(self, _e=None) -> None:
        self._hovered = True
        self._redraw()

    def _on_leave(self, _e=None) -> None:
        self._hovered = False
        self._redraw()

    def _on_focus_in(self, _e=None) -> None:
        self._focused = True
        self._redraw()

    def _on_focus_out(self, _e=None) -> None:
        self._focused = False
        self._redraw()

    def _set_value(self, val: int) -> str:
        clamped = max(self._min, min(self._max, int(val)))
        if self._var.get() != clamped:
            self._var.set(clamped)
            if self._command:
                self._command(clamped)
        return "break"

    def _nudge(self, delta: int) -> str:
        return self._set_value(self._var.get() + delta)

    def _on_pointer(self, event) -> None:
        self.focus_set()
        w = max(1, self.winfo_width())
        span = max(1, w - 2 * self._pad)
        frac = max(0.0, min(1.0, (event.x - self._pad) / span))
        val = int(round(self._min + frac * (self._max - self._min)))
        self._set_value(val)

    def _redraw(self) -> None:
        if not self.winfo_exists():
            return
        self.delete("all")
        w = max(40, self.winfo_width())
        h = max(20, self.winfo_height())
        cy = h / 2.0
        x0 = float(self._pad)
        x1 = float(w - self._pad)
        val = max(self._min, min(self._max, int(self._var.get())))
        frac = (val - self._min) / max(1, self._max - self._min)
        tx = x0 + frac * (x1 - x0)

        # Base track
        self.create_line(x0, cy, x1, cy, fill=BORDER, width=3, capstyle=tk.ROUND)
        # Active track
        if tx > x0:
            self.create_line(x0, cy, tx, cy, fill=INK, width=3, capstyle=tk.ROUND)
        # Focus halo
        if self._focused:
            self.create_oval(tx - 9, cy - 9, tx + 9, cy + 9, outline=INK, width=1)
        # Thumb
        r = 6.5 if (self._hovered or self._focused) else 6.0
        self.create_oval(
            tx - r, cy - r, tx + r, cy + r, fill=WHITE, outline=INK, width=2
        )


class ContextMenuBadge(tk.Frame):
    """Header status badge with animated spinner for toggling OS file-manager integration."""

    def __init__(
        self,
        parent: tk.Widget,
        menu_strings: Dict[str, str],
        on_click: Callable[[], None],
    ) -> None:
        super().__init__(
            parent, bg=WHITE, padx=1, pady=1, cursor="hand2", takefocus=True,
            highlightthickness=1, highlightbackground=WHITE, highlightcolor=INK,
        )
        self._menu_strings = menu_strings
        self._on_click = on_click
        self._active = False
        self._hovered = False
        self._focused = False
        self._busy = False
        self._pressed = False
        self._busy_text = ""
        self._spinner_angle = 0
        self._spinner_after_id: Optional[str] = None

        self._inner = tk.Frame(self, bg=WHITE, padx=6, pady=4, cursor="hand2")
        self._inner.pack(fill=tk.BOTH, expand=True)
        self._dot = tk.Canvas(
            self._inner, width=10, height=10, bg=WHITE, highlightthickness=0, bd=0, cursor="hand2"
        )
        self._dot.pack(side=tk.LEFT, padx=(0, 6), pady=(1, 0))
        self._dot_id = self._dot.create_oval(2, 2, 8, 8, fill=MUTED, outline=MUTED)
        self._spinner_id = self._dot.create_arc(
            1, 1, 9, 9, start=0, extent=250, style=tk.ARC, outline=INK, width=1.6, state="hidden"
        )
        self._text = tk.Label(
            self._inner, text="", bg=WHITE, fg=MUTED, font=ui_font(8, semibold=True), cursor="hand2"
        )
        self._text.pack(side=tk.LEFT)

        for w in (self, self._inner, self._dot, self._text):
            w.bind("<Enter>", self._on_enter)
            w.bind("<Leave>", self._on_leave)
            w.bind("<ButtonPress-1>", self._on_press)
            w.bind("<ButtonRelease-1>", self._on_release)

        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        for key in ("<Return>", "<KP_Enter>", "<space>"):
            self.bind(key, self._on_key)

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def is_busy(self) -> bool:
        return self._busy

    def set_active(self, active: bool) -> None:
        self._active = active
        self._apply_style()

    def start_spinner(self, text: str) -> None:
        self._busy = True
        self._busy_text = text
        self._spinner_angle = 0
        self._apply_style()
        self._tick_spinner()

    def stop_spinner(self) -> None:
        self._busy = False
        if self._spinner_after_id is not None:
            try:
                self.after_cancel(self._spinner_after_id)
            except Exception:
                pass
            self._spinner_after_id = None

    def _tick_spinner(self) -> None:
        if not self._busy or not self.winfo_exists():
            self._spinner_after_id = None
            return
        self._spinner_angle = (self._spinner_angle - 30) % 360
        self._dot.itemconfigure(self._spinner_id, start=self._spinner_angle)
        self._spinner_after_id = self.after(45, self._tick_spinner)

    def _apply_style(self) -> None:
        if not self.winfo_exists():
            return
        bg = WHITE
        if self._busy:
            txt_fg = MUTED
            label_txt = self._busy_text
            cursor = "watch"
            self._dot.itemconfigure(self._dot_id, state="hidden")
            self._dot.itemconfigure(self._spinner_id, state="normal")
        elif self._active:
            txt_fg = INK_HOVER if self._hovered else INK
            bg = PRESSED_BG if self._pressed else (HOVER_BG if self._hovered else WHITE)
            label_txt = self._menu_strings["active"]
            cursor = "hand2"
            self._dot.itemconfigure(self._spinner_id, state="hidden")
            self._dot.itemconfigure(self._dot_id, state="normal", fill="#16A34A", outline="#16A34A")
        else:
            txt_fg = INK if self._hovered else MUTED
            bg = PRESSED_BG if self._pressed else (HOVER_BG if self._hovered else WHITE)
            label_txt = self._menu_strings["inactive"]
            cursor = "hand2"
            self._dot.itemconfigure(self._spinner_id, state="hidden")
            self._dot.itemconfigure(self._dot_id, state="normal", fill="", outline=MUTED)

        self.configure(
            bg=WHITE,
            cursor=cursor,
            highlightbackground=INK if self._focused else WHITE,
        )
        self._inner.configure(bg=bg, cursor=cursor)
        self._dot.configure(bg=bg, cursor=cursor)
        self._text.configure(text=label_txt, bg=bg, fg=txt_fg, cursor=cursor)

    def _on_enter(self, _e=None) -> None:
        self._hovered = True
        self._apply_style()

    def _on_leave(self, _e=None) -> None:
        self._hovered = False
        self._pressed = False
        self._apply_style()

    def _on_focus_in(self, _e=None) -> None:
        self._focused = True
        self._apply_style()

    def _on_focus_out(self, _e=None) -> None:
        self._focused = False
        self._apply_style()

    def _on_key(self, _e=None) -> str:
        if not self._busy:
            self._on_click()
        return "break"

    def _on_press(self, _e=None) -> None:
        if not self._busy:
            self._pressed = True
            self._apply_style()

    def _on_release(self, event=None) -> None:
        was_pressed = self._pressed
        self._pressed = False
        self._apply_style()
        if not was_pressed or self._busy:
            return
        if event is not None:
            x0 = self.winfo_rootx()
            y0 = self.winfo_rooty()
            if not (x0 <= event.x_root < x0 + self.winfo_width()
                    and y0 <= event.y_root < y0 + self.winfo_height()):
                return
        self._on_click()

    def destroy(self) -> None:
        self.stop_spinner()
        super().destroy()
