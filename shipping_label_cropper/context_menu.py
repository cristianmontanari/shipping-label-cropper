"""Cross-platform OS file-manager / right-click context menu integration."""

from __future__ import annotations

import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import uuid
from pathlib import Path, PurePath
from typing import Any, Dict, List, Optional, Tuple

from .icons import generate_app_icon, generate_app_icon_png, get_project_root
from .presets import get_config_dir

try:
    import winreg
except ImportError:  # pragma: no cover - non-Windows fallback
    winreg = None  # type: ignore

CONTEXT_MENU_KEY = r"Software\Classes\SystemFileAssociations\.pdf\shell\ShippingLabelCropper"
LEGACY_CONTEXT_MENU_KEYS = (
    r"Software\Classes\SystemFileAssociations\.pdf\shell\CropPDF10x15",
)
MENU_LABEL = "Crop Shipping Label"
SENDTO_SHORTCUT_NAME = "Crop Shipping Label.lnk"
LEGACY_SENDTO_SHORTCUT_NAMES = ("Crop Label 10x15.lnk", "Ritaglia Etichetta 10x15.lnk")

LINUX_DESKTOP_FILENAME = "shipping-label-cropper.desktop"
LEGACY_LINUX_DESKTOP_FILENAMES = ("croppdf-10x15.desktop",)
LINUX_DOLPHIN_FILENAME = "shipping-label-cropper-servicemenu.desktop"
LEGACY_LINUX_DOLPHIN_FILENAMES = ("croppdf-10x15-servicemenu.desktop",)
LEGACY_LINUX_NAUTILUS_NAMES = ("Crop Label 10x15",)
MACOS_WORKFLOW_NAME = "Crop Shipping Label.workflow"
LEGACY_MACOS_WORKFLOW_NAMES = ("Crop Label 10x15.workflow",)


def is_frozen() -> bool:
    """Return True when running from a bundled executable (e.g. PyInstaller)."""
    return bool(getattr(sys, "frozen", False))


def get_python_executable() -> Path:
    """Return the preferred Python executable from the project's venv or current interpreter."""
    if is_frozen():
        return Path(sys.executable).resolve()
    root = get_project_root()
    if sys.platform == "win32":
        venv_pythonw = root / ".venv" / "Scripts" / "pythonw.exe"
        if venv_pythonw.exists():
            return venv_pythonw
        venv_python = root / ".venv" / "Scripts" / "python.exe"
        if venv_python.exists():
            return venv_python
        exe = Path(sys.executable)
        candidate = exe.with_name("pythonw.exe")
        return candidate if candidate.exists() else exe

    for name in ("python3", "python"):
        venv_py = root / ".venv" / "bin" / name
        if venv_py.exists():
            return venv_py
    return Path(sys.executable)


def get_launch_command_parts() -> Tuple[Path, Optional[Path]]:
    """Return (executable, main_py) for OS integration commands.

    In a frozen standalone executable, main_py is None so the OS invokes the executable directly.
    """
    if is_frozen():
        return Path(sys.executable).resolve(), None
    return get_python_executable(), get_project_root() / "main.py"


def build_windows_menu_command(executable: PurePath, main_py: Optional[PurePath] = None) -> str:
    """Build the Windows Registry shell command for opening a PDF file."""
    if main_py is None:
        return f'"{executable}" "%1"'
    return f'"{executable}" "{main_py}" "%1"'


