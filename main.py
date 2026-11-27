"""Entry point for Shipping Label Cropper."""

from __future__ import annotations

import argparse

from shipping_label_cropper import __version__
from shipping_label_cropper.context_menu import install_context_menu, uninstall_context_menu


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="shipping-label-cropper",
        description="Shipping Label Cropper - Crop A4 shipping label PDFs to 4x6 / 10x15 cm thermal labels",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument("pdf", nargs="?", help="Path to the PDF file to open")
    parser.add_argument(
        "--install-menu",
        action="store_true",
        help="Install the file manager / right-click menu entry for PDF files",
    )
    parser.add_argument(
        "--uninstall-menu",
        action="store_true",
        help="Remove the file manager / right-click menu entry",
    )
    args = parser.parse_args()

    if args.install_menu:
        ok, msg = install_context_menu()
        print(msg)
        return 0 if ok else 1

    if args.uninstall_menu:
        ok, msg = uninstall_context_menu()
        print(msg)
        return 0 if ok else 1

    from shipping_label_cropper.gui import ShippingLabelCropperApp

    app = ShippingLabelCropperApp(initial_pdf=args.pdf)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
