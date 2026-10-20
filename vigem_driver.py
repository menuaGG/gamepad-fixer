#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ViGEmBus surucu tespiti, temizligi ve kurulumu.

Bu modul vgamepad'e BAGIMLI DEGILDIR; boylece ViGEmBus surucusu hic
kurulu olmasa bile uygulama durumu okuyabilir ve kurulum onerebilir.
"""

import ctypes
import os
import tempfile
import time


# --- ViGEmBus surucu yardimcilari (eksikse otomatik kurulum icin) ---

VIGEM_SERVICE_KEY = r"SYSTEM\CurrentControlSet\Services\ViGEmBus"
VIGEM_API_LATEST = "https://api.github.com/repos/nefarius/ViGEmBus/releases/latest"
# 1.22.0 paketinin içindeki resmi sürücü dosyası 1.21.442.0'dır.
VIGEM_MIN_VERSION = (1, 21, 442)


def vigem_driver_installed():
    """ViGEmBus surucu servisi kayitli mi? True/False; okunamazsa None."""
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, VIGEM_SERVICE_KEY):
            return True
    except OSError:
        return False


def system_boot_time():
    """Windows'un açılış zamanını (Unix zaman damgası) döndürür."""
    try:
        ticks = ctypes.windll.kernel32.GetTickCount64()
        return time.time() - (ticks / 1000.0)
    except Exception:
        return None


