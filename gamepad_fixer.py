#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
gamepad_fixer.py - Gamepad'i SİSTEM DÜZEYİNDE düzelten sanal kol köprüsü
=========================================================================

Ne yapar?
---------
1) Fiziksel XInput kolunu okur (Windows XInput katmanı - oyunların kullandığı
   katman). Not: Bazı kollar (ör. Bluetooth Xbox kolu, ürün 02e0) pygame/SDL
   üzerinden BOŞ veri gösterirken XInput üzerinden sorunsuz canlı veri verir.
   Bu yüzden girdi artık doğrudan XInput'tan okunur (pygame gerekmez).
2) gamepad_stick.StickFilter ile düzeltir:
     - merkez kayması (drift/bias) temizliği  (ör. -0.152 -> 0)
     - kare kapı -> daire düzeltmesi          (Avg Error %21 -> ~%0)
     - dairesel ölü bölge + yumuşatma + snap
3) Düzeltilmiş veriyi bir SANAL Xbox 360 kolu (ViGEmBus, ürün 028e) olarak
   sisteme sunar. Test siteleri ve oyunlar DÜZELTİLMİŞ değerleri görür -
   fiziksel kola hiç dokunmadan.
4) Titreşim iletimi: Oyun/site sanal kola titreşim gönderdiğinde bu titreşim
   fiziksel Bluetooth koluna aktarılır (XInput + doğrudan BT HID paketi;
   çünkü Windows, Bluetooth'da XInput titreşimini desteklemeyebiliyor).

Gereksinimler (kurulu olmalı):
  - ViGEmBus sürücüsü ve servisi (ViGEmBus) çalışıyor.
  - pip install vgamepad
  - pip install hidapi   (Bluetooth titreşim aktarımı için; opsiyonel)
  (pygame gerekmez)

Kullanım:
    python gamepad_fixer.py                     # varsayılanlar
    python gamepad_fixer.py --deadzone 0.10 --smooth 0.02
    python gamepad_fixer.py --slot 0            # giriş yuvasını elle seç
    python gamepad_fixer.py --duration 10       # 10 sn sonra otomatik kapan
    python gamepad_fixer.py --no-rumble         # titreşim iletimini kapat
    python gamepad_fixer.py --rumble-scale 130  # titreşimi %130 güçlendir

Yuva seçimi:
  - Oluşturduğumuz sanal kol otomatik tespit edilip hariç tutulur.
  - Kalan bağlı yuvalardan otomatik seçilir; --slot 0..3 ile elle verilebilir.

Önemli notlar:
  - Başlarken kolu SERBEST BIRAK: merkez kayması otomatik ölçülüp silinir.
  - Kol uykuya geçerse sinyal kesilir; ortadaki Xbox/Guide tuşuna basıp
    uyandır; düzeltici kendiliğinden devam eder.
  - Çalışırken bu pencereyi AÇIK TUT; kapatınca sanal kol kaybolur.
  - Test sitesinde/oyunda SANAL kolu seç (Ürün 028e; fiziksel 02e0'tır).
  - Çıkış: Ctrl+C
"""

import argparse
import ctypes
import math
import os
import sys
import tempfile
import time

import vgamepad as vg

try:
    import hid as _hid
except ImportError:
    _hid = None

from gamepad_stick import StickFilter

# ----------------------------------------------------------------------
# XInput katmanı
# ----------------------------------------------------------------------

try:
    _xinput = ctypes.windll.xinput1_4
except OSError:
    _xinput = ctypes.windll.xinput1_3


class XINPUT_GAMEPAD(ctypes.Structure):
    _fields_ = [
        ("wButtons", ctypes.c_ushort),
        ("bLeftTrigger", ctypes.c_ubyte),
        ("bRightTrigger", ctypes.c_ubyte),
        ("sThumbLX", ctypes.c_short),
        ("sThumbLY", ctypes.c_short),
        ("sThumbRX", ctypes.c_short),
        ("sThumbRY", ctypes.c_short),
    ]


class XINPUT_STATE(ctypes.Structure):
    _fields_ = [
        ("dwPacketNumber", ctypes.c_ulong),
        ("Gamepad", XINPUT_GAMEPAD),
    ]


class XINPUT_BATTERY_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BatteryType", ctypes.c_ubyte),
        ("BatteryLevel", ctypes.c_ubyte),
    ]


class XINPUT_VIBRATION(ctypes.Structure):
    _fields_ = [
        ("wLeftMotorSpeed", ctypes.c_ushort),
        ("wRightMotorSpeed", ctypes.c_ushort),
    ]


BATT_TYPE_NAMES = {0: "baglanti yok", 1: "kablolu", 2: "alkalin", 3: "sarjli"}
BATT_LEVEL_NAMES = {0: "bos", 1: "az", 2: "orta", 3: "dolu"}


def xinput_read(slot):
    """Yuvayi okur; bagli degilse None doner."""
    st = XINPUT_STATE()
    if _xinput.XInputGetState(slot, ctypes.byref(st)) != 0:
        return None
    return st


def connected_slots():
    """Bagli XInput yuvalarinin kumesi."""
    out = set()
    for s in range(4):
        if xinput_read(s) is not None:
            out.add(s)
    return out


def battery_info(slot):
    b = XINPUT_BATTERY_INFORMATION()
    _xinput.XInputGetBatteryInformation(slot, 0, ctypes.byref(b))
    return b.BatteryType, b.BatteryLevel


def xinput_set_vibration(slot, large, small):
    """Fiziksel kola titresim gonderir (0-255, 0-255). True = kabul edildi."""
    large = max(0, min(255, int(large)))
    small = max(0, min(255, int(small)))
    vib = XINPUT_VIBRATION(large * 257, small * 257)
    return _xinput.XInputSetState(slot, ctypes.byref(vib)) == 0


# ----------------------------------------------------------------------
# Bluetooth HID titreşim yolu (xpadneo paketi)
# ----------------------------------------------------------------------
# Windows, Bluetooth bagli Xbox kolunda XInput titreşimini desteklemeyebilir.
# Bu yol, Linux'taki xpadneo sürücüsünün kullandığı HID çıkış raporunu
# doğrudan kola yollar. Paket 9 byte:
#   03 0F 00 00 <guclu> <zayif> FF 00 EB
#   report=0x03, enable=0x0F, sol/sag(main)=0, guclu, zayif,
#   pulse_sustain=0xFF, pulse_release=0x00, loop=0xEB

HID_VID, HID_PID = 0x045E, 0x02E0
RUMBLE_REPORT_ID = 0x03

# Aktif titresimde tekrar gonderim araligi (saniye). BT bagli kolda motor
# kisa sure sonra sonuyor (sustain bayragina ragmen); 1.0 sn cok uzundu:
# her tazeleme yeni bir kisa vizilti gibi duyuluyor, kullanici "1-2
# saniyede bir kesiliyor" diyordu (site kalici modda 900 ms'de bir
# gonderiyor, sanal taraf kesintisizdi). 0.25 sn'de bir tazeliyoruz;
# motor sonmeye firsat bulamiyor (xpadneo benzeri davranis).
RUMBLE_REFRESH = 0.25

# Sanal koldan (0,0)'dan farkli bir deger geldikten sonra bu sure boyunca
# gelen "dur" sinyalleri yok sayilir (saniye). XInput/ViGEm katmani bazen
# nonzero bir degerin hemen ardindan hayalet (0,0) bildirimi yolluyor;
# boylece kalici titreşim 50 ms icinde kesilmiyor. Gercek durusta gelen
# deger sifir kalir ve pencere dolunca "dur" fiziksel kola iletilir.
RUMBLE_ZERO_GUARD = 0.5


class HidRumbler:
    """Bluetooth kolu için HID çıkış raporuyla titreşim gönderir."""

    def __init__(self):
        self.dev = None
        self.path = None
        self._last_open = 0.0

    def _find(self):
        if _hid is None:
            return None
        for d in _hid.enumerate(HID_VID, HID_PID):
            if d.get("usage_page") == 0x01 and d.get("usage") == 0x05:
                return d["path"]
        return None

    def open(self):
        """Kanali acar (saniyede en fazla 1 deneme)."""
        if _hid is None:
            return False
        if self.dev is not None:
            return True
        now = time.time()
        if now - self._last_open < 1.0:
            return False
        self._last_open = now
        try:
            path = self._find()
            if not path:
                return False
            dev = _hid.device()
            dev.open_path(path)
            self.dev = dev
            self.path = path
            return True
        except Exception:
            self.dev = None
            return False

    def send(self, large, small):
        """0-255 degerleri kola yazar. Basarisizsa kanali kapatip yeniden dener."""
        if not self.open():
            return False
        large = max(0, min(255, int(large)))
        small = max(0, min(255, int(small)))
        buf = [RUMBLE_REPORT_ID, 0x0F, 0x00, 0x00, large, small, 0xFF, 0x00, 0xEB]
        try:
            n = self.dev.write(buf)
            if n is None or n < 0:
                raise OSError("write -> %s" % n)
            return True
        except Exception:
            try:
                self.dev.close()
            except Exception:
                pass
            self.dev = None
            return False

    def close(self):
        if self.dev is not None:
            try:
                self.dev.close()
            except Exception:
                pass
            self.dev = None


# --- Pil okuma (BT kol HID giris raporundan; xpadneo bayrak formati) -----
# Bayt: bit7 online | bit4 sarj | bit3-2 mod (0=USB, 1=batarya, 2=Play'n Charge)
#       | bit1-0 seviye (0=bitmek uzere, 1=normal, 2=yuksek, 3=tam)
BATT_FLAG_ONLINE = 0x80
BATT_FLAG_CHARGING = 0x10


def hid_battery_read(timeout=0.9):
    """Bluetooth kolun HID girisinden pil durumunu okumayi dener.

    Doner: (bilgi sozlugu veya None, ham bayt listesi veya None).
    Sozluk: online, charging, mode, level, value."""
    if _hid is None:
        return None, None
    dev = None
    try:
        path = None
        for d in _hid.enumerate(HID_VID, HID_PID):
            if d.get("usage_page") == 0x01 and d.get("usage") == 0x05:
                path = d["path"]
                break
        if not path:
            return None, None
        dev = _hid.device()
        dev.open_path(path)
        t_end = time.time() + max(0.2, float(timeout))
        while time.time() < t_end:
            data = dev.read(64, timeout=100)
            if not data:
                continue
            rid = int(data[0])
            val = int(data[1]) if len(data) >= 2 else 0
            # Pil raporu: 2 baytlik (rid + bayrak bayti) ya da bilinen id'ler
            if len(data) >= 2 and (rid in (0x02, 0x04)
                                   or (len(data) == 2
                                       and (val & BATT_FLAG_ONLINE))):
                info = {"online": bool(val & BATT_FLAG_ONLINE),
                        "charging": bool(val & BATT_FLAG_CHARGING),
                        "mode": (val >> 2) & 0x03,
                        "level": val & 0x03,
                        "value": val}
                return info, [int(b) for b in data[:16]]
        return None, None
    except Exception:
        return None, None
    finally:
        if dev is not None:
            try:
                dev.close()
            except Exception:
                pass


def norm_axis(v):
    x = v / 32767.0
    if x > 1.0:
        return 1.0
    if x < -1.0:
        return -1.0
    return x


# XInput buton bitleri -> vgamepad butonlari
BUTTON_BITS = (
    (0x1000, vg.XUSB_BUTTON.XUSB_GAMEPAD_A),
    (0x2000, vg.XUSB_BUTTON.XUSB_GAMEPAD_B),
    (0x4000, vg.XUSB_BUTTON.XUSB_GAMEPAD_X),
    (0x8000, vg.XUSB_BUTTON.XUSB_GAMEPAD_Y),
    (0x0100, vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER),
    (0x0200, vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER),
    (0x0020, vg.XUSB_BUTTON.XUSB_GAMEPAD_BACK),
    (0x0010, vg.XUSB_BUTTON.XUSB_GAMEPAD_START),
    (0x0400, vg.XUSB_BUTTON.XUSB_GAMEPAD_GUIDE),
    (0x0040, vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_THUMB),
    (0x0080, vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_THUMB),
    (0x0001, vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP),
    (0x0002, vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN),
    (0x0004, vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT),
    (0x0008, vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT),
)


def make_filters(args):
    return (
        StickFilter(deadzone=args.deadzone, circularity=args.circularity,
                    crosstalk=args.crosstalk, smooth_time=args.smooth),
        StickFilter(deadzone=args.deadzone, circularity=args.circularity,
                    crosstalk=args.crosstalk, smooth_time=args.smooth),
    )


def start_gamepad():
    """Sanal Xbox 360 kolunu olusturur; ViGEmBus sorunlarini anlasilir raporlar."""
    try:
        g = vg.VX360Gamepad()
    except Exception as ex:
        print("HATA: Sanal kol olusturulamadi.")
        print("ViGEmBus surucusu calisiyor mu? Yonetici PowerShell'de:")
        print("    Start-Service ViGEmBus")
        print("Ayrinti: %s" % ex)
        sys.exit(1)
    # Varsayilan baslangic durumunu hemen temizle (notr)
    g.reset()
    g.update()
    return g


def detect_our_slot(before):
    """Sanal kol olustuktan sonra yeni beliren yuvayi bulur (birkac deneme)."""
    for _ in range(10):
        new = connected_slots() - before
        if new:
            return new
        time.sleep(0.1)
    return set()


def pick_input_slot(exclude):
    """Haric tutulanlar disinda kalan ilk bagli yuvayi dondurur."""
    for s in range(4):
        if s in exclude:
            continue
        if xinput_read(s) is not None:
            return s
    return None


def wait_input_slot(exclude, deadline=None, stop_event=None):
    """Fiziksel kol (haric tutulmayan bir yuva) gelene kadar bekler."""
    warned = False
    while True:
        slot = pick_input_slot(exclude)
        if slot is not None:
            return slot
        if deadline is not None and time.perf_counter() >= deadline:
            return None
        if stop_event is not None and stop_event.is_set():
            return None
        if not warned:
            print("Fiziksel kol bekleniyor... kolu ac (Xbox dugmesi) / bagla")
            warned = True
        time.sleep(1.5)


def describe_slots():
    """Bagli yuvalarin kisa durum dokumu (baslangic logu icin)."""
    rows = []
    for s in range(4):
        st = xinput_read(s)
        if st is None:
            continue
        bt, bl = battery_info(s)
        g = st.Gamepad
        rows.append("yuva%d LX=%+.3f LY=%+.3f pil=%s/%s" % (
            s, norm_axis(g.sThumbLX), norm_axis(g.sThumbLY),
            BATT_TYPE_NAMES.get(bt, bt), BATT_LEVEL_NAMES.get(bl, bl)))
    return rows


def capture_bias(slot, seconds=1.3):
    """Cubuklar serbestken merkez kaymasini (bias) olcer. None = olculemedi."""
    left, right = [], []
    t_end = time.time() + seconds
    while time.time() < t_end:
        st = xinput_read(slot)
        if st is None:
            break
        g = st.Gamepad
        lx, ly = norm_axis(g.sThumbLX), norm_axis(g.sThumbLY)
        rx, ry = norm_axis(g.sThumbRX), norm_axis(g.sThumbRY)
        if math.hypot(lx, ly) < 0.30:
            left.append((lx, ly))
        if math.hypot(rx, ry) < 0.30:
            right.append((rx, ry))
        time.sleep(0.016)

    def avg(s):
        if len(s) < 10:
            return None
        return (sum(a for a, _ in s) / len(s), sum(b for _, b in s) / len(s))

    return avg(left), avg(right)


def calibrate(slot, args):
    """Filtreleri kurar ve bias kalibrasyonunu yapar."""
    f_l, f_r = make_filters(args)
    bias_l, bias_r = capture_bias(slot)
    if bias_l is not None:
        f_l.bias_x, f_l.bias_y = bias_l
        print("  Sol cubuk merkez kaymasi: (%+.3f, %+.3f) -> temizlendi" % bias_l)
    else:
        print("  Sol cubuk kalibre edilemedi (cubuk serbest degildi?)")
    if bias_r is not None:
        f_r.bias_x, f_r.bias_y = bias_r
        print("  Sag cubuk merkez kaymasi: (%+.3f, %+.3f) -> temizlendi" % bias_r)
    else:
        print("  Sag cubuk kalibre edilemedi")
    return f_l, f_r


def _cleanup(gamepad, pid_path):
    """Sanal kolu notre al, bildirimi coz ve PID dosyasini sil."""
    try:
        if hasattr(gamepad, "unregister_notification"):
            gamepad.unregister_notification()
    except Exception:
        pass
    try:
        gamepad.reset()
        gamepad.update()
        time.sleep(0.05)
    except Exception:
        pass
    if pid_path:
        try:
            os.remove(pid_path)
        except OSError:
            pass


def main(argv=None, stop_event=None, live=None):
    """argv: arguman listesi (None = komut satiri); stop_event: durdurma bayragi;
    live: paylasilan ayar sozlugu (ornek: rumble_scale) - calisirken canli okunur."""
    ap = argparse.ArgumentParser(
        description="Fiziksel XInput kolunu duzeltip sanal Xbox 360 kolu olarak sunar.")
    ap.add_argument("--slot", type=int, default=-1,
                    help="giris yuvasi 0-3 (-1 = otomatik)")
    ap.add_argument("--deadzone", type=float, default=0.12,
                    help="dairesel olu bolge yaricapi (varsayilan 0.12)")
    ap.add_argument("--circularity", type=float, default=1.0,
                    help="kare kapi -> daire duzeltmesi, 0..1 (Xbox icin 1.0)")
    ap.add_argument("--crosstalk", type=float, default=0.0,
                    help="eksen sizinti orani (varsayilan 0 = kapali)")
    ap.add_argument("--smooth", type=float, default=0.03,
                    help="yumusatma zaman sabiti, saniye (0 = kapali)")
    ap.add_argument("--rate", type=int, default=120,
                    help="guncelleme hizi, Hz (varsayilan 120)")
    ap.add_argument("--duration", type=float, default=0.0,
                    help="bu sure sonunda otomatik kapan (0 = sonsuz)")
    ap.add_argument("--invert-y", action="store_true",
                    help="dikey ekseni ters cevir (site ters gosteriyorsa)")
    ap.add_argument("--no-rumble", action="store_true",
                    help="titresim aktarimini kapat (varsayilan: acik)")
    ap.add_argument("--rumble-scale", type=float, default=100.0,
                    help="titresim siddeti yuzdesi 0-200 (varsayilan 100)")
    args = ap.parse_args(argv)
    args.rumble_scale = max(0.0, min(200.0, args.rumble_scale))

    def _stopped():
        return stop_event is not None and stop_event.is_set()

    pid_path = os.path.join(tempfile.gettempdir(), "gamepad_fixer.pid")
    try:
        with open(pid_path, "w") as f:
            f.write(str(os.getpid()))
    except OSError:
        pid_path = None

    t_start = time.perf_counter()
    deadline = (t_start + args.duration) if args.duration else None

    print("=" * 62)
    print("gamepad_fixer - sistem duzeyinde gamepad duzeltici (XInput -> ViGEm)")
    print("=" * 62)
    print("PID: %d" % os.getpid())

    slots_before = connected_slots()
    for row in describe_slots():
        print("  mevcut %s" % row)

    print("Sanal kol olusturuluyor...")
    gamepad = start_gamepad()

    # --- Titresim iletimi kurulumu ---
    rumble = {"large": 0, "small": 0, "last_log": 0.0, "last_arm": 0.0,
              "evt": 0}
    hid_rumble = None
    if not args.no_rumble:
        def _on_rumble(client, target, large_motor, small_motor, led_number, user_data):
            rumble["evt"] += 1
            large_motor = int(large_motor)
            small_motor = int(small_motor)
            try:
                pct = float((live or {}).get("rumble_scale", args.rumble_scale))
            except (TypeError, ValueError):
                pct = args.rumble_scale
            pct = max(0.0, min(200.0, pct))
            if pct != 100.0:
                large_motor = max(0, min(255, int(round(large_motor * pct / 100.0))))
                small_motor = max(0, min(255, int(round(small_motor * pct / 100.0))))
            if (large_motor, small_motor) != (rumble["large"], rumble["small"]):
                # baslangic/bitis olaylarini her zaman, ara degisimleri throttle'li logla
                now_cb = time.time()
                zero_event = (large_motor == 0 and small_motor == 0) or (
                    rumble["large"] == 0 and rumble["small"] == 0)
                if zero_event or now_cb - rumble["last_log"] >= 0.3:
                    rumble["last_log"] = now_cb
                    print("[%s] [Titresim] Sanal koldan gelen: guclu=%d zayif=%d"
                          % (time.strftime("%H:%M:%S"), large_motor, small_motor))
            elif (large_motor or small_motor) and (
                    time.time() - rumble["last_arm"] >= 2.0):
                # Ayni deger yeniden geldi (site kalici modda ~900 ms'de bir
                # ayni degeri yollayabiliyor); teshis icin 2 sn'de bir logla.
                rumble["last_arm"] = time.time()
                print("[%s] [Titresim] Ayni deger yeniden gonderildi: "
                      "guclu=%d zayif=%d"
                      % (time.strftime("%H:%M:%S"), large_motor, small_motor))
            rumble["large"] = large_motor
            rumble["small"] = small_motor

        try:
            gamepad.register_notification(_on_rumble)
            hid_rumble = HidRumbler()
            if hid_rumble.open():
                print("Titresim: XInput + Bluetooth HID aktarimi hazir.")
            else:
                print("Titresim: XInput aktarimi (BT HID kanali kol baglaninca denenecek).")
        except Exception as ex:
            hid_rumble = None
            print("UYARI: titresim bildirimi kurulamadi: %s" % ex)
    else:
        print("Titresim aktarimi: KAPALI (--no-rumble)")

    our_slots = detect_our_slot(slots_before)
    if our_slots:
        print("Sanal kol yuvasi: %s (okuma listesinden cikarildi)"
              % ", ".join(str(s) for s in sorted(our_slots)))
    else:
        print("UYARI: sanal kolun yuvasi tespit edilemedi; yine de devam.")

    exclude = set(our_slots)

    if args.slot >= 0:
        in_slot = args.slot
        if xinput_read(in_slot) is None:
            print("--slot %d su an bagli degil, bekleniyor..." % in_slot)
            while xinput_read(in_slot) is None:
                if deadline is not None and time.perf_counter() >= deadline:
                    print("Sure doldu, cikiliyor.")
                    _cleanup(gamepad, pid_path)
                    return
                if _stopped():
                    print("Durdurma istegi alindi.")
                    _cleanup(gamepad, pid_path)
                    return
                time.sleep(1.5)
    else:
        in_slot = wait_input_slot(exclude, deadline, stop_event)
        if in_slot is None:
            if _stopped():
                print("Durdurma istegi alindi.")
            else:
                print("Sure doldu, cikiliyor.")
            _cleanup(gamepad, pid_path)
            return

    bt, bl = battery_info(in_slot)
    print("Fiziksel kol: yuva %d  (pil: %s/%s)"
          % (in_slot, BATT_TYPE_NAMES.get(bt, bt), BATT_LEVEL_NAMES.get(bl, bl)))
    if our_slots:
        if min(our_slots) == 0:
            print("  (Sanal kol 1. sirada: oyunlar/site ilk kol olarak bunu gorur)")
        else:
            print("  IPUCU: 'Oyunlarin ilk kolu SANAL olsun' istersen: kolu KAPAT,")
            print("  duzelticiyi yeniden baslat; 'Fiziksel kol bekleniyor...'")
            print("  yazinca kolu AC. Boylece sanal kol 1. siraya gecer.")
    print("Merkez kalibrasyonu icin kolu SERBEST birak (1.3 sn)...")
    f_l, f_r = calibrate(in_slot, args)

    print("Ayar: deadzone=%.2f circularity=%.2f crosstalk=%.2f smooth=%.3f sn titresim=%s"
          % (args.deadzone, args.circularity, args.crosstalk, args.smooth,
             "kapali" if args.no_rumble else "acik"))
    print()
    print(">>> SANAL KOL AKTIF. Bu pencereyi acik tut; cikis: Ctrl+C")
    print(">>> Test sitesinde/oyunda URUN 028e olan kolu sec (fiziksel = 02e0)")
    print()

    frame = 1.0 / max(args.rate, 1)
    last_t = time.perf_counter()
    last_log = last_t
    rumble_applied = (0, 0)
    rumble_slot = None
    rumble_t = 0.0
    rumble_warn_t = 0.0
    rum_wiggle = False
    rum_refresh_n = 0
    rum_hold = (0, 0)
    rum_hold_t = 0.0
    rum_hold_log_t = 0.0

    try:
        while True:
            if _stopped():
                print("Durdurma istegi alindi...")
                break
            loop_end = time.perf_counter() + frame
            now = time.perf_counter()
            dt = max(now - last_t, 1e-4)
            last_t = now

            st = xinput_read(in_slot)
            if st is None:
                # kol ayrildi: sanal kolu notre al, yeni kol bekle
                print("Fiziksel kol ayrildi - yeniden bekleniyor...")
                gamepad.reset()
                gamepad.update()
                got = wait_input_slot(exclude, deadline, stop_event)
                if got is None:
                    if _stopped():
                        print("Durdurma istegi alindi.")
                    else:
                        print("Sure doldu, cikiliyor.")
                    break
                in_slot = got
                bt, bl = battery_info(in_slot)
                print("Yeniden baglandi: yuva %d (pil: %s/%s)"
                      % (in_slot, BATT_TYPE_NAMES.get(bt, bt),
                         BATT_LEVEL_NAMES.get(bl, bl)))
                print("Merkez kalibrasyonu icin kolu SERBEST birak (1.3 sn)...")
                f_l, f_r = calibrate(in_slot, args)
                continue

            g = st.Gamepad
            raw_lx = norm_axis(g.sThumbLX)
            raw_ly = norm_axis(g.sThumbLY)
            raw_rx = norm_axis(g.sThumbRX)
            raw_ry = norm_axis(g.sThumbRY)

            lx, ly = f_l.process(raw_lx, raw_ly, dt)
            rx, ry = f_r.process(raw_rx, raw_ry, dt)

            if args.invert_y:
                ly = -ly
                ry = -ry

            # XInput: y yukari pozitif; vgamepad: y yukari pozitif -> ceviri yok.
            gamepad.left_joystick_float(lx, ly)
            gamepad.right_joystick_float(rx, ry)

            gamepad.left_trigger_float(g.bLeftTrigger / 255.0)
            gamepad.right_trigger_float(g.bRightTrigger / 255.0)

            for bit, btn in BUTTON_BITS:
                if g.wButtons & bit:
                    gamepad.press_button(button=btn)
                else:
                    gamepad.release_button(button=btn)

            gamepad.update()

            now = time.perf_counter()

            # --- Titresim iletimi: sanal kola geleni fiziksel kola aktar ---
            if not args.no_rumble:
                rum_gelen = (rumble["large"], rumble["small"])
                # Kisa "dur" sinyallerini yok say (bkz. RUMBLE_ZERO_GUARD)
                if rum_gelen != (0, 0):
                    rum_hold = rum_gelen
                    rum_hold_t = now
                    rum_eff = rum_gelen
                elif now - rum_hold_t < RUMBLE_ZERO_GUARD:
                    rum_eff = rum_hold
                    if now - rum_hold_log_t >= 0.5:
                        rum_hold_log_t = now
                        print("[Titresim] Kisa 'dur' sinyali yok sayildi "
                              "(gelen 0/0, %.1f sn bekleme)" % RUMBLE_ZERO_GUARD)
                else:
                    rum_eff = (0, 0)
                changed = (rum_eff != rumble_applied or in_slot != rumble_slot)
                # aktif titresimde RUMBLE_REFRESH sn'de bir tazele
                refresh = (rum_eff != (0, 0) and now - rumble_t >= RUMBLE_REFRESH)
                if (changed and now - rumble_t >= 0.04) or refresh:
                    l_send, s_send = rum_eff
                    if not changed:
                        # Tazelemede kucuk salinim (255 <-> 254): bazi XInput
                        # suruculeri "deger degismedi" diye cihaza yeniden
                        # iletmeyebilir; 1/255 fark motorlarda fark edilmez.
                        rum_wiggle = not rum_wiggle
                        if rum_wiggle:
                            l_send = max(0, l_send - 1)
                            s_send = max(0, s_send - 1)
                    ok_x = xinput_set_vibration(in_slot, l_send, s_send)
                    ok_h = True
                    if hid_rumble is not None:
                        ok_h = hid_rumble.send(rum_eff[0], rum_eff[1])
                    if not ok_x or not ok_h:
                        if now - rumble_warn_t >= 1.0:
                            rumble_warn_t = now
                            print("[Titresim] UYARI: iletime basarisiz "
                                  "(XInput=%s, BT HID=%s)" % (ok_x, ok_h))
                    if changed:
                        rum_refresh_n = 0
                    elif refresh:
                        rum_refresh_n += 1
                        if rum_refresh_n % 8 == 0:  # ~2 sn'de bir (8 x 0.25)
                            print("[%s] [Titresim] aktif tutuluyor "
                                  "(tazeleme #%d guclu=%d zayif=%d)"
                                  % (time.strftime("%H:%M:%S"),
                                     rum_refresh_n, rum_eff[0], rum_eff[1]))
                    rumble_applied = rum_eff
                    rumble_slot = in_slot
                    rumble_t = now

            if now - last_log >= 2.0:
                last_log = now
                print("[%s] L ham=(%+.3f,%+.3f) -> duz=(%+.3f,%+.3f) | "
                      "R ham=(%+.3f,%+.3f) -> duz=(%+.3f,%+.3f) | "
                      "titr ham=(%d,%d) olay=%d"
                      % (time.strftime("%H:%M:%S"),
                         raw_lx, raw_ly, lx, ly, raw_rx, raw_ry, rx, ry,
                         rumble["large"], rumble["small"], rumble["evt"]))

            if deadline is not None and now >= deadline:
                print("Sure doldu, duzgun kapaniyor...")
                break

            slp = loop_end - time.perf_counter()
            if slp > 0:
                time.sleep(slp)

    except KeyboardInterrupt:
        print()
        print("Cikis istegi alindi...")
    finally:
        if not args.no_rumble and in_slot is not None:
            try:
                xinput_set_vibration(in_slot, 0, 0)
            except Exception:
                pass
            if hid_rumble is not None:
                hid_rumble.send(0, 0)
                hid_rumble.close()
        _cleanup(gamepad, pid_path)
        print("Sanal kol kapatildi. Gorusuruz!")


if __name__ == "__main__":
    main()