def get_context_menu_ui_strings() -> Dict[str, str]:
    """Return platform-appropriate labels for the desktop integration toggle in the header."""
    if sys.platform == "darwin":
        return {
            "active": "Quick Action active",
            "inactive": "Enable Quick Action",
            "dialog_title": "Finder Quick Action",
            "remove_heading": "Remove Finder Quick Action?",
            "remove_message": (
                f'The "{MENU_LABEL}" Quick Action is active for PDF files in Finder.\n'
                "You can re-enable it anytime by clicking here."
            ),
            "open_hint": f"Right-click a PDF in Finder → Quick Actions → {MENU_LABEL}.",
        }
    if sys.platform != "win32":
        return {
            "active": "File menu active",
            "inactive": "Enable file menu",
            "dialog_title": "File manager menu",
            "remove_heading": "Remove from file manager menu?",
            "remove_message": (
                f'The "{MENU_LABEL}" entry is active for PDF files.\n'
                "You can re-enable it anytime by clicking here."
            ),
            "open_hint": f"Right-click a PDF in your file manager → Open With → {MENU_LABEL}.",
        }
    return {
        "active": "Right-click menu active",
        "inactive": "Enable right-click menu",
        "dialog_title": "Right-click menu",
        "remove_heading": "Remove from right-click menu?",
        "remove_message": (
            f'The "{MENU_LABEL}" entry is active for PDF files.\n'
            "You can re-enable it anytime by clicking here."
        ),
        "open_hint": (
            f"Right-click a PDF in File Explorer → {MENU_LABEL}.\n"
            "On Windows 11, use Shift + right-click or choose Show more options."
        ),
    }


def _linux_xdg_data_home() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg)
    return Path.home() / ".local" / "share"


def _linux_desktop_entry_path(data_home: Optional[Path] = None) -> Path:
    base = data_home or _linux_xdg_data_home()
    return base / "applications" / LINUX_DESKTOP_FILENAME


def _linux_nautilus_script_path(data_home: Optional[Path] = None) -> Path:
    base = data_home or _linux_xdg_data_home()
    return base / "nautilus" / "scripts" / MENU_LABEL


def _linux_dolphin_servicemenu_path(data_home: Optional[Path] = None) -> Path:
    base = data_home or _linux_xdg_data_home()
    return base / "kio" / "servicemenus" / LINUX_DOLPHIN_FILENAME


def _desktop_exec_quote(arg: str) -> str:
    """Quote an argument for the Exec= key in an XDG .desktop file."""
    escaped = (
        arg.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("`", "\\`")
        .replace("%", "%%")
    )
    return f'"{escaped}"'


def _format_desktop_exec(python_exe: PurePath, main_py: Optional[PurePath]) -> str:
    if main_py is None:
        return f"{_desktop_exec_quote(str(python_exe))} %f"
    return f"{_desktop_exec_quote(str(python_exe))} {_desktop_exec_quote(str(main_py))} %f"


def build_linux_desktop_entry(
    python_exe: PurePath, main_py: Optional[PurePath], icon_png: PurePath
) -> str:
    """Build XDG .desktop content so PDF files offer 'Open With → Crop Shipping Label'."""
    exec_cmd = _format_desktop_exec(python_exe, main_py)
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={MENU_LABEL}\n"
        "Comment=Crop A4 shipping label PDFs to 4x6 / 10x15 cm thermal labels\n"
        f"Exec={exec_cmd}\n"
        f"Icon={icon_png}\n"
        "Terminal=false\n"
        "Categories=Utility;Office;\n"
        "MimeType=application/pdf;\n"
        "StartupWMClass=ShippingLabelCropper\n"
    )


def build_linux_dolphin_servicemenu(
    python_exe: PurePath, main_py: Optional[PurePath], icon_png: PurePath
) -> str:
    """Build a KDE Dolphin ServiceMenu .desktop file for direct right-click PDF cropping."""
    exec_cmd = _format_desktop_exec(python_exe, main_py)
    return (
        "[Desktop Entry]\n"
        "Type=Service\n"
        "MimeType=application/pdf;\n"
        "Actions=CropShippingLabel;\n"
        "X-KDE-Priority=TopLevel\n"
        "\n"
        "[Desktop Action CropShippingLabel]\n"
        f"Name={MENU_LABEL}\n"
        f"Icon={icon_png}\n"
        f"Exec={exec_cmd}\n"
    )