def read_file_version(path):
    """Bir PE dosyasının (ör. .sys) sürümünü (a, b, c, d) olarak okur."""
    try:
        ver = ctypes.windll.version
        ver.GetFileVersionInfoSizeW.argtypes = (
            ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32))
        ver.GetFileVersionInfoSizeW.restype = ctypes.c_uint32
        ver.GetFileVersionInfoW.argtypes = (
            ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
            ctypes.c_void_p)
        ver.GetFileVersionInfoW.restype = ctypes.c_int
        ver.VerQueryValueW.argtypes = (
            ctypes.c_void_p, ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint32))
        ver.VerQueryValueW.restype = ctypes.c_int

        path = str(path)
        size = ver.GetFileVersionInfoSizeW(path, None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(path, 0, size, buf):
            return None
        ptr = ctypes.c_void_p()
        length = ctypes.c_uint32()
        if not ver.VerQueryValueW(buf, "\\", ctypes.byref(ptr),
                                  ctypes.byref(length)):
            return None

        class _VS_FIXEDFILEINFO(ctypes.Structure):
            _fields_ = (
                ("dwSignature", ctypes.c_uint32),
                ("dwStrucVersion", ctypes.c_uint32),
                ("dwFileVersionMS", ctypes.c_uint32),
                ("dwFileVersionLS", ctypes.c_uint32),
                ("dwProductVersionMS", ctypes.c_uint32),
                ("dwProductVersionLS", ctypes.c_uint32),
                ("dwFileFlagsMask", ctypes.c_uint32),
                ("dwFileFlags", ctypes.c_uint32),
                ("dwFileOS", ctypes.c_uint32),
                ("dwFileType", ctypes.c_uint32),
                ("dwFileSubtype", ctypes.c_uint32),
                ("dwFileDateMS", ctypes.c_uint32),
                ("dwFileDateLS", ctypes.c_uint32),
            )

        info = ctypes.cast(ptr, ctypes.POINTER(_VS_FIXEDFILEINFO)).contents
        ms, ls = info.dwFileVersionMS, info.dwFileVersionLS
        return (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
    except Exception:
        return None


def version_text(version):
    """(1, 22, 0, 0) -> '1.22.0.0'"""
    if not version:
        return None
    return ".".join(str(int(x)) for x in version)


def vigem_sys_path():
    """Etkin ViGEmBus.sys dosyasının yolu (servis kaydından okunur)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, VIGEM_SERVICE_KEY) as k:
            image, _ = winreg.QueryValueEx(k, "ImagePath")
        image = str(image).strip().strip('"')
        low = image.lower()
        if low.startswith(r"\systemroot"):
            image = os.environ.get("SystemRoot", r"C:\Windows") + image[11:]
        elif low.startswith("system32"):
            image = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                                 image)
        if image and os.path.isfile(image):
            return image
    except Exception:
        pass
    return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                        "System32", "drivers", "ViGEmBus.sys")


def vigem_driver_state():
    """ViGEmBus kurulumunun ayrıntılı durumunu döndürür.

    Dönüş sözlüğü:
      service       : servis kaydı var mı? (True/False/None)
      version       : (a, b, c, d) ya da None
      version_text  : "1.17.333.0" gibi
      sys_exists    : ViGEmBus.sys dosyası var mı?
      too_old       : sürüm 1.22.0'dan eski mi?
      repair_needed : eski/bozuk kurulum var mı? (üzerine kurulum YAPILMAMALI)
    """
    service = vigem_driver_installed()
    sys_path = vigem_sys_path()
    sys_exists = os.path.isfile(sys_path)
    version = read_file_version(sys_path) if sys_exists else None
    too_old = bool(version and tuple(version[:3]) < VIGEM_MIN_VERSION)
    repair_needed = bool(service and (too_old or not sys_exists))
    return {
        "service": service,
        "version": version,
        "version_text": version_text(version),
        "sys_path": sys_path,
        "sys_exists": sys_exists,
        "too_old": too_old,
        "repair_needed": repair_needed,
    }


def vigem_cleanup_driver(timeout=300):
    """Eski/bozuk ViGEmBus sürücüsünü yönetici izniyle temizler.

    ÖNEMLİ: Bu işlemden sonra bilgisayar yeniden başlatılmalıdır; ancak o
    zaman eski sürücü bellekten tamamen kalkar ve yeni sürüm güvenle kurulur.

    Dönüş: {"ok": bool, "message": str, "log": str}
    """
    base = os.path.join(tempfile.gettempdir(), "GamepadFixer")
    try:
        os.makedirs(base, exist_ok=True)
    except OSError:
        base = tempfile.gettempdir()
    log_path = os.path.join(base, "vigem_cleanup.log")
    script_path = os.path.join(base, "vigem_cleanup.ps1")
    try:
        if os.path.exists(log_path):
            os.remove(log_path)
    except OSError:
        pass

    script = r'''$ErrorActionPreference = "SilentlyContinue"
$log = '%LOG%'
"=== ViGEmBus cleanup start ===" | Out-File $log -Encoding ASCII
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
"Admin: $isAdmin" | Out-File $log -Append -Encoding ASCII
if (-not $isAdmin) {
    "ERROR:not_admin" | Out-File $log -Append -Encoding ASCII
    "=== DONE ===" | Out-File $log -Append -Encoding ASCII
    exit 2
}
$text = (pnputil /enum-drivers) -join "`n"
$blocks = $text -split "`n`n"
$oem = @()
foreach ($b in $blocks) {
    if ($b -match "vigembus\.inf") {
        $m = [regex]::Match($b, "oem\d+\.inf", "IgnoreCase")
        if ($m.Success) { $oem += $m.Value }
    }
}
$oem = $oem | Sort-Object -Unique
"Packages: $($oem -join ', ')" | Out-File $log -Append -Encoding ASCII
foreach ($inf in $oem) {
    "Deleting package $inf" | Out-File $log -Append -Encoding ASCII
    (pnputil /delete-driver $inf /uninstall /force) 2>&1 | Out-File $log -Append -Encoding ASCII
    "ExitCode: $LASTEXITCODE" | Out-File $log -Append -Encoding ASCII
}
(sc.exe stop ViGEmBus) 2>&1 | Out-File $log -Append -Encoding ASCII
(sc.exe delete ViGEmBus) 2>&1 | Out-File $log -Append -Encoding ASCII
$sys = Join-Path $env:SystemRoot "System32\drivers\ViGEmBus.sys"
if (Test-Path $sys) {
    Remove-Item $sys -Force
    if (Test-Path $sys) {
        "sys file still present (will be freed after reboot)" | Out-File $log -Append -Encoding ASCII
    } else {
        "sys file deleted" | Out-File $log -Append -Encoding ASCII
    }
} else {
    "sys file absent" | Out-File $log -Append -Encoding ASCII
}
"=== DONE ===" | Out-File $log -Append -Encoding ASCII
'''
    script = script.replace("%LOG%", log_path.replace("'", "''"))
    try:
        with open(script_path, "w", encoding="utf-8-sig") as f:
            f.write(script)
    except OSError as exc:
        return {"ok": False,
                "message": "temizlik betiği yazılamadı: %s" % exc,
                "log": ""}

    ok, msg = run_installer_elevated(
        "powershell.exe",
        params=('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden '
                '-File "%s"' % script_path),
        timeout=timeout)
    log_text = ""
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            log_text = f.read()
    except OSError:
        pass
    done = "=== DONE ===" in log_text
    not_admin = "ERROR:not_admin" in log_text
    if not (ok and done and not not_admin):
        if not ok:
            message = msg
        elif not_admin:
            message = "yönetici izni verilmedi"
        elif not done:
            message = "temizlik betiği tamamlanamadı"
        else:
            message = "bilinmeyen hata"
        return {"ok": False, "message": message, "log": log_text}
    return {"ok": True, "message": "temizlik tamamlandı", "log": log_text}


def vigem_setup_info(timeout=15):
    """ViGEmBus son sürümünün indirme adresi ve sürüm etiketi."""
    import json as _json
    import urllib.request as _ur
    req = _ur.Request(VIGEM_API_LATEST, headers={"User-Agent": "GamepadFixer"})
    with _ur.urlopen(req, timeout=timeout) as resp:
        data = _json.loads(resp.read().decode("utf-8"))
    url = None
    for asset in data.get("assets", []):
        name = (asset.get("name") or "").lower()
        if name.endswith(".exe"):
            url = asset.get("browser_download_url")
            break
    return {"url": url,
            "version": str(data.get("tag_name") or "").lstrip("vV")}


def vigem_setup_url(timeout=15):
    """ViGEmBus son surum kurulum (.exe) indirme adresini dondurur."""
    return vigem_setup_info(timeout).get("url")


def download_file(url, dest, timeout=60, progress=None):
    """url adresini dest dosyasina indirir; progress(oran 0..1) cagrilir."""
    import urllib.request as _ur
    req = _ur.Request(url, headers={"User-Agent": "GamepadFixer"})
    with _ur.urlopen(req, timeout=timeout) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        got = 0
        last = -1
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if progress is not None and total > 0:
                    pct = int(got * 100.0 / total)
                    if pct != last:
                        last = pct
                        progress(got / float(total))
    return dest


class _ShellExecuteInfoW(ctypes.Structure):
    _fields_ = (
        ("cbSize", ctypes.c_uint32),
        ("fMask", ctypes.c_uint32),
        ("hwnd", ctypes.c_void_p),
        ("lpVerb", ctypes.c_wchar_p),
        ("lpFile", ctypes.c_wchar_p),
        ("lpParameters", ctypes.c_wchar_p),
        ("lpDirectory", ctypes.c_wchar_p),
        ("nShow", ctypes.c_int),
        ("hInstApp", ctypes.c_void_p),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", ctypes.c_wchar_p),
        ("hkeyClass", ctypes.c_void_p),
        ("dwHotKey", ctypes.c_uint32),
        ("hIcon", ctypes.c_void_p),
        ("hProcess", ctypes.c_void_p),
    )


def run_installer_elevated(path, params=None, timeout=900):
    """Kurulum dosyasini yonetici izniyle calistirir, bitmesini bekler.
    (True, aciklama) ya da (False, hata mesaji) dondurur."""
    SEE_MASK_NOCLOSEPROCESS = 0x00000040
    sei = _ShellExecuteInfoW()
    sei.cbSize = ctypes.sizeof(sei)
    sei.fMask = SEE_MASK_NOCLOSEPROCESS
    sei.lpVerb = "runas"
    sei.lpFile = str(path)
    sei.lpParameters = params or None
    sei.nShow = 1
    shell32 = ctypes.windll.shell32
    shell32.ShellExecuteExW.restype = ctypes.c_int
    if not shell32.ShellExecuteExW(ctypes.byref(sei)):
        err = ctypes.windll.kernel32.GetLastError()
        if err == 1223:
            return (False, "yonetici izni verilmedi (UAC iptal)")
        return (False, "kurulum baslatilamadi (kod %d)" % err)
    h = sei.hProcess
    if not h:
        return (False, "kurulum sureci izlenemedi")
    try:
        res = ctypes.windll.kernel32.WaitForSingleObject(h,
                                                          int(timeout * 1000))
    finally:
        ctypes.windll.kernel32.CloseHandle(h)
    if res == 0:
        return (True, "kurulum tamamlandi")
    return (True, "kurulum zaman asimi (pencere hala acik olabilir)")
