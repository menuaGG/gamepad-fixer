#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
gamepad_stick.py - Gamepad analog çubuk (stick) filtre hattı
=============================================================

Ne yapar?
---------
1) KARE kapılı çubukları (ör. Xbox 360 / 045e:02e0) DAİREYE çevirir:
   bu çubuklar diyagonalde eksenlerden çok daha uzağa ulaşır (tam itişte
   eksende |v| ~ 1.00, diyagonalde ~ 1.41). Daire çizildiğinde çıkış
   yarıçapı %21'e kadar dalgalanır; hardwaretester.com bunu "Avg Error
   ~%20.9" gibi raporlar (kare kapının teorik değeri %20.6'dır).
   circularize() yönü koruyup radyal dalgalanmayı düzeltir.
2) KARE (square / eksen bazlı) ölü bölge yerine DAİRESEL (radial) ölü
   bölge uygular; ölü bölge kenarını 0 kabul edip çıktıyı 1'e yeniden
   ölçekler. Böylece kare ölü bölgedeki "köşe kırılması", ani yön
   sıçramaları ve ölü bölge çevresindeki kesik bölgeler oluşmaz.
3) İsteğe bağlı EKSEN SAPMASI (cross-talk) temizliği: bir eksene itince
   diğer eksene sızan değer yumuşak ağırlıkla çıkarılır (bazı adaptörlerde
   / aşınmış çubuklarda görülür; sihirbazla ölçülür).
4) Çerçeve hızından bağımsız yumuşatma (exponential smoothing).
5) Sonuç: düzgün, sürekli, yön-koruyan daire çıktısı.

Hattın sırası (StickFilter.process):
  bias düzeltmesi -> eksen sapması temizliği -> kare kapı -> daire
  düzeltmesi (circularity) -> dairesel ölü bölge + yeniden ölçekleme ->
  (isteğe bağlı) eğri/expo -> birim daireye kırpma -> yumuşatma -> snap

Xbox 360 (045e:02e0) için önerilen ayar:
  StickFilter(deadzone=0.15, circularity=1.0)   # site ölçümü ~%21 -> ~%0

Pygame bağımlılığı YOKTUR (saf matematik). Kendi kendini test etmek için:

    python gamepad_stick.py

Canlı, görsel gamepad testi için: gamepad_stick_test.py

Oyun döngüsüne entegrasyon örneği (pygame):
-------------------------------------------
    from gamepad_stick import StickFilter

    filt = StickFilter(deadzone=0.15, circularity=1.0, smooth_time=0.06)

    # ilk açılışta merkez kalibrasyonu (çubuk serbestken):
    import pygame
    pygame.init(); pygame.joystick.init()
    joy = pygame.joystick.Joystick(0); joy.init()
    filt.calibrate([(joy.get_axis(0), joy.get_axis(1)) for _ in range(40)])

    # döngü içinde:
    dt = clock.tick(60) / 1000.0
    x, y = filt.process(joy.get_axis(0), joy.get_axis(1), dt)
    # not: pygame'de y aşağı pozitiftir; ileri için y = -y çevirebilirsiniz.