def build_linux_nautilus_script(python_exe: PurePath, main_py: Optional[PurePath]) -> str:
    """Build a GNOME Files (Nautilus) right-click script."""
    py_q = shlex.quote(str(python_exe))
    invoke = py_q if main_py is None else f"{py_q} {shlex.quote(str(main_py))}"
    return (
        "#!/usr/bin/env bash\n"
        'for f in "$@"; do\n'
        f'    nohup {invoke} "$f" >/dev/null 2>&1 &\n'
        "done\n"
    )


def _macos_workflow_path(services_dir: Optional[Path] = None) -> Path:
    base = services_dir or (Path.home() / "Library" / "Services")
    return base / MACOS_WORKFLOW_NAME


def create_macos_quick_action(
    python_exe: PurePath,
    main_py: Optional[PurePath],
    services_dir: Optional[Path] = None,
) -> Path:
    """Create a native macOS Finder Quick Action (.workflow) bundle for PDF files."""
    wf_dir = _macos_workflow_path(services_dir)
    contents_dir = wf_dir / "Contents"
    contents_dir.mkdir(parents=True, exist_ok=True)

    py_q = shlex.quote(str(python_exe))
    invoke = py_q if main_py is None else f"{py_q} {shlex.quote(str(main_py))}"
    shell_cmd = (
        'for f in "$@"; do\n'
        f'  nohup {invoke} "$f" >/dev/null 2>&1 &\n'
        "done\n"
    )

    info_plist: Dict[str, Any] = {
        "NSServices": [
            {
                "NSMenuItem": {"default": MENU_LABEL},
                "NSMessage": "runWorkflowAsService",
                "NSRequiredContext": {
                    "NSApplicationIdentifier": "com.apple.finder",
                },
                "NSSendFileTypes": ["com.adobe.pdf"],
            }
        ]
    }
    with (contents_dir / "Info.plist").open("wb") as fh:
        plistlib.dump(info_plist, fh, fmt=plistlib.FMT_XML)

    action_uuid = str(uuid.uuid4()).upper()
    input_uuid = str(uuid.uuid4()).upper()
    output_uuid = str(uuid.uuid4()).upper()
    document_wflow: Dict[str, Any] = {
        "AMApplicationBuild": "523",
        "AMApplicationVersion": "2.10",
        "AMDocumentVersion": "2",
        "actions": [
            {
                "action": {
                    "AMAccepts": {
                        "Container": "List",
                        "Optional": True,
                        "Types": ["com.apple.cocoa.path"],
                    },
                    "AMActionVersion": "2.0.3",
                    "AMApplication": ["Automator"],
                    "AMParameterProperties": {
                        "COMMAND_STRING": {},
                        "CheckedForUserDefaultShell": {},
                        "inputMethod": {},
                        "shell": {},
                        "source": {},
                    },
                    "AMProvides": {
                        "Container": "List",
                        "Types": ["com.apple.cocoa.path"],
                    },
                    "ActionBundlePath": "/System/Library/Automator/Run Shell Script.action",
                    "ActionName": "Run Shell Script",
                    "ActionParameters": {
                        "COMMAND_STRING": shell_cmd,
                        "CheckedForUserDefaultShell": True,
                        "inputMethod": 1,
                        "shell": "/bin/bash",
                        "source": "",
                    },
                    "BundleIdentifier": "com.apple.RunShellScript",
                    "CFBundleVersion": "2.0.3",
                    "CanShowSelectedItemsWhenRun": False,
                    "CanShowWhenRun": False,
                    "Category": ["AMCategoryUtilities"],
                    "Class Name": "RunShellScriptAction",
                    "InputUUID": input_uuid,
                    "Keywords": ["Shell", "Script", "Command", "Run", "Unix"],
                    "OutputUUID": output_uuid,
                    "UUID": action_uuid,
                    "UnlocalizedApplications": ["Automator"],
                    "arguments": {
                        "0": {
                            "default value": 0,
                            "name": "inputMethod",
                            "required": "0",
                            "type": "0",
                            "uuid": "0",
                        },
                        "1": {
                            "default value": "",
                            "name": "source",
                            "required": "0",
                            "type": "0",
                            "uuid": "1",
                        },
                        "2": {
                            "default value": False,
                            "name": "CheckedForUserDefaultShell",
                            "required": "0",
                            "type": "0",
                            "uuid": "2",
                        },
                        "3": {
                            "default value": "",
                            "name": "COMMAND_STRING",
                            "required": "0",
                            "type": "0",
                            "uuid": "3",
                        },
                        "4": {
                            "default value": "/bin/sh",
                            "name": "shell",
                            "required": "0",
                            "type": "0",
                            "uuid": "4",
                        },
                    },
                    "isViewVisible": 1,
                },
                "isViewVisible": 1,
            }
        ],
        "connectors": {},
        "workflowMetaData": {
            "applicationBundleID": "com.apple.finder",
            "applicationBundleIDsByPath": {
                "/System/Library/CoreServices/Finder.app": "com.apple.finder"
            },
            "applicationPath": "/System/Library/CoreServices/Finder.app",
            "applicationPaths": ["/System/Library/CoreServices/Finder.app"],
            "inputTypeIdentifier": "com.apple.Automator.fileSystemObject.PDF",
            "outputTypeIdentifier": "com.apple.Automator.nothing",
            "presentationMode": 15,
            "processesInput": 0,
            "serviceApplicationBundleID": "com.apple.finder",
            "serviceApplicationPath": "/System/Library/CoreServices/Finder.app",
            "serviceInputTypeIdentifier": "com.apple.Automator.fileSystemObject.PDF",
            "serviceOutputTypeIdentifier": "com.apple.Automator.nothing",
            "serviceProcessesInput": 0,
            "systemImageName": "NSActionTemplate",
            "useAutomaticInputType": 0,
            "workflowTypeIdentifier": "com.apple.Automator.servicesMenu",
        },
    }
    with (contents_dir / "document.wflow").open("wb") as fh:
        plistlib.dump(document_wflow, fh, fmt=plistlib.FMT_XML)
    return wf_dir


