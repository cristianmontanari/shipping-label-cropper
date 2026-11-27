# Contributing to Shipping Label Cropper

Thank you for your interest in contributing to **Shipping Label Cropper**!

## Development Setup

### Prerequisites
- **Python 3.10+** with `tkinter` and `venv`
  - **Windows:** Standard Python.org installer includes `tkinter`.
  - **macOS:** Python.org installer or `brew install python-tk@3.12`.
  - **Linux:** `sudo apt install python3-tk python3-venv` (Debian/Ubuntu), `sudo dnf install python3-tkinter` (Fedora), or `sudo pacman -S tk` (Arch).

### Local Environment
```bash
git clone https://github.com/cristianmontanari/shipping-label-cropper.git
cd shipping-label-cropper
python3 -m venv .venv
```

Activate the virtual environment and install dependencies:
- **Windows (PowerShell):**
  ```powershell
  .\.venv\Scripts\Activate.ps1
  pip install -r requirements.txt
  ```
- **macOS / Linux:**
  ```bash
  source .venv/bin/activate
  pip install -r requirements.txt
  ```

Run the app:
```bash
python main.py
```

## Running Tests

Run the automated test suite before opening a pull request:
```bash
python test_shipping_label_cropper.py
```

On headless Linux environments, run tests under a virtual framebuffer:
```bash
xvfb-run -a python3 test_shipping_label_cropper.py
```

## Guidelines

- **Keep changes focused:** One bug fix or feature per pull request.
- **Cross-platform awareness:** Changes to printing, shortcuts, fonts, or file-manager menus should not break Windows, macOS, or Linux.
- **Privacy first:** Never commit real shipping labels, screenshots containing real customer addresses/names/tracking numbers, or personal printer logs. Use synthetic demo labels instead.
- **Add tests:** Include regression tests in `test_shipping_label_cropper.py` for bug fixes and new core behavior.

## Reporting Bugs & Requesting Features

Please use the GitHub Issue templates when opening a bug report or feature request. For printing issues, include your OS, thermal printer model, driver/CUPS setup, and (if relevant) an anonymized sample PDF.