"""

import math

__all__ = [
    "StickFilter",
    "circular_deadzone",
    "square_deadzone",
    "circularize",
    "compensate_crosstalk",
    "circular_error",
    "estimate_crosstalk",
    "vector_mag",
    "clamp",
]


# ------------------------------------------------------------------ yardımcı

def clamp(v, lo, hi):
    """v değerini [lo, hi] aralığına kırpar."""
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def vector_mag(x, y):
    """(x, y) vektörünün uzunluğu."""
    return math.hypot(x, y)


def _sigmoid(t):
    """Taşmaya dayanıklı sigmoid."""
    if t >= 30.0:
        return 1.0
    if t <= -30.0:
        return 0.0
    return 1.0 / (1.0 + math.exp(-t))


# -------------------------------------------------------- ölü bölge (deadzone)

def square_deadzone(x, y, dz):
    """KARE ölü bölge: her eksene ayrı ayrı uygulanır (klasik yöntem).

    Sorunları: köşelerde (diyagonalde) yön bozulur, ölü bölge sınırında
    çıkış sıçrar ve dairesel hareket "köşeli/sarsıntılı" hissedilir.
    Burada yalnızca karşılaştırma ve gösterim için bulunuyor."""
    if abs(x) < dz:
        x = 0.0
    if abs(y) < dz:
        y = 0.0
    return x, y


def circular_deadzone(x, y, dz, rescale=True, curve=1.0):
    """DAİRESEL (radial) ölü bölge - önerilen yöntem.

    Ölü bölge, vektörün UZUNLUĞUNA uygulanır; çıktının YÖNÜ her zaman
    korunur. rescale=True ise ölü bölge kenarı 0 kabul edilip çıkış 1'e
    kadar yeniden ölçeklenir; kenar civarında süreklilik ve yumuşak
    başlangıç sağlanır.

    curve > 1.0 verilirse düşük sapmalarda hassasiyet artar (expo).
    """
    mag = math.hypot(x, y)
    if mag < 1e-9 or mag <= dz:
        return 0.0, 0.0
    n = (mag - dz) / (1.0 - dz) if rescale else mag
    n = clamp(n, 0.0, 1.0)
    if curve != 1.0 and n > 0.0:
        n = n ** curve
    ux, uy = x / mag, y / mag
    return ux * n, uy * n


# ------------------------------------------------- kare kapı -> daire düzeltme

def circularize(x, y, strength=1.0):
    """Kare kapılı çubuğu DAİREYE çevirir (yön korunur).

    Xbox 360 (045e:02e0) gibi kare kapılı çubuklar, tam itişte eksende
    |v| ~ 1.00 okurken diyagonalde |v| ~ 1.41 okur. Bu yüzden daire
    çizildiğinde çıkış yarıçapı %21'e kadar dalgalanır (hardwaretester
    "Avg Error" ~%20.9; kare kapının teorik değeri %20.6'dır).

    Düzeltme: yarıçap, o yöndeki gerçek donanım erişimine bölünür.
    Erişim profili kare ile daire arasında s ile karıştırılır:

        erişim = (1 - s) * 1 + s * (r / max(|x|, |y|))
        ölçek  = 1 / erişim

    (r / max(|x|, |y|) = 1 / max(|ux|, |uy|) olduğundan bu, o yöndeki
    maksimum erişimin ta kendisidir: eksende 1.0, diyagonalde ~1.414.)

    strength (s): 0 = düzeltme yok; 1 = tam düzeltme (çıkış büyüklüğü her
    yönde max(|x|, |y|) olur). Dönüşüm her yerde sürekli ve sınırlıdır,
    bu yüzden daire çıktısı pürüzsüz kalır ve kısmi itişlerde de doğru
    büyüklük üretilir (diyagonalde 'fazla hızlı' gitme sorunu biter).
    """
    if strength <= 0.0:
        return x, y
    r = math.hypot(x, y)
    if r < 1e-9:
        return 0.0, 0.0
    m = max(abs(x), abs(y))
    reach = (1.0 - strength) + strength * (r / m)
    scale = 1.0 / reach
    return x * scale, y * scale


# ------------------------------------------------- eksen sapması (cross-talk)

def compensate_crosstalk(x, y, k=0.21, blend=0.22):
    """İsteğe bağlı: %21'e kadar çıkabilen eksen sapması (cross-talk) izi.

    Model: bir eksene basıldığında diğer eksene yaklaşık k oranında
    sızıntı olur (k=0.21 -> %21):

        x_okunan = x_gerçek + k * y_gerçek
        y_okunan = y_gerçek + k * x_gerçek

    BÜYÜK eksenden KÜÇÜK eksene sızan değer, iki eksenin dengesine göre
    YUMUŞAK bir ağırlıkla (sigmoid) çıkarılır. Eksene tam itişte sızıntı
    silinir, diyagonalde yön korunur ve geçiş sürekli olduğu için daire
    çizerken zıplama/oynama oluşmaz.

    Not: Xbox 360'ın tipik %21 "Avg Error"u kare kapı kaynaklıdır
    (bkz. circularize); bu fonksiyon ondan farklı bir sapma türünü,
    eksenler arası sızıntıyı temizler. Gerçek k değerini test aracındaki
    [V] sihirbazı ile ölçebilirsiniz.
    """
    if k <= 0.0:
        return x, y
    ax, ay = abs(x), abs(y)
    s = ax + ay
    if s < 1e-9:
        return 0.0, 0.0
    d = (ax - ay) / s              # -1..1: x ekseni ne kadar baskın?
    t = max(blend, 1e-6)
    w_y = _sigmoid(d / t)          # y'ye uygulanacak düzeltme ağırlığı
    w_x = 1.0 - w_y                # x'e uygulanacak düzeltme ağırlığı
    return x - k * w_x * y, y - k * w_y * x


def estimate_crosstalk(samples, dominant="x"):
    """Örneklerden sızıntı oranını tahmin eder (kalibrasyon sihirbazı için).

    samples: [(x, y), ...] - çubuk TEK bir yöne tam itilmişken toplanmalı.
    dominant: beklenen baskın eksen ("x" veya "y").
    Döner: ortalama |küçük eksen| / |büyük eksen| oranı (ör. 0.21),
    yeterli geçerli örnek yoksa None.
    """
    ratios = []
    for x, y in samples:
        M = abs(x) if dominant == "x" else abs(y)
        m = abs(y) if dominant == "x" else abs(x)
        if M > 0.5:
            ratios.append(m / M)
    if len(ratios) < 5:
        return None
    return sum(ratios) / len(ratios)


# ------------------------------------------------------- dairesellik ölçümü

def circular_error(samples):
    """hardwaretester tarzı 'ortalama dairesellik hatası' (yüzde).

        hata = (1 - ortalama(r) / max(r)) * 100

    Tam bastırılıp yavaşça tam tur çevrilen KARE kapılı çubukta teorik
    değer ~%20.6'dır (site ekranındaki "Avg Error" ile aynı mantık,
    kanıt: Xbox 360 sağ çubuk %20.9). Tam dairesel çubukta ~%0.

    samples: [(x, y), ...] örnekleri. En az 60 geçerli örnek (r > 0.5) ve
    max(r) >= 0.85 gerekir; yoksa None döner.
    """
    rs = [r for r in (math.hypot(x, y) for x, y in samples) if r > 0.5]
    if len(rs) < 60:
        return None
    rmax = max(rs)
    if rmax < 0.85:
        return None
    return (1.0 - (sum(rs) / len(rs)) / rmax) * 100.0


# ------------------------------------------------------------ filtre sınıfı

class StickFilter:
    """Gamepad analog çubuk için tam filtre hattı.

    Örnek:
        filt = StickFilter(deadzone=0.15, circularity=1.0)   # Xbox 360
        filt.calibrate(ornekler)              # merkez kayması (bias)
        ox, oy = filt.process(ax, ay, dt)     # her karede

    Parametreler:
        deadzone   : dairesel ölü bölge yarıçapı (0..1).
        rescale    : ölü bölge kenarını 0'a çekip 1'e ölçekle.
        curve      : expo/hassasiyet eğrisi (1.0 = doğrusal).
        circularity: kare kapı -> daire düzeltmesi (0 = kapalı,
                     1 = tam; Xbox 360 için 1.0 önerilir).
        crosstalk  : eksen sapması/sızıntı oranı (0 = kapalı, ör. 0.21).
        crosstalk_blend : sızıntı temizliğinin yumuşaklığı.
        smooth_time: yumuşatma zaman sabiti, saniye (0 = kapalı).
        snap       : bu değerin altındaki çıkışı sıfıra sabitle.
        clamp_out  : çıkışı birim daireye kırp (yön korunur).
        shape      : "circular" (varsayılan) veya "square"
                     (yalnızca karşılaştırma için).
    """

    def __init__(self, deadzone=0.15, rescale=True, curve=1.0,
                 circularity=0.0, crosstalk=0.0, crosstalk_blend=0.22,
                 smooth_time=0.06, snap=0.004, clamp_out=True,
                 shape="circular"):
        self.deadzone = float(deadzone)
        self.rescale = bool(rescale)
        self.curve = float(curve)
        self.circularity = float(circularity)
        self.crosstalk = float(crosstalk)
        self.crosstalk_blend = float(crosstalk_blend)
        self.smooth_time = float(smooth_time)
        self.snap = float(snap)
        self.clamp_out = bool(clamp_out)
        self.shape = shape

        # merkez kayması (kalibrasyonla bulunur)
        self.bias_x = 0.0
        self.bias_y = 0.0

        # yumuşatma durumu
        self._sx = 0.0
        self._sy = 0.0

    # ---------------------------------------------------------- kalibrasyon

    def calibrate(self, samples):
        """Merkez kaymasını (bias) verilen örneklerin ortalamasından bulur.
        Çubuk SERBEST bırakılmışken toplanan örnekler verilmelidir."""
        xs = [s[0] for s in samples]
        ys = [s[1] for s in samples]
        if not xs:
            return
        self.bias_x = sum(xs) / len(xs)
        self.bias_y = sum(ys) / len(ys)

    def calibrate_center(self, samples):
        """calibrate() ile aynıdır; okunabilirlik için ek ad."""
        self.calibrate(samples)

    def reset(self):
        """Yumuşatma durumunu sıfırlar (bias ve ayarlar korunur)."""
        self._sx = 0.0
        self._sy = 0.0

    # ------------------------------------------------------------ ana işlem

    def process(self, x, y, dt=1.0 / 60.0):
        """Ham (x, y) girdisini filtreleyip temiz (ox, oy) çıktısı döner.

        x, y : ham eksen değerleri (-1..1). Eksen yön anlamı çağırana aittir
               (pygame'de y aşağı pozitiftir; matematik yön-agnostiktir).
        dt   : saniye cinsinden geçen süre (yumuşatma için).
        """
        # 1) merkez kayması (bias) düzeltmesi
        x -= self.bias_x
        y -= self.bias_y

        # 2) isteğe bağlı eksen sapması (cross-talk) temizliği
        x, y = compensate_crosstalk(x, y, self.crosstalk,
                                    self.crosstalk_blend)

        # 3) kare kapı -> daire düzeltmesi (Xbox 360 için 1.0 önerilir)
        x, y = circularize(x, y, self.circularity)

        # 4) ölü bölge: dairesel (önerilen) veya kare (karşılaştırma)
        if self.shape == "square":
            x, y = square_deadzone(x, y, self.deadzone)
        else:
            x, y = circular_deadzone(x, y, self.deadzone,
                                     self.rescale, self.curve)

        # 5) çıkışı birim daireye kırp (yön korunur)
        if self.clamp_out:
            m = math.hypot(x, y)
            if m > 1.0:
                x /= m
                y /= m

        # 6) yumuşatma: çerçeve hızından bağımsız exponential
        if self.smooth_time > 0.0:
            alpha = 1.0 - math.exp(-max(dt, 0.0) / self.smooth_time)
            self._sx += (x - self._sx) * alpha
            self._sy += (y - self._sy) * alpha
            x, y = self._sx, self._sy

        # 7) küçük kalıntıyı sıfıra sabitle
        if abs(x) < self.snap:
            x = 0.0
        if abs(y) < self.snap:
            y = 0.0

        return x, y


# ------------------------------------------------------------------ self test

def _demo():
    dz = 0.15
    print("=" * 70)
    print("gamepad_stick.py - self test (sentetik veri, gamepad gerekmez)")
    print("=" * 70)

    # ------------------------------------------------------------------ [1]
    print()
    print("[1] OLU BOLGE: KARE vs DAIRESEL  (|giris| = 0.20 sabit, aci taramasi)")
    print("    Ayni yondeki giris, iki yontemde nasil cikiyor?")
    print("    aci |  KARE cikti (x,y)  yon  |  DAIRESEL cikti (x,y)  yon")
    print("    ----+--------------------------+--------------------------")

    def fmt(vx, vy):
        m = math.hypot(vx, vy)
        if m < 1e-9:
            return "(%6.3f,%6.3f)  %5s" % (0.0, 0.0, "---")
        return "(%6.3f,%6.3f)  %5.1f" % (vx, vy,
                                         math.degrees(math.atan2(vy, vx)))

    for deg in (0, 10, 20, 30, 40, 45, 50, 60, 70, 80, 90):
        a = math.radians(deg)
        x, y = 0.20 * math.cos(a), 0.20 * math.sin(a)
        sx, sy = square_deadzone(x, y, dz)
        cx, cy = circular_deadzone(x, y, dz)
        print("    %3d |  %s |  %s" % (deg, fmt(sx, sy), fmt(cx, cy)))
    print("    Not: kare cikti 45 derece civarinda 0'a duser, sonra aniden")
    print("    90 dereceye atlar. Dairesel ciktida yon HER ZAMAN giris ile")
    print("    ayni; yalnizca buyukluk yumusakca kuculur.")

    # ------------------------------------------------------------------ [2]
    print()
    print("[2] EKSEN SAPMASI (cross-talk) TEMIZLIGI  (k=0.21, yumusatma kapali)")
    print("    Eksene tam itisin diger eksene sizintisi (%21) ne oldu?")
    print("    giris (x, y)      cikis (x, y)      sizinti %")
    print("    ------------------------------------------------+---------")
    f = StickFilter(deadzone=dz, crosstalk=0.21, smooth_time=0.0)
    rows = [(1.00, 0.21), (0.60, 0.126), (0.21, 1.00), (-0.80, -0.168)]
    for rx, ry in rows:
        ox, oy = f.process(rx, ry, 1.0 / 60.0)
        if abs(rx) >= abs(ry):
            b = abs(ry / rx) * 100.0
            a2 = abs(oy / ox) * 100.0 if abs(ox) > 1e-9 else 0.0
        else:
            b = abs(rx / ry) * 100.0
            a2 = abs(ox / oy) * 100.0 if abs(oy) > 1e-9 else 0.0
        print("    (%5.2f,%6.3f) -> (%6.3f,%6.4f)   %%%5.1f -> %%%4.1f"
              % (rx, ry, ox, oy, b, a2))

    # ------------------------------------------------------------------ [3]
    print()
    print("[3] TAM DAIRE + %21 EKSEN SIZINTISI - yon hatasi")
    print("    Cubugu tam daire cizdirdigimizde CIKIS yonu ideale ne kadar")
    print("    yaklasik kaliyor? (dusuk = duzgun dairesel hareket)")

    def max_angle_err(filt, leak=0.21, n=720):
        worst = 0.0
        for i in range(n):
            a = 2.0 * math.pi * i / n
            tx, ty = math.cos(a), math.sin(a)
            mx, my = tx + leak * ty, ty + leak * tx   # sahte %21 sizinti
            ox, oy = filt.process(mx, my, 1.0 / 240.0)
            if math.hypot(ox, oy) < 1e-6:
                continue
            e = math.degrees(math.atan2(oy, ox) - a)
            e = (e + 180.0) % 360.0 - 180.0
            worst = max(worst, abs(e))
        return worst

    f_off = StickFilter(deadzone=dz, crosstalk=0.0, smooth_time=0.0)
    f_on = StickFilter(deadzone=dz, crosstalk=0.21, smooth_time=0.0)
    print("    filtre KAPALI : en fazla yon hatasi = %5.1f derece"
          % max_angle_err(f_off))
    print("    filtre ACIK   : en fazla yon hatasi = %5.1f derece"
          % max_angle_err(f_on))

    # ------------------------------------------------------------------ [4]
    print()
    print("[4] KARE KAPI -> DAIRE (Xbox 360 / 045e:02e0 tipik durumu)")
    print("    Sonsuz kare kapili cubuk simulasyonu: tam itisle tam tur...")
    raw = []
    n = 720
    for i in range(n):
        a = 2.0 * math.pi * i / n
        c, s = math.cos(a), math.sin(a)
        m = max(abs(c), abs(s))
        raw.append((c / m, s / m))          # kare sinirinda okuma

    e_raw = circular_error(raw)
    f_circ = StickFilter(deadzone=dz, crosstalk=0.0, circularity=1.0,
                         smooth_time=0.0)
    out = [f_circ.process(x, y, 1.0 / 240.0) for x, y in raw]
    e_filt = circular_error(out)
    print("    hardwaretester tarzi ortalama dairesellik hatasi:")
    print("      ham cikis     : %%%5.1f   (site: sag cubuk %%20.9 olcmustu)" % e_raw)
    print("      circularize(1): %%%5.1f" % e_filt)

    print("    Yari itis testi (rho=0.5 diyagonal; kare kapi okumasi = 0.5,0.5)")
    half = (0.5, 0.5)          # kare kapıda yarı diyagonal itişin okuması
    ref = (0.5, 0.0)           # aynı itişin eksen (doğru) referansı
    f0 = StickFilter(deadzone=dz, circularity=0.0, smooth_time=0.0)
    o0 = f0.process(*half, 1.0 / 60.0)
    o1 = f_circ.process(*half, 1.0 / 60.0)
    rr = f_circ.process(*ref, 1.0 / 60.0)
    print("      eksen referansi           : %.3f" % math.hypot(*rr))
    print("      duzeltme KAPALI (diyagonal): %.3f  <- yanlislikla hizli"
          % math.hypot(*o0))
    print("      duzeltme ACIK   (diyagonal): %.3f  <- eksenle ayni, dogru"
          % math.hypot(*o1))

    # ------------------------------------------------------------------ [5]
    print()
    print("[5] YUMUSATMA (tau = 60 ms) - 1.0 adim girisi, 60 fps")
    fs = StickFilter(deadzone=dz, crosstalk=0.0, smooth_time=0.06)
    for i in range(8):
        ox, _ = fs.process(1.0, 0.0, 1.0 / 60.0)
        print("    t = %5.1f ms   cikis x = %.3f" % (i * 16.7, ox))

    print()
    print("Bitti. Canli gorsel test icin: python gamepad_stick_test.py")
    print("StickFilter(deadzone=0.15, circularity=1.0) -> 360 cubuk hazir.")


if __name__ == "__main__":
    _demo()