def is_context_menu_installed() -> bool:
    """Return True if the OS file-manager / context menu integration is installed."""
    if sys.platform == "win32":
        if winreg is None:
            return False
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CONTEXT_MENU_KEY):
                return True
        except OSError:
            return False

    if sys.platform == "darwin":
        wf = _macos_workflow_path()
        return (wf / "Contents" / "document.wflow").exists()

    return _linux_desktop_entry_path().exists()


def _delete_windows_reg_menu_key(key_path: str) -> None:
    if winreg is None:
        return
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path + r"\command")
    except FileNotFoundError:
        pass
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
    except FileNotFoundError:
        pass


def _remove_legacy_linux_entries() -> None:
    data_home = _linux_xdg_data_home()
    for name in LEGACY_LINUX_DESKTOP_FILENAMES:
        p = data_home / "applications" / name
        if p.exists():
            p.unlink()
    for name in LEGACY_LINUX_NAUTILUS_NAMES:
        p = data_home / "nautilus" / "scripts" / name
        if p.exists():
            p.unlink()
    for name in LEGACY_LINUX_DOLPHIN_FILENAMES:
        p = data_home / "kio" / "servicemenus" / name
        if p.exists():
            p.unlink()


def _remove_legacy_macos_workflows() -> None:
    services_dir = Path.home() / "Library" / "Services"
    for name in LEGACY_MACOS_WORKFLOW_NAMES:
        wf = services_dir / name
        if wf.exists():
            shutil.rmtree(wf, ignore_errors=True)


def get_persistent_linux_icon_png() -> Path:
    """Return a persistent PNG icon path for Linux .desktop entries.

    When running from a PyInstaller --onefile bundle, sys._MEIPASS is a temporary
    folder deleted on exit, so we store the icon inside the persistent config dir.
    """
    if is_frozen():
        target = get_config_dir() / "icon.png"
        if not target.exists():
            bundled = get_project_root() / "assets" / "icon.png"
            if bundled.exists():
                shutil.copy2(bundled, target)
            else:
                generate_app_icon_png(target)
        return target
    return generate_app_icon_png()


