"""Cross-platform thermal printer discovery, capabilities, queue control, and printing."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image


# ------------------------------------------------------------ CUPS Helpers
def _cups_env() -> Dict[str, str]:
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    return env


def _parse_cups_printers(lpstat_e_out: str = "", lpstat_p_out: str = "") -> List[str]:
    """Parse printer names from `lpstat -e` or `lpstat -p` output."""
    printers: List[str] = []
    if lpstat_e_out.strip():
        for line in lpstat_e_out.splitlines():
            name = line.strip()
            if name and name not in printers:
                printers.append(name)
        if printers:
            return printers

    for line in lpstat_p_out.splitlines():
        m = re.match(r"^printer\s+(\S+)\s+", line.strip())
        if m:
            name = m.group(1)
            if name not in printers:
                printers.append(name)
    return printers


def _parse_cups_lpoptions(lpoptions_out: str) -> Dict[str, Any]:
    """Parse `lpoptions -p <printer> -l` output for thermal darkness and print speed options."""
    darkness: Optional[Dict[str, Any]] = None
    speed: Optional[Dict[str, Any]] = None

    for raw_line in lpoptions_out.splitlines():
        if ":" not in raw_line:
            continue
        left, right = raw_line.split(":", 1)
        left = left.strip()
        tokens = right.strip().split()
        if not left or not tokens:
            continue

        if "/" in left:
            opt_key, display_name = left.split("/", 1)
        else:
            opt_key, display_name = left, left
        opt_key = opt_key.strip()
        display_name = display_name.strip()
        combined = f"{opt_key} {display_name}"

        current_val: Optional[str] = None
        clean_tokens: List[str] = []
        for tok in tokens:
            if tok.startswith("*"):
                val = tok[1:]
                current_val = val
                clean_tokens.append(val)
            else:
                clean_tokens.append(tok)

        if not clean_tokens:
            continue

        if darkness is None and re.search(
            r"darkness|density|intensity|heat|burn", combined, re.IGNORECASE
        ):
            # Extract numeric options (ignoring sentinel values like Default/None/PrinterDefault)
            numeric_vals: List[int] = []
            all_numeric_or_sentinel = True
            for val in clean_tokens:
                if re.fullmatch(r"-?\d+", val):
                    numeric_vals.append(int(val))
                elif re.fullmatch(r"(default|none|printerdefault|auto)", val, re.IGNORECASE):
                    continue
                else:
                    all_numeric_or_sentinel = False
                    break

            if all_numeric_or_sentinel and len(numeric_vals) >= 2:
                sorted_nums = sorted(set(numeric_vals))
                # Support contiguous integer ranges directly as slider
                if sorted_nums == list(range(sorted_nums[0], sorted_nums[-1] + 1)):
                    min_v = sorted_nums[0]
                    max_v = sorted_nums[-1]
                    if current_val and re.fullmatch(r"-?\d+", current_val):
                        cur_int = int(current_val)
                    else:
                        cur_int = (min_v + max_v) // 2
                    darkness = {
                        "param_name": opt_key,
                        "min": min_v,
                        "max": max_v,
                        "default": cur_int,
                        "current": cur_int,
                    }

        if speed is None and re.search(
            r"print\s*speed|printspeed|\bspeed\b|jobspeed", combined, re.IGNORECASE
        ):
            opts = [{"name": val, "label": val} for val in clean_tokens]
            cur_opt = current_val if current_val in clean_tokens else clean_tokens[0]
            speed = {
                "feature_name": opt_key,
                "options": opts,
                "current": cur_opt,
            }

    return {
        "supported": (darkness is not None or speed is not None),
        "use_current_feature": None,
        "use_current_no_option": None,
        "darkness": darkness,
        "speed": speed,
    }


def _list_cups_printers() -> List[str]:
    if not shutil.which("lpstat"):
        return []
    env = _cups_env()
    out_e = ""
    out_p = ""
    try:
        res_e = subprocess.run(
            ["lpstat", "-e"],
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )
        if res_e.returncode == 0:
            out_e = res_e.stdout
    except Exception:
        pass

    if not out_e.strip():
        try:
            res_p = subprocess.run(
                ["lpstat", "-p"],
                capture_output=True,
                text=True,
                env=env,
                timeout=10,
            )
            if res_p.returncode == 0:
                out_p = res_p.stdout
        except Exception:
            pass
    return _parse_cups_printers(out_e, out_p)


def _get_cups_printer_capabilities(printer_name: str) -> Dict[str, Any]:
    if not printer_name:
        return {"supported": False}
    env = _cups_env()
    result: Dict[str, Any] = {
        "supported": False,
        "use_current_feature": None,
        "use_current_no_option": None,
        "darkness": None,
        "speed": None,
        "queue_error": False,
        "queue_status": "Ready",
        "job_count": 0,
    }

    if shutil.which("lpoptions"):
        try:
            res = subprocess.run(
                ["lpoptions", "-p", printer_name, "-l"],
                capture_output=True,
                text=True,
                env=env,
                timeout=10,
            )
            if res.returncode == 0 and res.stdout.strip():
                result.update(_parse_cups_lpoptions(res.stdout))
        except Exception:
            pass

    if shutil.which("lpstat"):
        try:
            res_p = subprocess.run(
                ["lpstat", "-p", printer_name],
                capture_output=True,
                text=True,
                env=env,
                timeout=10,
            )
            if res_p.returncode == 0:
                low = res_p.stdout.lower()
                if "disabled" in low:
                    result["queue_error"] = True
                    result["queue_status"] = "Disabled / Paused"
                elif "printing" in low:
                    result["queue_status"] = "Printing"
                else:
                    result["queue_status"] = "Idle"
        except Exception:
            pass

        try:
            res_o = subprocess.run(
                ["lpstat", "-o", printer_name],
                capture_output=True,
                text=True,
                env=env,
                timeout=10,
            )
            if res_o.returncode == 0:
                jobs = [ln for ln in res_o.stdout.splitlines() if ln.strip()]
                result["job_count"] = len(jobs)
        except Exception:
            pass

    return result


def _clear_cups_printer_queue(printer_name: str) -> Tuple[bool, str]:
    if not printer_name:
        return False, "No printer selected."
    if not shutil.which("cancel"):
        return False, "CUPS 'cancel' command not found."
    try:
        res = subprocess.run(
            ["cancel", "-a", printer_name],
            capture_output=True,
            text=True,
            env=_cups_env(),
            timeout=15,
        )
        if res.returncode != 0:
            err = (res.stderr or res.stdout).strip()
            return False, f"Unable to clear print queue: {err}"
        if shutil.which("cupsenable"):
            subprocess.run(
                ["cupsenable", printer_name],
                capture_output=True,
                env=_cups_env(),
                timeout=10,
            )
        return True, "Print queue cleared."
    except Exception as exc:
        return False, f"Unable to clear print queue: {exc}"


def _print_via_cups(
    images: List[Image.Image],
    printer_name: str,
    label_w_mm: float = 100.0,
    label_h_mm: float = 150.0,
    *,
    pdf_path: Optional[Path] = None,
    page_count: Optional[int] = None,
    copies: int = 1,
    darkness: Optional[int] = None,
    speed_option: Optional[str] = None,
    darkness_param: Optional[str] = None,
    speed_feature: Optional[str] = None,
) -> Tuple[bool, str]:
    if not shutil.which("lp"):
        return False, "CUPS 'lp' command not found. Make sure CUPS printing is installed."

    if pdf_path is None and not images:
        return False, "No pages to print."

    num_copies = max(1, min(99, int(copies)))
    try:
        with tempfile.TemporaryDirectory(prefix="croppdf_print_") as tmpdir:
            target_file: Path
            num_pages: int
            if pdf_path is not None and Path(pdf_path).exists():
                target_file = Path(pdf_path)
                num_pages = page_count if (page_count and page_count > 0) else max(1, len(images))
            else:
                target_file = Path(tmpdir) / "labels.pdf"
                rgb_imgs = [im.convert("RGB") for im in images]
                rgb_imgs[0].save(
                    str(target_file),
                    format="PDF",
                    resolution=300.0,
                    save_all=True,
                    append_images=rgb_imgs[1:],
                )
                num_pages = len(rgb_imgs)

            cmd: List[str] = [
                "lp",
                "-d",
                printer_name,
                "-n",
                str(num_copies),
                "-o",
                f"media=Custom.{label_w_mm:g}x{label_h_mm:g}mm",
                "-o",
                "fit-to-page",
            ]
            if darkness is not None and darkness_param:
                cmd.extend(["-o", f"{darkness_param}={int(darkness)}"])
            if speed_option and speed_feature:
                cmd.extend(["-o", f"{speed_feature}={speed_option}"])
            cmd.extend(["--", str(target_file)])

            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                env=_cups_env(),
                timeout=30,
            )
            if res.returncode != 0:
                err = (res.stderr or res.stdout).strip()
                return False, f"Print error: {err}"

            total_labels = num_pages * num_copies
            label_word = "label" if total_labels == 1 else "labels"
            return True, f'Sent {total_labels} {label_word} to "{printer_name}".'
    except Exception as exc:
        return False, f"Print error: {exc}"


# --------------------------------------------------- Windows PowerShell Scripts
_WIN_FIND_TICKET_CHILD = r"""
function Find-TicketChild($doc, $localName, $attrName, $childLocalName) {
    foreach ($el in $doc.DocumentElement.ChildNodes) {
        if ($el.LocalName -eq $localName -and $el.GetAttribute('name') -eq $attrName) {
            foreach ($sub in $el.ChildNodes) {
                if ($sub.LocalName -eq $childLocalName) {
                    return $sub
                }
            }
        }
    }
    return $null
}
"""

_WIN_CAPABILITIES_SCRIPT = (
    r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName ReachFramework, System.Printing
$printerName = $env:CROPPDF_PRINTER_NAME
$ps = New-Object System.Printing.LocalPrintServer
$pq = $ps.GetPrintQueue($printerName)

$useCurFeature = $null
$useCurNoOpt = $null
$darkness = $null
$speed = $null

function Get-ChildProp($node, $propSuffix) {
    foreach ($c in $node.ChildNodes) {
        if ($c.LocalName -eq 'Property' -and ($c.GetAttribute('name') -like "*$propSuffix")) {
            return $c.InnerText.Trim()
        }
    }
    return $null
}
"""
    + _WIN_FIND_TICKET_CHILD
    + r"""
try {
    try {
        $ptXml = (New-Object System.IO.StreamReader($pq.UserPrintTicket.GetXmlStream())).ReadToEnd()
        $capXml = (New-Object System.IO.StreamReader($pq.GetPrintCapabilitiesAsXml())).ReadToEnd()

        $ptDoc = New-Object System.Xml.XmlDocument
        $ptDoc.LoadXml($ptXml)
        $capDoc = New-Object System.Xml.XmlDocument
        $capDoc.LoadXml($capXml)

        foreach ($node in $capDoc.DocumentElement.ChildNodes) {
            $name = $node.GetAttribute('name')
            if (-not $name) { continue }
            $common = Get-ChildProp $node ':CommonName'

            if ($node.LocalName -eq 'Feature' -and $name -match 'JobUseCurrentPrinterSettings$') {
                $useCurFeature = $name
                foreach ($opt in $node.ChildNodes) {
                    if ($opt.LocalName -eq 'Option' -and $opt.GetAttribute('name') -match 'OptNo$') {
                        $useCurNoOpt = $opt.GetAttribute('name')
                        break
                    }
                }
            }

            if ($null -eq $darkness -and $node.LocalName -eq 'ParameterDef' -and ($common -eq 'Ptxcn_PrintDarkness' -or $name -match 'Darkness|Density|Intensity')) {
                $minV = Get-ChildProp $node ':MinValue'
                $maxV = Get-ChildProp $node ':MaxValue'
                $defV = Get-ChildProp $node ':DefaultValue'
                if ($null -ne $minV -and $null -ne $maxV) {
                    $curNode = Find-TicketChild $ptDoc 'ParameterInit' $name 'Value'
                    $curVal = if ($curNode) { [int]$curNode.InnerText.Trim() } elseif ($null -ne $defV) { [int]$defV } else { [int]$minV }
                    $darkness = @{
                        param_name = $name
                        min = [int]$minV
                        max = [int]$maxV
                        default = if ($null -ne $defV) { [int]$defV } else { $curVal }
                        current = $curVal
                    }
                }
            }

            if ($null -eq $speed -and $node.LocalName -eq 'Feature' -and ($common -eq 'Ptxcn_PrintSpeed' -or $name -match 'PrintSpeed|JobSpeed')) {
                $opts = @()
                foreach ($opt in $node.ChildNodes) {
                    if ($opt.LocalName -eq 'Option') {
                        $optName = $opt.GetAttribute('name')
                        $label = Get-ChildProp $opt ':DisplayName'
                        if (-not $label) { $label = $optName }
                        $opts += @{ name = $optName; label = $label }
                    }
                }
                if ($opts.Count -gt 0) {
                    $curOpt = Find-TicketChild $ptDoc 'Feature' $name 'Option'
                    $curName = if ($curOpt) { $curOpt.GetAttribute('name') } else { $opts[0].name }
                    $speed = @{
                        feature_name = $name
                        options = $opts
                        current = $curName
                    }
                }
            }
        }
    } catch {
        # Basic drivers may not expose PrintTicket/PrintCapabilities XML; still report queue status below.
    }

    $pq.Refresh()
    $result = @{
        supported = ($null -ne $darkness -or $null -ne $speed)
        use_current_feature = $useCurFeature
        use_current_no_option = $useCurNoOpt
        darkness = $darkness
        speed = $speed
        queue_error = [bool]($pq.IsInError -or $pq.IsOffline)
        queue_status = [string]$pq.QueueStatus
        job_count = [int]$pq.NumberOfJobs
    }
    $result | ConvertTo-Json -Depth 5 -Compress
} finally {
    $pq.Dispose()
    $ps.Dispose()
}
"""
)

