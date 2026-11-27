"""Zoomable and pannable expanded label preview view."""

from __future__ import annotations

import tkinter as tk
from typing import Any, Callable, Optional, Tuple

import pymupdf as fitz
from PIL import Image, ImageTk

from .pdf_engine import CropRect, render_label_preview
from .widgets import (
    BORDER,
    BORDER_STRONG,
    CANVAS_BG,
    INK,
    MUTED,
    WHITE,
    ElidedLabel,
    MinimalButton,
    _wheel_steps,
    attach_tooltip,
    ui_font,
)

PreviewState = Tuple[fitz.Document, int, CropRect, int, float, float, float]


class ExpandedPreviewFrame(tk.Frame):
    """Full-workspace high-resolution label preview with zoom and pan controls."""

    _PAD = 32

    def __init__(
        self,
        parent: tk.Widget,
        *,
        header_height: int,
        get_state: Callable[[], Optional[PreviewState]],
        on_rotate: Callable[[int], None],
        on_close: Callable[[], None],
        on_canvas_down: Callable[[Any], None],
    ) -> None:
        super().__init__(parent, bg=WHITE)
        self._get_state = get_state
        self._on_canvas_down_cb = on_canvas_down

        self._zoom = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._pan_limit_x = 0.0
        self._pan_limit_y = 0.0
        self._label_box = (0, 0, 0, 0)
        self._drag_start: Optional[Tuple[float, float]] = None
        self._source: Optional[Image.Image] = None
        self._source_key: Optional[PreviewState] = None
        self._photo: Optional[ImageTk.PhotoImage] = None
        self._draw_after: Optional[str] = None

        header = tk.Frame(self, bg=WHITE, padx=16, height=header_height)
        header.pack_propagate(False)
        header.pack(fill=tk.X)
        controls = tk.Frame(header, bg=WHITE)
        controls.pack(side=tk.RIGHT)
        ElidedLabel(
            header, text="Label preview", bg=WHITE, fg=INK, font=ui_font(9, semibold=True), anchor="w",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        tk.Frame(self, bg=BORDER, height=1).pack(fill=tk.X)

        self._preview = tk.Canvas(
            self, bg=CANVAS_BG, width=1, height=1, bd=0, takefocus=True,
            highlightthickness=1, highlightbackground=CANVAS_BG, highlightcolor=INK,
        )
        footer = tk.Frame(self, bg=WHITE, padx=12, pady=7)
        footer.pack(side=tk.BOTTOM, fill=tk.X)
        self._info = tk.Label(footer, bg=WHITE, fg=MUTED, font=ui_font(8))
        self._info.pack(side=tk.LEFT)
        ElidedLabel(
            footer, text="Scroll to zoom · Drag to pan · Esc to return", bg=WHITE, fg=MUTED, font=ui_font(8), anchor="e",
        ).pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(8, 0))
        tk.Frame(self, bg=BORDER, height=1).pack(side=tk.BOTTOM, fill=tk.X)
        self._preview.pack(fill=tk.BOTH, expand=True)

        def _tool(
            text: str,
            command: Callable[[], Any],
            *,
            icon: Optional[str] = None,
            gap: int = 3,
            tip: str = "",
        ) -> MinimalButton:
            button = MinimalButton(controls, text, command, padx=6, pady=2, takefocus=True, icon=icon)
            button.pack(side=tk.LEFT, padx=(0, gap))
            if tip:
                attach_tooltip(button, tip)
            return button

        self._zoom_out_btn = _tool("−", self.zoom_out, tip="Zoom out (−)")
        self._zoom_button = _tool("100%", self.reset_zoom, tip="Reset zoom (0)")
        self._zoom_in_btn = _tool("+", self.zoom_in, gap=0, tip="Zoom in (+)")
        tk.Frame(controls, bg=BORDER, width=1, height=18).pack(side=tk.LEFT, padx=6)
        rotate_left = _tool("", lambda: on_rotate(-90), icon="rotate-left", tip="Rotate left (Shift+R)")
        rotate_right = _tool("", lambda: on_rotate(90), icon="rotate-right", gap=0, tip="Rotate right (R)")
        tk.Frame(controls, bg=BORDER, width=1, height=18).pack(side=tk.LEFT, padx=6)
        back = _tool("← Back to crop", on_close, gap=0, tip="Return to crop (Esc)")

        self._preview.bind("<ButtonPress-1>", self._on_canvas_down)
        self._preview.bind("<B1-Motion>", self._on_canvas_drag)
        self._preview.bind("<ButtonRelease-1>", self._on_canvas_release)
        self._preview.bind("<Double-Button-1>", self._reset_zoom)
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self._preview.bind(sequence, self._on_wheel)
        for widget in (
            self,
            self._preview,
            self._zoom_out_btn,
            self._zoom_button,
            self._zoom_in_btn,
            rotate_left,
            rotate_right,
            back,
        ):
            widget.bind("0", self._reset_zoom)
            for key in ("+", "="):
                widget.bind(key, lambda _e: self.zoom_in())
            widget.bind("-", lambda _e: self.zoom_out())
            for key, (dx, dy) in {"Left": (32, 0), "Right": (-32, 0), "Up": (0, 32), "Down": (0, -32)}.items():
                widget.bind(f"<{key}>", lambda _e, x=dx, y=dy: self._pan(x, y))
        self.bind("<Destroy>", self._on_destroy)

    def bind_configure(self) -> None:
        self._preview.bind(
            "<Configure>",
            lambda _e: self._draw() if self._photo is None else self._schedule_draw(50),
        )

    def focus_initial(self, *, focus_canvas: bool) -> None:
        (self._preview if focus_canvas else self).focus_set()

    def refresh_source(self) -> None:
        state = self._get_state()
        if state is None:
            return
        if state == self._source_key:
            return
        doc, page_index, crop, manual_rotation, w_mm, h_mm, margin_mm = state
        try:
            self._source, eff_rot = render_label_preview(
                src_doc=doc,
                page_index=page_index,
                crop=crop,
                manual_rotation=manual_rotation,
                margin_mm=margin_mm,
                label_w_mm=w_mm,
                label_h_mm=h_mm,
                max_width=2000,
                max_height=3000,
            )
        except Exception:
            self._source = None
            self._source_key = None
            self._info.configure(text="Preview unavailable")
        else:
            self._source_key = state
            self._pan_x = self._pan_y = 0.0
            self._drag_start = None
            parts = [f"{w_mm / 10:g} × {h_mm / 10:g} cm", f"Margin {margin_mm:g} mm", f"{eff_rot}°"]
            if len(doc) > 1:
                parts.append(f"Page {page_index + 1}/{len(doc)}")
            self._info.configure(text=" · ".join(parts))
        self._draw()

    def _cancel_draw(self) -> None:
        if self._draw_after is not None:
            try:
                self.after_cancel(self._draw_after)
            except Exception:
                pass
            self._draw_after = None

    def _on_destroy(self, event=None) -> None:
        if event is None or event.widget is self:
            self._cancel_draw()

    def _schedule_draw(self, delay: int) -> None:
        self._cancel_draw()
        self._draw_after = self.after(delay, self._draw)

    def _draw(self, fast: bool = False) -> None:
        self._cancel_draw()
        if not self.winfo_exists():
            return
        preview = self._preview
        preview.delete("all")
        self._zoom_button.set_text(f"{self._zoom:.0%}")
        self._zoom_out_btn.set_enabled(self._zoom > 0.501)
        self._zoom_in_btn.set_enabled(self._zoom < 4.999)
        cw, ch = preview.winfo_width(), preview.winfo_height()
        if self._source is None or cw <= 2 or ch <= 2:
            self._pan_limit_x = self._pan_limit_y = 0.0
            preview.configure(cursor="arrow")
            return

        avail_w = max(1, cw - self._PAD)
        avail_h = max(1, ch - self._PAD)
        src_w, src_h = self._source.size
        scale = min(avail_w / src_w, avail_h / src_h) * self._zoom
        disp_w = max(1.0, src_w * scale)
        disp_h = max(1.0, src_h * scale)
        self._pan_limit_x = max(0.0, (disp_w - avail_w) / 2.0)
        self._pan_limit_y = max(0.0, (disp_h - avail_h) / 2.0)
        self._pan_x = max(-self._pan_limit_x, min(self._pan_limit_x, self._pan_x))
        self._pan_y = max(-self._pan_limit_y, min(self._pan_limit_y, self._pan_y))

        px0 = (cw - disp_w) / 2.0 + self._pan_x
        py0 = (ch - disp_h) / 2.0 + self._pan_y
        ix0 = int(round(px0))
        iy0 = int(round(py0))
        ix1 = ix0 + max(1, int(round(disp_w)))
        iy1 = iy0 + max(1, int(round(disp_h)))

        vx0 = max(0, ix0)
        vy0 = max(0, iy0)
        vx1 = min(cw, ix1)
        vy1 = min(ch, iy1)
        self._label_box = (ix0, iy0, ix1, iy1)

        if vx1 > vx0 and vy1 > vy0:
            sx0 = max(0.0, (vx0 - ix0) / (ix1 - ix0) * src_w)
            sy0 = max(0.0, (vy0 - iy0) / (iy1 - iy0) * src_h)
            sx1 = min(src_w, (vx1 - ix0) / (ix1 - ix0) * src_w)
            sy1 = min(src_h, (vy1 - iy0) / (iy1 - iy0) * src_h)
            resample = Image.Resampling.BILINEAR if fast else Image.Resampling.LANCZOS
            view_img = self._source.resize(
                (vx1 - vx0, vy1 - vy0), resample=resample, box=(sx0, sy0, sx1, sy1)
            )
            self._photo = ImageTk.PhotoImage(view_img, master=preview)
            preview.create_image(vx0, vy0, anchor="nw", image=self._photo)

        preview.create_rectangle(ix0 - 1, iy0 - 1, ix1, iy1, outline=BORDER_STRONG)
        preview.configure(cursor="fleur" if self._pan_limit_x or self._pan_limit_y else "arrow")
        if fast:
            self._schedule_draw(90)

    def zoom_in(self) -> str:
        return self._set_zoom(1.25)

    def zoom_out(self) -> str:
        return self._set_zoom(0.8)

    def reset_zoom(self) -> str:
        return self._reset_zoom()

    def pan(self, dx: float, dy: float) -> str:
        return self._pan(dx, dy)

    def _set_zoom(self, factor: float, anchor_x: float = 0.0, anchor_y: float = 0.0) -> str:
        new_zoom = max(0.5, min(5.0, self._zoom * factor))
        if self._source is not None and new_zoom != self._zoom:
            ratio = new_zoom / self._zoom
            self._pan_x = self._pan_x * ratio + anchor_x * (1.0 - ratio)
            self._pan_y = self._pan_y * ratio + anchor_y * (1.0 - ratio)
            self._zoom = new_zoom
            self._draw(fast=True)
        return "break"

    def _reset_zoom(self, _event=None) -> str:
        self._zoom = 1.0
        self._pan_x = self._pan_y = 0.0
        self._draw()
        return "break"

    def _on_wheel(self, event) -> str:
        steps = _wheel_steps(event)
        return self._set_zoom(
            1.15 ** steps,
            event.x - self._preview.winfo_width() / 2,
            event.y - self._preview.winfo_height() / 2,
        )

    def _pan(self, dx: float, dy: float) -> str:
        if not (self._pan_limit_x or self._pan_limit_y):
            return "break"
        self._pan_x += dx
        self._pan_y += dy
        self._draw(fast=True)
        return "break"

    def _on_canvas_down(self, event) -> None:
        self._on_canvas_down_cb(event)
        self.focus_set()
        x0, y0, x1, y1 = self._label_box
        if (self._pan_limit_x or self._pan_limit_y) and x0 <= event.x <= x1 and y0 <= event.y <= y1:
            self._drag_start = (event.x - self._pan_x, event.y - self._pan_y)

    def _on_canvas_drag(self, event) -> None:
        if self._drag_start is not None:
            self._pan(event.x - self._drag_start[0] - self._pan_x, event.y - self._drag_start[1] - self._pan_y)

    def _on_canvas_release(self, _event) -> None:
        if self._drag_start is not None:
            self._drag_start = None
            self._draw()