def install_context_menu() -> Tuple[bool, str]:
    """Install OS-native PDF right-click / file-manager integration (no admin required)."""
    python_exe, main_py = get_launch_command_parts()

    if sys.platform == "win32":
        if winreg is None:
            return False, "Windows Registry module is not available."

        icon_location = f"{python_exe},0" if main_py is None else str(generate_app_icon())
        command = build_windows_menu_command(python_exe, main_py)

        try:
            for legacy_key in LEGACY_CONTEXT_MENU_KEYS:
                _delete_windows_reg_menu_key(legacy_key)

            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, CONTEXT_MENU_KEY) as key:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, MENU_LABEL)
                winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, icon_location)

            with winreg.CreateKey(
                winreg.HKEY_CURRENT_USER, CONTEXT_MENU_KEY + r"\command"
            ) as cmd_key:
                winreg.SetValueEx(cmd_key, "", 0, winreg.REG_SZ, command)

            _remove_sendto_shortcut()
            _create_sendto_shortcut(python_exe, main_py, icon_location)
            return True, (
                "Context menu installed!\n\n"
                "On Windows 11, press Shift + Right-click on any PDF "
                f'(or Right-click → Show more options → "{MENU_LABEL}").'
            )
        except OSError as exc:
            return False, f"Error registering in the Windows Registry: {exc}"

    if sys.platform == "darwin":
        try:
            generate_app_icon_png()
            _remove_legacy_macos_workflows()
            create_macos_quick_action(python_exe, main_py)
            pbs = Path("/System/Library/CoreServices/pbs")
            if pbs.exists():
                subprocess.run(
                    [str(pbs), "-update"],
                    capture_output=True,
                    timeout=10,
                )
            return True, (
                "Finder Quick Action installed!\n\n"
                f'Right-click any PDF in Finder → Quick Actions → "{MENU_LABEL}".'
            )
        except Exception as exc:
            return False, f"Error installing macOS Quick Action: {exc}"

    # Linux / POSIX XDG desktop integration
    try:
        icon_png = get_persistent_linux_icon_png()
        _remove_legacy_linux_entries()
        desktop_path = _linux_desktop_entry_path()
        desktop_path.parent.mkdir(parents=True, exist_ok=True)
        desktop_path.write_text(
            build_linux_desktop_entry(python_exe, main_py, icon_png),
            encoding="utf-8",
        )
        try:
            os.chmod(desktop_path, 0o755)
        except OSError:
            pass

        extras: List[str] = []
        # GNOME Files (Nautilus) right-click → Scripts
        nautilus_dir = _linux_xdg_data_home() / "nautilus"
        if nautilus_dir.exists() or shutil.which("nautilus"):
            script_path = _linux_nautilus_script_path()
            script_path.parent.mkdir(parents=True, exist_ok=True)
            script_path.write_text(
                build_linux_nautilus_script(python_exe, main_py),
                encoding="utf-8",
            )
            try:
                os.chmod(script_path, 0o755)
            except OSError:
                pass
            extras.append("Nautilus Scripts")

        # KDE Dolphin right-click → Actions
        kio_dir = _linux_xdg_data_home() / "kio"
        if kio_dir.exists() or shutil.which("dolphin"):
            dolphin_path = _linux_dolphin_servicemenu_path()
            dolphin_path.parent.mkdir(parents=True, exist_ok=True)
            dolphin_path.write_text(
                build_linux_dolphin_servicemenu(python_exe, main_py, icon_png),
                encoding="utf-8",
            )
            try:
                os.chmod(dolphin_path, 0o755)
            except OSError:
                pass
            extras.append("Dolphin Actions")

        if shutil.which("update-desktop-database"):
            subprocess.run(
                ["update-desktop-database", str(desktop_path.parent)],
                capture_output=True,
                timeout=10,
            )

        extra_note = f" (also added to {', '.join(extras)})" if extras else ""
        return True, (
            f"File manager menu installed{extra_note}!\n\n"
            f'Right-click any PDF → Open With → "{MENU_LABEL}".'
        )
    except Exception as exc:
        return False, f"Error installing Linux desktop entry: {exc}"