_WIN_CLEAR_QUEUE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$printerName = $env:CROPPDF_PRINTER_NAME
$jobs = @(Get-PrintJob -PrinterName $printerName)
$failed = @()
foreach ($j in $jobs) {
    try {
        Remove-PrintJob -InputObject $j -ErrorAction Stop
    } catch {
        $failed += $_.Exception.Message
    }
}
if ($failed.Count -gt 0) {
    throw ($failed -join '; ')
}
Write-Output $jobs.Count
"""

_WIN_DEVMODE_BLOCK = (
    r"""
Add-Type -AssemblyName ReachFramework, System.Printing
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class CropPdfDevMode {
    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern IntPtr GlobalAlloc(uint uFlags, UIntPtr dwBytes);
    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern IntPtr GlobalLock(IntPtr hMem);
    [DllImport("kernel32.dll", SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool GlobalUnlock(IntPtr hMem);
    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern IntPtr GlobalFree(IntPtr hMem);

    public static IntPtr BytesToHGlobal(byte[] data) {
        IntPtr h = GlobalAlloc(0x0042, (UIntPtr)data.Length);
        IntPtr p = GlobalLock(h);
        try { Marshal.Copy(data, 0, p, data.Length); }
        finally { GlobalUnlock(h); }
        return h;
    }
}
"@
$ps = New-Object System.Printing.LocalPrintServer
$pq = $ps.GetPrintQueue($printerName)
try {
    $ptXml = (New-Object System.IO.StreamReader($pq.UserPrintTicket.GetXmlStream())).ReadToEnd()
} finally {
    $pq.Dispose()
    $ps.Dispose()
}
$ptDoc = New-Object System.Xml.XmlDocument
$ptDoc.LoadXml($ptXml)
$ns = 'http://schemas.microsoft.com/windows/2003/08/printing/printschemaframework'
$xsi = 'http://www.w3.org/2001/XMLSchema-instance'
"""
    + _WIN_FIND_TICKET_CHILD
    + r"""
$useFeatName = $env:CROPPDF_USE_FEAT
$useNoName = $env:CROPPDF_USE_NO
if ($useFeatName -and $useNoName) {
    $uOpt = Find-TicketChild $ptDoc 'Feature' $useFeatName 'Option'
    if ($uOpt) {
        $uOpt.SetAttribute('name', $useNoName)
    } else {
        $f = $ptDoc.CreateElement('psf', 'Feature', $ns)
        $f.SetAttribute('name', $useFeatName)
        $o = $ptDoc.CreateElement('psf', 'Option', $ns)
        $o.SetAttribute('name', $useNoName)
        $f.AppendChild($o) | Out-Null
        $ptDoc.DocumentElement.AppendChild($f) | Out-Null
    }
}

$darkParamName = $env:CROPPDF_DARK_PARAM
$darkValue = $env:CROPPDF_DARK_VAL
if ($darkParamName -and $darkValue -ne '') {
    $dVal = Find-TicketChild $ptDoc 'ParameterInit' $darkParamName 'Value'
    if ($dVal) {
        $dVal.InnerText = $darkValue
    } else {
        $p = $ptDoc.CreateElement('psf', 'ParameterInit', $ns)
        $p.SetAttribute('name', $darkParamName)
        $v = $ptDoc.CreateElement('psf', 'Value', $ns)
        $v.SetAttribute('type', $xsi, 'xsd:integer')
        $v.InnerText = $darkValue
        $p.AppendChild($v) | Out-Null
        $ptDoc.DocumentElement.AppendChild($p) | Out-Null
    }
}

$spdFeatName = $env:CROPPDF_SPD_FEAT
$spdOptName = $env:CROPPDF_SPD_OPT
if ($spdFeatName -and $spdOptName) {
    $sOpt = Find-TicketChild $ptDoc 'Feature' $spdFeatName 'Option'
    if ($sOpt) {
        $sOpt.SetAttribute('name', $spdOptName)
    } else {
        $f = $ptDoc.CreateElement('psf', 'Feature', $ns)
        $f.SetAttribute('name', $spdFeatName)
        $o = $ptDoc.CreateElement('psf', 'Option', $ns)
        $o.SetAttribute('name', $spdOptName)
        $f.AppendChild($o) | Out-Null
        $ptDoc.DocumentElement.AppendChild($f) | Out-Null
    }
}

$ms = New-Object System.IO.MemoryStream
$ptDoc.Save($ms)
$ms.Position = 0
$newPt = New-Object System.Printing.PrintTicket($ms)
$conv = New-Object System.Printing.Interop.PrintTicketConverter($printerName, [System.Printing.Interop.PrintTicketConverter]::MaxPrintSchemaVersion)
try {
    $dmBytes = $conv.ConvertPrintTicketToDevMode($newPt, [System.Printing.Interop.BaseDevModeType]::UserDefault)
    $hDm = [CropPdfDevMode]::BytesToHGlobal($dmBytes)
    try {
        $pd.PrinterSettings.SetHdevmode($hDm)
        $pd.DefaultPageSettings.SetHdevmode($hDm)
    } finally {
        [CropPdfDevMode]::GlobalFree($hDm) | Out-Null
    }
} finally {
    $conv.Dispose()
}
"""
)


def _run_powershell(
    script: str,
    *,
    env: Optional[Dict[str, str]] = None,
    timeout: int = 15,
) -> subprocess.CompletedProcess[str]:
    """Run a PowerShell command with UTF-8 output encoding and no console window."""
    full_script = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n" + script
    return subprocess.run(
        ["powershell", "-NoProfile", "-Command", full_script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        timeout=timeout,
    )


def _build_windows_print_script(w_hi: int, h_hi: int, *, apply_devmode: bool) -> str:
    """Build the Windows PowerShell printing script using env vars for all dynamic inputs."""
    devmode_block = _WIN_DEVMODE_BLOCK if apply_devmode else ""
    landscape_literal = "$true" if int(w_hi) > int(h_hi) else "$false"
    return f"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$printerName = $env:CROPPDF_PRINTER_NAME
$files = @(Get-ChildItem -LiteralPath $env:CROPPDF_PRINT_DIR -Filter 'label_*.png' | Sort-Object Name | Select-Object -ExpandProperty FullName)
$idx = 0
$pd = New-Object System.Drawing.Printing.PrintDocument
$pd.PrinterSettings.PrinterName = $printerName
if (-not $pd.PrinterSettings.IsValid) {{
    throw ('Invalid printer: ' + $printerName)
}}
{devmode_block}
$paper = New-Object System.Drawing.Printing.PaperSize('Label10x15', {int(w_hi)}, {int(h_hi)})
$pd.DefaultPageSettings.PaperSize = $paper
$pd.DefaultPageSettings.Landscape = {landscape_literal}
$pd.DefaultPageSettings.Margins = New-Object System.Drawing.Printing.Margins(0, 0, 0, 0)
$pd.OriginAtMargins = $false
$pd.add_PrintPage({{
    param($sender, $e)
    $img = [System.Drawing.Image]::FromFile($files[$script:idx])
    try {{
        $bounds = $e.PageBounds
        $scale = [Math]::Min($bounds.Width / [double]$img.Width, $bounds.Height / [double]$img.Height)
        $dw = [float]($img.Width * $scale)
        $dh = [float]($img.Height * $scale)
        $dx = [float]($bounds.X + ($bounds.Width - $dw) / 2.0)
        $dy = [float]($bounds.Y + ($bounds.Height - $dh) / 2.0)
        $e.Graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $e.Graphics.DrawImage($img, $dx, $dy, $dw, $dh)
    }} finally {{
        $img.Dispose()
    }}
    $script:idx++
    $e.HasMorePages = ($script:idx -lt $files.Count)
}})
$pd.Print()
Start-Sleep -Milliseconds 350
$pInfo = Get-Printer -Name $printerName -ErrorAction SilentlyContinue
if ($pInfo -and ($pInfo.PrinterStatus -match 'Error|Offline|PaperOut|DoorOpen')) {{
    throw "Job queued, but printer is in '$($pInfo.PrinterStatus)' state (check connection or clear stuck jobs)."
}}
"""


# ----------------------------------------------------------- Public Printing
def list_printers() -> List[str]:
    """List installed printers (Windows via PowerShell, Linux/macOS via CUPS)."""
    if sys.platform != "win32":
        return _list_cups_printers()

    try:
        res = _run_powershell("Get-Printer | Select-Object -ExpandProperty Name", timeout=10)
        if res.returncode == 0:
            return [line.strip() for line in res.stdout.splitlines() if line.strip()]
    except Exception:
        pass
    return []


def get_printer_capabilities(printer_name: str) -> Dict[str, Any]:
    """Query thermal darkness, speed, and queue state for a printer."""
    if not printer_name:
        return {"supported": False}

    if sys.platform != "win32":
        return _get_cups_printer_capabilities(printer_name)

    env = os.environ.copy()
    env["CROPPDF_PRINTER_NAME"] = printer_name
    try:
        res = _run_powershell(_WIN_CAPABILITIES_SCRIPT, env=env, timeout=15)
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout.strip())
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {"supported": False}


def clear_printer_queue(printer_name: str) -> Tuple[bool, str]:
    """Remove all queued/stuck print jobs for the specified printer."""
    if not printer_name:
        return False, "No printer selected."

    if sys.platform != "win32":
        return _clear_cups_printer_queue(printer_name)

    env = os.environ.copy()
    env["CROPPDF_PRINTER_NAME"] = printer_name
    try:
        res = _run_powershell(_WIN_CLEAR_QUEUE_SCRIPT, env=env, timeout=15)
        if res.returncode != 0:
            err = (res.stderr or res.stdout).strip()
            return False, f"Unable to clear print queue: {err}"
        return True, "Print queue cleared."
    except Exception as exc:
        return False, f"Unable to clear print queue: {exc}"


def print_images_to_thermal_printer(
    images: List[Image.Image],
    printer_name: str,
    label_w_mm: float = 100.0,
    label_h_mm: float = 150.0,
    *,
    pdf_path: Optional[Path] = None,
    page_count: Optional[int] = None,
    copies: int = 1,
    darkness: Optional[int] = None,
    speed_option: Optional[str] = None,
    darkness_param: Optional[str] = None,
    speed_feature: Optional[str] = None,
    use_current_feature: Optional[str] = None,
    use_current_no_option: Optional[str] = None,
) -> Tuple[bool, str]:
    """Print cropped labels directly to a thermal printer (Windows GDI/PrintTicket or CUPS)."""
    num_copies = max(1, min(99, int(copies)))
    if sys.platform != "win32":
        return _print_via_cups(
            images=images,
            printer_name=printer_name,
            label_w_mm=label_w_mm,
            label_h_mm=label_h_mm,
            pdf_path=pdf_path,
            page_count=page_count,
            copies=num_copies,
            darkness=darkness,
            speed_option=speed_option,
            darkness_param=darkness_param,
            speed_feature=speed_feature,
        )

    if not images:
        return False, "No pages to print."

    try:
        with tempfile.TemporaryDirectory(prefix="croppdf_print_") as tmpdir:
            expanded = [img for _ in range(num_copies) for img in images]
            for idx, img in enumerate(expanded):
                p = Path(tmpdir) / f"label_{idx:04d}.png"
                img.save(str(p), format="PNG", dpi=(300, 300))

            # PaperSize uses hundredths of an inch
            w_hi = max(100, int(round((label_w_mm / 25.4) * 100)))
            h_hi = max(100, int(round((label_h_mm / 25.4) * 100)))

            apply_devmode = darkness is not None or bool(speed_option)
            ps_script = _build_windows_print_script(w_hi, h_hi, apply_devmode=apply_devmode)

            env = os.environ.copy()
            env["CROPPDF_PRINTER_NAME"] = printer_name
            env["CROPPDF_PRINT_DIR"] = tmpdir
            env["CROPPDF_USE_FEAT"] = use_current_feature or ""
            env["CROPPDF_USE_NO"] = use_current_no_option or ""
            env["CROPPDF_DARK_PARAM"] = darkness_param or ""
            env["CROPPDF_DARK_VAL"] = "" if darkness is None else str(int(darkness))
            env["CROPPDF_SPD_FEAT"] = speed_feature or ""
            env["CROPPDF_SPD_OPT"] = speed_option or ""

            res = _run_powershell(ps_script, env=env, timeout=30)
            if res.returncode != 0:
                err = (res.stderr or res.stdout).strip()
                return False, f"Print error: {err}"
            label_word = "label" if len(expanded) == 1 else "labels"
            return True, f'Sent {len(expanded)} {label_word} to "{printer_name}".'
    except Exception as exc:
        return False, f"Print error: {exc}"
