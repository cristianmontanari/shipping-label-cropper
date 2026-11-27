# Changelog

All notable changes to **Shipping Label Cropper** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-11-27

### Added
- Minimal black-and-white desktop UI for cropping A4 shipping label PDFs to thermal label formats (`10×15 cm`, `10×10 cm`, `10×19 cm`, exact `4×6"`).
- 100% vector PDF export with annotation/widget flattening and rotated-page handling.
- Automatic non-white label bounds detection, quick presets (`Auto`, `Full page`), manual 90° rotation, margin selection (`0`, `2`, `4 mm`), and undo/redo history.
- Real-time label preview with expandable zoom/pan modal and multi-page navigation.
- Direct thermal printing on Windows (with printer darkness/speed controls and queue clearing) and CUPS on macOS/Linux.
- Cross-platform right-click / file manager integration (Windows Explorer & SendTo, macOS Finder Quick Action, Linux XDG `.desktop`, Nautilus Scripts, KDE Dolphin ServiceMenu), including support for standalone frozen executables.
- `--version` CLI flag, `pyproject.toml` packaging metadata, and automated test suite.
- GitHub Actions CI (Windows, macOS, Linux) and cross-platform standalone release workflow (Windows `.exe`, macOS Apple Silicon & Intel `.dmg`, Linux `.tar.gz` with SHA-256 checksums).
- Open-source documentation and community templates (`LICENSE` under GNU AGPL-3.0, `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, Issue & PR templates).