def uninstall_context_menu() -> Tuple[bool, str]:
    """Remove OS-native PDF right-click / file-manager integration."""
    if sys.platform == "win32":
        if winreg is None:
            return False, "Windows Registry module is not available."

        try:
            for key_path in (CONTEXT_MENU_KEY, *LEGACY_CONTEXT_MENU_KEYS):
                _delete_windows_reg_menu_key(key_path)
            _remove_sendto_shortcut()
            return True, "Context menu removed successfully."
        except OSError as exc:
            return False, f"Error removing context menu: {exc}"

    if sys.platform == "darwin":
        try:
            wf_dir = _macos_workflow_path()
            if wf_dir.exists():
                shutil.rmtree(wf_dir)
            _remove_legacy_macos_workflows()
            pbs = Path("/System/Library/CoreServices/pbs")
            if pbs.exists():
                subprocess.run(
                    [str(pbs), "-update"],
                    capture_output=True,
                    timeout=10,
                )
            return True, "Finder Quick Action removed successfully."
        except Exception as exc:
            return False, f"Error removing Finder Quick Action: {exc}"

    # Linux / POSIX
    try:
        desktop_path = _linux_desktop_entry_path()
        for path in (
            desktop_path,
            _linux_nautilus_script_path(),
            _linux_dolphin_servicemenu_path(),
        ):
            if path.exists():
                path.unlink()
        _remove_legacy_linux_entries()
        if desktop_path.parent.exists() and shutil.which("update-desktop-database"):
            subprocess.run(
                ["update-desktop-database", str(desktop_path.parent)],
                capture_output=True,
                timeout=10,
            )
        return True, "File manager menu removed successfully."
    except Exception as exc:
        return False, f"Error removing Linux desktop entry: {exc}"


# ----------------------------------------------------------- Windows SendTo
def _sendto_shortcut_path(name: str = SENDTO_SHORTCUT_NAME) -> Optional[Path]:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "SendTo" / name


def _create_sendto_shortcut(
    pythonw: Path, main_py: Optional[Path], icon_location: Path | str
) -> None:
    lnk = _sendto_shortcut_path()
    if not lnk or not lnk.parent.exists():
        return

    icon_str = str(icon_location)
    if "," not in icon_str:
        icon_str = f"{icon_str},0"

    env = os.environ.copy()
    env["CROPPDF_LNK"] = str(lnk)
    env["CROPPDF_TARGET"] = str(pythonw)
    env["CROPPDF_MAIN_PY"] = str(main_py) if main_py is not None else ""
    env["CROPPDF_WORKDIR"] = str(main_py.parent if main_py is not None else pythonw.parent)
    env["CROPPDF_ICON"] = icon_str

    script = (
        "$ws = New-Object -ComObject WScript.Shell; "
        "$sc = $ws.CreateShortcut($env:CROPPDF_LNK); "
        "$sc.TargetPath = $env:CROPPDF_TARGET; "
        "if ($env:CROPPDF_MAIN_PY) { $sc.Arguments = '\"' + $env:CROPPDF_MAIN_PY + '\"' }; "
        "$sc.WorkingDirectory = $env:CROPPDF_WORKDIR; "
        "$sc.IconLocation = $env:CROPPDF_ICON; "
        "$sc.Save()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
        capture_output=True,
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        timeout=10,
    )


def _remove_sendto_shortcut() -> None:
    for name in (SENDTO_SHORTCUT_NAME, *LEGACY_SENDTO_SHORTCUT_NAMES):
        lnk = _sendto_shortcut_path(name)
        if lnk and lnk.exists():
            try:
                lnk.unlink()
            except OSError:
                pass
