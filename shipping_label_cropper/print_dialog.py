"""Direct thermal printing dialog with safe background capability loading and queue control."""

from __future__ import annotations

import tkinter as tk
from typing import Any, Callable, Dict, List, Optional

from .presets import load_config, save_config
from .printing import clear_printer_queue, get_printer_capabilities
from .widgets import (
    BORDER,
    BORDER_STRONG,
    HOVER_BG,
    INK,
    MUTED,
    SUBTLE_BG,
    WHITE,
    MinimalButton,
    MinimalDropdown,
    MinimalSlider,
    run_in_background,
    ui_font,
)


def _controls_signature(caps: Optional[Dict[str, Any]]) -> Any:
    """Return a comparable signature of printer capability controls to detect changes."""
    if not isinstance(caps, dict) or not caps.get("supported"):
        return (False, None, None)
    dark = caps.get("darkness")
    dark_sig = (
        (dark.get("param_name"), dark.get("min"), dark.get("max"))
        if isinstance(dark, dict)
        else None
    )
    spd = caps.get("speed")
    spd_sig = None
    if isinstance(spd, dict) and isinstance(spd.get("options"), list):
        spd_sig = (
            spd.get("feature_name"),
            tuple(
                (str(item.get("name", "")), str(item.get("label", "")))
                for item in spd["options"]
                if isinstance(item, dict) and item.get("name")
            ),
        )
    return (True, dark_sig, spd_sig)


