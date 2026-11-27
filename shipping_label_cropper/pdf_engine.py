"""Core PDF rendering, smart label detection, preview, and vector 10x15 export."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pymupdf as fitz  # PyMuPDF
from PIL import Image, ImageOps

PT_PER_MM = 72.0 / 25.4  # 2.834645669...

LABEL_SIZES_MM: Dict[str, Tuple[float, float]] = {
    "10 × 15 cm (Standard 4×6\")": (100.0, 150.0),
    "10 × 10 cm (Square)": (100.0, 100.0),
    "10 × 19 cm (Long)": (100.0, 190.0),
    "10.2 × 15.2 cm (Exact 4×6\")": (101.6, 152.4),
}


@dataclass(frozen=True)
class CropRect:
    """Normalized crop rectangle in visual page coordinates [0.0, 1.0]."""

    x0: float
    y0: float
    x1: float
    y1: float

    def normalized(self) -> "CropRect":
        x0 = max(0.0, min(1.0, min(self.x0, self.x1)))
        x1 = max(0.0, min(1.0, max(self.x0, self.x1)))
        y0 = max(0.0, min(1.0, min(self.y0, self.y1)))
        y1 = max(0.0, min(1.0, max(self.y0, self.y1)))
        min_span = 0.01
        if x1 - x0 < min_span:
            x1 = min(1.0, x0 + min_span)
            x0 = max(0.0, x1 - min_span)
        if y1 - y0 < min_span:
            y1 = min(1.0, y0 + min_span)
            y0 = max(0.0, y1 - min_span)
        return CropRect(x0, y0, x1, y1)

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(0.0, self.y1 - self.y0)

    def to_pdf_rect(self, page_rect: fitz.Rect) -> fitz.Rect:
        r = self.normalized()
        return fitz.Rect(
            page_rect.x0 + r.x0 * page_rect.width,
            page_rect.y0 + r.y0 * page_rect.height,
            page_rect.x0 + r.x1 * page_rect.width,
            page_rect.y0 + r.y1 * page_rect.height,
        )

    def to_mm(self, page_rect: fitz.Rect) -> Tuple[float, float]:
        r = self.normalized()
        w_mm = (r.width * page_rect.width) / PT_PER_MM
        h_mm = (r.height * page_rect.height) / PT_PER_MM
        return w_mm, h_mm


FULL_PAGE_CROP = CropRect(0.0, 0.0, 1.0, 1.0)


def render_page_image(
    doc: fitz.Document,
    page_index: int,
    max_width: int = 1200,
    max_height: int = 1200,
    *,
    min_scale: float = 0.5,
) -> Tuple[Image.Image, fitz.Rect]:
    """Render a PDF page to an RGB PIL Image sized to fit max_width x max_height."""
    page = doc[page_index]
    rect = page.rect
    if rect.width <= 0 or rect.height <= 0:
        raise ValueError("The PDF page has invalid dimensions.")
    scale = min(max_width / rect.width, max_height / rect.height)
    scale = max(min_scale, min(scale, 4.0))
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    return img, rect


def detect_label_bounds(
    doc: fitz.Document,
    page_index: int,
    padding_mm: float = 3.0,
    white_threshold: int = 242,
) -> CropRect:
    """Automatically detect the non-white content bounding box of a PDF page."""
    page = doc[page_index]
    rect = page.rect
    # Render at moderate resolution (~110 DPI) for fast, accurate content detection
    scale = 110.0 / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    gray = ImageOps.grayscale(img)

    # Create binary mask where non-white pixels are 255
    mask = gray.point(lambda p: 255 if p < white_threshold else 0)
    bbox = mask.getbbox()
    if not bbox:
        return FULL_PAGE_CROP

    left, top, right, bottom = bbox
    w, h = img.size

    # Convert padding in mm to normalized fractions
    pad_x = (padding_mm * PT_PER_MM) / rect.width if rect.width > 0 else 0.0
    pad_y = (padding_mm * PT_PER_MM) / rect.height if rect.height > 0 else 0.0

    x0 = max(0.0, (left / w) - pad_x)
    y0 = max(0.0, (top / h) - pad_y)
    x1 = min(1.0, (right / w) + pad_x)
    y1 = min(1.0, (bottom / h) + pad_y)
    return CropRect(x0, y0, x1, y1).normalized()


def collect_export_pages(
    doc: fitz.Document,
    page_index: int,
    page_crops: Dict[int, CropRect],
    apply_all: bool,
    *,
    padding_mm: float = 3.0,
) -> List[Tuple[int, CropRect]]:
    """Build the list of (page_index, crop) pairs to export or print."""
    current = page_crops.get(page_index, FULL_PAGE_CROP)
    if not apply_all:
        return [(page_index, current)]

    result: List[Tuple[int, CropRect]] = []
    for idx in range(len(doc)):
        crop = page_crops.get(idx)
        if crop is None:
            crop = detect_label_bounds(doc, idx, padding_mm=padding_mm)
            page_crops[idx] = crop
        result.append((idx, crop))
    return result


def _create_output_document(
    src_doc: fitz.Document,
    page_crops: List[Tuple[int, CropRect]],
    manual_rotation: int = 0,
    margin_mm: float = 2.0,
    label_w_mm: float = 100.0,
    label_h_mm: float = 150.0,
    page_rotations: Optional[Dict[int, int]] = None,
) -> fitz.Document:
    """Build an in-memory vector PDF with exact thermal label page dimensions."""
    out_doc = fitz.open()
    target_w_pt = label_w_mm * PT_PER_MM
    target_h_pt = label_h_mm * PT_PER_MM
    margin_pt = max(0.0, margin_mm) * PT_PER_MM

    # Keep inner content area valid even if someone sets a large margin
    max_margin = min(target_w_pt, target_h_pt) * 0.25
    margin_pt = min(margin_pt, max_margin)

    inner_w = target_w_pt - 2.0 * margin_pt
    inner_h = target_h_pt - 2.0 * margin_pt

    for page_idx, crop in page_crops:
        rot_cw = (
            page_rotations.get(page_idx, manual_rotation)
            if page_rotations is not None
            else manual_rotation
        ) % 360
        src_page = src_doc[page_idx]
        src_rect = src_page.rect
        clip_rect = crop.to_pdf_rect(src_rect)
        if clip_rect.width <= 1.0 or clip_rect.height <= 1.0:
            clip_rect = src_rect

        # Effective visual dimensions after manual rotation
        eff_w, eff_h = (
            (clip_rect.height, clip_rect.width)
            if rot_cw in (90, 270)
            else (clip_rect.width, clip_rect.height)
        )

        # Scale proportionally to maximize fill inside margins and center
        scale = min(inner_w / eff_w, inner_h / eff_h)
        draw_w = eff_w * scale
        draw_h = eff_h * scale
        dx = (target_w_pt - draw_w) / 2.0
        dy = (target_h_pt - draw_h) / 2.0
        dest_rect = fitz.Rect(dx, dy, dx + draw_w, dy + draw_h)

        out_page = out_doc.new_page(width=target_w_pt, height=target_h_pt)
        tmp_doc: fitz.Document | None = None
        try:
            if src_page.first_annot is not None or src_page.first_widget is not None:
                # Flatten annotations/form widgets into vector content on a temporary copy.
                tmp_doc = fitz.open()
                tmp_doc.insert_pdf(src_doc, from_page=page_idx, to_page=page_idx)
                tmp_doc.bake(annots=True, widgets=True)
                render_doc = tmp_doc
                render_idx = 0
                render_page = tmp_doc[0]
            else:
                render_doc = src_doc
                render_idx = page_idx
                render_page = src_page

            orig_rot = render_page.rotation
            # show_pdf_page intersects clip against render_page.rect and drops
            # CropBox offsets when rotated, so temporarily clear /Rotate while grafting.
            src_clip = clip_rect * render_page.derotation_matrix if orig_rot else clip_rect
            pymupdf_rotate = (-(rot_cw + orig_rot)) % 360
            if orig_rot:
                render_page.set_rotation(0)
            try:
                out_page.show_pdf_page(
                    dest_rect,
                    render_doc,
                    render_idx,
                    clip=src_clip,
                    rotate=pymupdf_rotate,
                    keep_proportion=True,
                )
            finally:
                if orig_rot and render_doc is src_doc:
                    render_page.set_rotation(orig_rot)
        finally:
            if tmp_doc is not None and not tmp_doc.is_closed:
                tmp_doc.close()

    return out_doc


def export_cropped_pdf(
    src_doc: fitz.Document,
    output_path: Path | str,
    page_crops: List[Tuple[int, CropRect]],
    manual_rotation: int = 0,
    margin_mm: float = 2.0,
    label_w_mm: float = 100.0,
    label_h_mm: float = 150.0,
    page_rotations: Optional[Dict[int, int]] = None,
) -> Path:
    """Export selected pages cropped into an exact thermal label vector PDF."""
    output_path = Path(output_path)
    out_doc = _create_output_document(
        src_doc=src_doc,
        page_crops=page_crops,
        manual_rotation=manual_rotation,
        margin_mm=margin_mm,
        label_w_mm=label_w_mm,
        label_h_mm=label_h_mm,
        page_rotations=page_rotations,
    )
    try:
        out_doc.save(str(output_path), garbage=4, deflate=True)
    finally:
        if not out_doc.is_closed:
            out_doc.close()
    return output_path


def render_label_preview(
    src_doc: fitz.Document,
    page_index: int,
    crop: CropRect,
    manual_rotation: int = 0,
    margin_mm: float = 2.0,
    label_w_mm: float = 100.0,
    label_h_mm: float = 150.0,
    max_width: int = 220,
    max_height: int = 330,
) -> Tuple[Image.Image, int]:
    """Render an exact visual preview of the resulting thermal label page."""
    out_doc = _create_output_document(
        src_doc=src_doc,
        page_crops=[(page_index, crop)],
        manual_rotation=manual_rotation,
        margin_mm=margin_mm,
        label_w_mm=label_w_mm,
        label_h_mm=label_h_mm,
    )
    try:
        page = out_doc[0]
        rect = page.rect
        scale = min(max_width / rect.width, max_height / rect.height)
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    finally:
        out_doc.close()
    return img, manual_rotation % 360


def render_pdf_for_printing(
    pdf_path: Path | str,
    dpi: int = 300,
) -> List[Image.Image]:
    """Render each page of a cropped PDF at high DPI for direct thermal printing."""
    doc = fitz.open(str(pdf_path))
    images: List[Image.Image] = []
    scale = dpi / 72.0
    try:
        for page in doc:
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            images.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
    finally:
        doc.close()
    return images