class DirectPrintDialog:
    """Modal dialog for choosing a thermal printer, darkness/speed, and clearing stuck jobs."""

    def __init__(
        self,
        root: tk.Tk,
        printers: List[str],
        caps_cache: Dict[str, Dict[str, Any]],
        *,
        on_active_dialog: Callable[[Optional[tk.Toplevel]], None],
        show_dialog: Callable[..., bool],
        show_error: Callable[[str, str], None],
        on_confirm_print: Callable[[Dict[str, Any]], bool],
        job_summary: Optional[str] = None,
        page_count: int = 1,
    ) -> None:
        self._root = root
        self._caps_cache = caps_cache
        self._on_active_dialog = on_active_dialog
        self._show_dialog = show_dialog
        self._show_error = show_error
        self._on_confirm_print = on_confirm_print
        self._base_summary = job_summary
        self._page_count = max(1, int(page_count))
        self._anchor_pos: Optional[Tuple[int, int]] = None

        self.dlg = tk.Toplevel(root)
        self._on_active_dialog(self.dlg)
        self.dlg.title("Direct Print")
        self.dlg.configure(bg=WHITE)
        self.dlg.resizable(False, False)
        self.dlg.transient(root)
        self.dlg.grab_set()

        self._cfg = load_config()
        last_p = self._cfg.get("last_printer", "")
        self._printer_var = tk.StringVar(value=last_p if last_p in printers else printers[0])
        raw_settings = self._cfg.get("printer_settings")
        self._saved_printer_settings: Dict[str, Dict[str, Any]] = (
            raw_settings if isinstance(raw_settings, dict) else {}
        )
        self._copies_var = tk.IntVar(value=1)

        self._caps: Optional[Dict[str, Any]] = None
        self._darkness_var: Optional[tk.IntVar] = None
        self._speed_var: Optional[tk.StringVar] = None
        self._speed_label_to_opt: Dict[str, str] = {}
        self._request_id = 0

        self._summary_lbl: Optional[tk.Label] = None
        if job_summary:
            summary_box = tk.Frame(self.dlg, bg=SUBTLE_BG, highlightbackground=BORDER, highlightthickness=1)
            summary_box.pack(fill=tk.X, padx=20, pady=(16, 0))
            self._summary_lbl = tk.Label(
                summary_box, text=job_summary, bg=SUBTLE_BG, fg=INK,
                font=ui_font(8, semibold=True), anchor="w", padx=10, pady=7,
            )
            self._summary_lbl.pack(fill=tk.X)

        tk.Label(
            self.dlg, text="Thermal printer", bg=WHITE, fg=INK, font=ui_font(9, semibold=True)
        ).pack(anchor="w", padx=20, pady=(14 if job_summary else 18, 6))

        self._printer_dropdown = MinimalDropdown(
            self.dlg,
            variable=self._printer_var,
            values=printers,
            command=self._load_printer_settings,
            font=ui_font(9),
        )
        self._printer_dropdown.pack(fill=tk.X, padx=20, pady=(0, 12))

        # Copies stepper row
        copies_row = tk.Frame(self.dlg, bg=WHITE)
        copies_row.pack(fill=tk.X, padx=20, pady=(0, 12))
        tk.Label(
            copies_row, text="Copies", bg=WHITE, fg=INK, font=ui_font(9, semibold=True)
        ).pack(side=tk.LEFT)
        stepper = tk.Frame(copies_row, bg=WHITE)
        stepper.pack(side=tk.RIGHT)
        self._copies_minus = MinimalButton(
            stepper, "−", lambda: self._step_copies(-1), padx=8, pady=2, takefocus=True,
        )
        self._copies_minus.pack(side=tk.LEFT)
        self._copies_lbl = tk.Label(
            stepper, text="1", width=3, bg=WHITE, fg=INK, font=ui_font(9, semibold=True), anchor="center",
        )
        self._copies_lbl.pack(side=tk.LEFT, padx=4)
        self._copies_plus = MinimalButton(
            stepper, "+", lambda: self._step_copies(1), padx=8, pady=2, takefocus=True,
        )
        self._copies_plus.pack(side=tk.LEFT)
        self._sync_copies_ui()

        self._settings_frame = tk.Frame(self.dlg, bg=WHITE)
        self._settings_frame.pack(fill=tk.X, padx=20, pady=(0, 6))
        self._queue_frame = tk.Frame(self._settings_frame, bg=WHITE)
        self._queue_frame.pack(fill=tk.X)
        self._controls_frame = tk.Frame(self._settings_frame, bg=WHITE)
        self._controls_frame.pack(fill=tk.X)

        row = tk.Frame(self.dlg, bg=WHITE)
        row.pack(fill=tk.X, padx=20, pady=(12, 18))

        self._cancel_btn = MinimalButton(row, text="Cancel", command=self.close, pady=7, takefocus=True)
        self._cancel_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self._print_btn = MinimalButton(
            row, text="Print", command=self._do_print, variant="primary",
            font=ui_font(9, semibold=True), pady=7, takefocus=True,
        )
        self._print_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))

        self._recenter(initial=True)
        self.dlg.protocol("WM_DELETE_WINDOW", self.close)
        self.dlg.bind("<Escape>", self.close)
        self.dlg.bind("<Return>", self._on_return)
        self.dlg.bind("<KP_Enter>", self._on_return)
        self._print_btn.focus_set()
        self._load_printer_settings(self._printer_var.get())

    def _step_copies(self, delta: int) -> None:
        val = max(1, min(99, self._copies_var.get() + delta))
        self._copies_var.set(val)
        self._sync_copies_ui()

    def _sync_copies_ui(self) -> None:
        copies = self._copies_var.get()
        self._copies_lbl.configure(text=str(copies))
        self._copies_minus.set_enabled(copies > 1)
        self._copies_plus.set_enabled(copies < 99)
        if self._summary_lbl is not None and self._base_summary:
            total = self._page_count * copies
            if copies > 1:
                self._summary_lbl.configure(text=f"{self._base_summary}  × {copies} ({total} total)")
            else:
                self._summary_lbl.configure(text=self._base_summary)

    def _on_return(self, _event=None) -> str:
        if self._print_btn._enabled:
            self._do_print()
        return "break"

    def _recenter(self, *, initial: bool = False) -> None:
        if not self.dlg.winfo_exists():
            return
        self.dlg.update_idletasks()
        dw = max(348, self.dlg.winfo_reqwidth())
        dh = self.dlg.winfo_reqheight()
        self._root.update_idletasks()
        scr_w = self._root.winfo_screenwidth()
        scr_h = self._root.winfo_screenheight()
        if initial or self._anchor_pos is None:
            rx = self._root.winfo_rootx() + (self._root.winfo_width() - dw) // 2
            ry = self._root.winfo_rooty() + (self._root.winfo_height() - dh) // 2
            rx = max(20, min(scr_w - dw - 20, rx))
            ry = max(20, min(scr_h - dh - 40, ry))
            self._anchor_pos = (rx, ry)
        else:
            rx, ry = self._anchor_pos
            rx = max(20, min(scr_w - dw - 20, rx))
            ry = max(20, min(scr_h - dh - 40, ry))
        self.dlg.geometry(f"{dw}x{dh}+{rx}+{ry}")

    def _render_queue_banner(self, printer_name: str, caps: Dict[str, Any]) -> None:
        for child in self._queue_frame.winfo_children():
            child.destroy()

        job_count = int(caps.get("job_count") or 0)
        if not (caps.get("queue_error") or job_count > 0):
            return

        q_status = str(caps.get("queue_status") or "Error")
        q_banner = tk.Frame(self._queue_frame, bg="#FEF2F2", highlightbackground="#FECACA", highlightthickness=1)
        q_banner.pack(fill=tk.X, pady=(0, 12))
        q_inner = tk.Frame(q_banner, bg="#FEF2F2", padx=10, pady=7)
        q_inner.pack(fill=tk.X)
        msg_txt = (
            f"Queue: {q_status} ({job_count} job{'s' if job_count != 1 else ''})"
            if job_count > 0
            else f"Printer status: {q_status}"
        )
        tk.Label(
            q_inner, text=msg_txt, bg="#FEF2F2", fg="#991B1B", font=ui_font(8, semibold=True)
        ).pack(side=tk.LEFT)

        def _clear_queue(p_name: str = printer_name) -> None:
            clear_btn.set_text("Clearing…")
            clear_btn.set_enabled(False)
            self._print_btn.set_enabled(False)

            def _on_cleared(result: Any, exc: Optional[BaseException]) -> None:
                if not self.dlg.winfo_exists():
                    return
                if exc is not None:
                    ok_q, err_q = False, str(exc)
                else:
                    ok_q, err_q = result
                self._caps_cache.pop(p_name, None)
                if not ok_q:
                    self._show_dialog("Queue error", "Unable to clear queue", err_q)
                if self._printer_var.get() == p_name:
                    self._load_printer_settings(p_name, force_refresh=True)

            run_in_background(self.dlg, lambda: clear_printer_queue(p_name), _on_cleared)

        clear_btn = MinimalButton(
            q_inner,
            text="Clear queue",
            command=_clear_queue,
            variant="secondary",
            font=ui_font(8, semibold=True),
            padx=8,
            pady=2,
            takefocus=True,
        )
        clear_btn.pack(side=tk.RIGHT)

    def _render_controls(self, printer_name: str, caps: Dict[str, Any]) -> None:
        for child in self._controls_frame.winfo_children():
            child.destroy()

        self._caps = caps
        self._darkness_var = None
        self._speed_var = None
        self._speed_label_to_opt = {}

        if not caps.get("supported"):
            return

        p_saved = self._saved_printer_settings.get(printer_name, {})
        if not isinstance(p_saved, dict):
            p_saved = {}

        dark = caps.get("darkness")
        if isinstance(dark, dict):
            d_min = int(dark.get("min", 0))
            d_max = int(dark.get("max", 15))
            d_cur = int(dark.get("current", dark.get("default", d_min)))
            if isinstance(p_saved.get("darkness"), int):
                d_init = max(d_min, min(d_max, int(p_saved["darkness"])))
            else:
                d_init = max(d_min, min(d_max, d_cur))

            d_var = tk.IntVar(value=d_init)
            self._darkness_var = d_var

            d_hdr = tk.Frame(self._controls_frame, bg=WHITE)
            d_hdr.pack(fill=tk.X, pady=(0, 4))
            tk.Label(
                d_hdr, text="Darkness", bg=WHITE, fg=INK, font=ui_font(9, semibold=True)
            ).pack(side=tk.LEFT)
            d_val_lbl = tk.Label(
                d_hdr, text=f"{d_init} / {d_max}", bg=WHITE, fg=MUTED, font=ui_font(9, semibold=True)
            )
            d_val_lbl.pack(side=tk.RIGHT)

            MinimalSlider(
                self._controls_frame,
                variable=d_var,
                min_val=d_min,
                max_val=d_max,
                command=lambda v, lbl=d_val_lbl, mx=d_max: lbl.configure(text=f"{v} / {mx}"),
            ).pack(fill=tk.X, pady=(0, 12))

        spd = caps.get("speed")
        if isinstance(spd, dict) and isinstance(spd.get("options"), list) and spd["options"]:
            opts = spd["options"]
            label_to_opt: Dict[str, str] = {}
            opt_to_label: Dict[str, str] = {}
            labels: List[str] = []
            for item in opts:
                if isinstance(item, dict) and item.get("name"):
                    lbl = str(item.get("label") or item["name"])
                    opt_name = str(item["name"])
                    label_to_opt[lbl] = opt_name
                    opt_to_label[opt_name] = lbl
                    labels.append(lbl)

            if labels:
                saved_opt = str(p_saved.get("speed", ""))
                cur_opt = str(spd.get("current", ""))
                init_opt = saved_opt if saved_opt in opt_to_label else (
                    cur_opt if cur_opt in opt_to_label else str(opts[0]["name"])
                )
                s_var = tk.StringVar(value=opt_to_label.get(init_opt, labels[0]))
                self._speed_var = s_var
                self._speed_label_to_opt = label_to_opt

                tk.Label(
                    self._controls_frame, text="Print speed", bg=WHITE, fg=INK, font=ui_font(9, semibold=True)
                ).pack(anchor="w", pady=(0, 6))
                MinimalDropdown(
                    self._controls_frame, variable=s_var, values=labels, font=ui_font(9)
                ).pack(fill=tk.X, pady=(0, 4))

    def _load_printer_settings(self, printer_name: str, force_refresh: bool = False) -> None:
        self._request_id += 1
        req_id = self._request_id

        cached = None if force_refresh else self._caps_cache.get(printer_name)
        if cached is not None:
            self._render_queue_banner(printer_name, cached)
            self._render_controls(printer_name, cached)
            self._print_btn.set_enabled(True)
            self._recenter()
        else:
            self._caps = None
            self._darkness_var = None
            self._speed_var = None
            self._speed_label_to_opt = {}
            self._print_btn.set_enabled(False)
            for child in self._queue_frame.winfo_children():
                child.destroy()
            for child in self._controls_frame.winfo_children():
                child.destroy()
            tk.Label(
                self._controls_frame,
                text="Loading printer settings...",
                bg=WHITE,
                fg=MUTED,
                font=ui_font(8),
            ).pack(anchor="w", pady=(0, 4))
            self._recenter()

        def _on_loaded(caps: Any, exc: Optional[BaseException]) -> None:
            failed = exc is not None or not isinstance(caps, dict)
            resolved = {"supported": False} if failed else caps
            if not failed:
                self._caps_cache[printer_name] = resolved
            if not self.dlg.winfo_exists() or self._request_id != req_id or self._printer_var.get() != printer_name:
                return
            self._render_queue_banner(printer_name, resolved)
            if cached is None or _controls_signature(cached) != _controls_signature(resolved):
                self._render_controls(printer_name, resolved)
            else:
                self._caps = resolved
            self._print_btn.set_enabled(True)
            self._recenter()

        run_in_background(self.dlg, lambda: get_printer_capabilities(printer_name), _on_loaded)

    def close(self, _e=None) -> str:
        self._on_active_dialog(None)
        if self.dlg.winfo_exists():
            try:
                self.dlg.grab_release()
            except Exception:
                pass
            self.dlg.destroy()
        return "break"

    def _do_print(self) -> None:
        chosen = self._printer_var.get()
        self._cfg["last_printer"] = chosen

        caps = self._caps if isinstance(self._caps, dict) else {}
        darkness_val: Optional[int] = None
        darkness_param: Optional[str] = None
        speed_opt: Optional[str] = None
        speed_feature: Optional[str] = None
        use_cur_feat = caps.get("use_current_feature")
        use_cur_no = caps.get("use_current_no_option")

        if self._darkness_var is not None and isinstance(caps.get("darkness"), dict):
            darkness_val = int(self._darkness_var.get())
            darkness_param = str(caps["darkness"].get("param_name") or "")

        if self._speed_var is not None and isinstance(caps.get("speed"), dict):
            speed_opt = self._speed_label_to_opt.get(self._speed_var.get())
            speed_feature = str(caps["speed"].get("feature_name") or "")

        if darkness_val is not None or speed_opt is not None:
            ps_map = self._cfg.get("printer_settings")
            if not isinstance(ps_map, dict):
                ps_map = {}
            entry = ps_map.get(chosen)
            if not isinstance(entry, dict):
                entry = {}
            if darkness_val is not None:
                entry["darkness"] = darkness_val
            if speed_opt is not None:
                entry["speed"] = speed_opt
            ps_map[chosen] = entry
            self._cfg["printer_settings"] = ps_map

        try:
            save_config(self._cfg)
        except OSError as exc:
            self._show_error(
                "Unable to save printer settings",
                f"Check write access to the app settings and try again.\n\n{exc}",
            )
            return

        job = {
            "printer_name": chosen,
            "copies": max(1, min(99, int(self._copies_var.get()))),
            "darkness": darkness_val,
            "speed_option": speed_opt,
            "darkness_param": darkness_param,
            "speed_feature": speed_feature,
            "use_current_feature": str(use_cur_feat) if use_cur_feat else None,
            "use_current_no_option": str(use_cur_no) if use_cur_no else None,
        }
        if self._on_confirm_print(job):
            self.close()
