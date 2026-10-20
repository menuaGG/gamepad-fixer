# Gamepad Fixer

Xbox kontrolcülerindeki **analog çubuk kayması (drift)** ve **ölü bölge (deadzone)** sorununu yazılımsal olarak düzelten Windows aracı.

Fiziksel kolu okur; dinlenme kaymasını otomatik telafi eder, filtreleri uygular ve sonucu **sanal bir Xbox 360 kolu** olarak sunar. Oyunlar ve test siteleri düzeltilmiş değerleri görür.

### ⬇️ [**GamepadFixer.exe'yi indir**](https://github.com/menuaGG/gamepad-fixer/releases/latest/download/GamepadFixer.exe)

> Tek dosya — kurulum gerekmez. ViGEmBus sürücüsü eksikse **uygulama kendisi indirip kurar**; size sadece "Evet" demek kalır.

📦 **Her şey tek pakette:** [GamepadFixer-full.zip](https://github.com/menuaGG/gamepad-fixer/releases/latest/download/GamepadFixer-full.zip) — exe + kaynak kodlar + README + belgeler.

> [!WARNING]
> **Eski/bozuk ViGEmBus sürümleri (özellikle 2020 tarihli 1.17.333.0 gibi) kuruluysa:** v1.10 öncesi sürümler sürücüyü doğrudan güncellemeye çalışıyordu; bazı sistemlerde bu sırada **mavi ekran (0x1000007E)** görülebiliyordu. **v1.10.1+** eski sürücüyü otomatik tespit eder, üzerine kurulum **yapmaz**; önce güvenle temizler, yeniden başlatma ister ve sonra güncel ViGEmBus paketini (1.22.0) kurar. Mavi ekran aldıysanız aşağıdaki **"Mavi ekran / takılı kalan sürücü"** bölümüne bakın.
>
> **Not:** Güncel ViGEmBus 1.22.0 paketinin içindeki sürücü dosyası **1.21.442.0**'dır; bu normaldir.

![Ana pencere](docs/main-window.png)

## Özellikler

- **Otomatik kayma düzeltme**: Kol başlarken ~1,3 sn'lik kalibrasyonla dinlenme kayması ölçülür ve sürekli telafi edilir. Elini çektiğinde çubuk oyunda **0** okunur.
- **Güvenli sürücü kurulumu**: ViGEmBus sürücüsü eksikse uygulama ilk açılışta otomatik indirip kurmayı önerir. **Eski/bozuk bir sürüm (ör. 1.17.333.0) varsa üzerine kurmaz**; önce temizler, yeniden başlatma ister, sonra güncel ViGEmBus paketini (1.22.0+) kurar. Araçlar menüsünden "ViGEmBus'u onar" ile de çalıştırılabilir.
- **Ölü Bölge Testi**: Kolları serbest bırakıp tek düğmeye basmanız yeterli. Araç 3 sn ölçer, ölçülen kaymayı **tamamen kapatan** ölü bölge değerini önerir; "Uygula ve Kaydet" dediğinizde ayarı yazıp gerekirse kol köprüsünü kendisi yeniden başlatır.

  ![Ölü Bölge Testi](docs/deadzone-test.png)

- **Dairesel ölü bölge** (kare değil), **dairesellik düzeltme**, **yumuşatma** ve **dead-band (crosstalk)** filtreleri — hepsi ayarlanabilir.
- **Canlı test sayfası**: düğmeler, analog çubuklar, dairesellik hatası (Avg Error) ve titreşim testi (1 sn / sürekli).
- Koyu / açık / sistem teması, sade / detaylı panel düzeni.
- Pil durumu göstergesi.
- Ayarlar kalıcıdır: `%LOCALAPPDATA%\GamepadFixer\config.json`

## Nasıl çalışır?

1. Fiziksel kol **XInput** üzerinden okunur (ayarlanabilir 30–1000 Hz).
2. Başlangıçta dinlenme kayması ölçülür ve her okumadan geri çıkarılır (bias).
3. Filtreler uygulanır: dairesel ölü bölge, dairesellik, yumuşatma, dead-band.
4. Sonuç, [ViGEm](https://github.com/nefarius/ViGEmBus) üzerinden **sanal Xbox 360 kolu (028e)** olarak sunulur.

> Not: Fiziksel kolun kendi ham sinyali donanımsaldır ve değiştirilemez. Düzeltilmiş değerler sanal kolda görünür — oyunlarda/sitelerde **sanal kolu** seçin.

## Kurulum

1. **[GamepadFixer.exe'yi indirin](https://github.com/menuaGG/gamepad-fixer/releases/latest/download/GamepadFixer.exe)** — tek dosya.
2. Çalıştırın. SmartScreen uyarısı çıkarsa: **Daha fazla bilgi → Yine de çalıştır** (exe imzasızdır).
3. ViGEmBus sürücüsü kurulu değilse uygulama **ilk açılışta otomatik indirip kurmayı önerir**:
   "Evet" deyin → Windows yönetici izni verin → gerisini uygulama halleder. İsterseniz
   **Araçlar → "ViGEmBus sürücüsünü kur / güncelle"** menüsünden de çalıştırabilirsiniz.
   - **Eski/bozuk sürüm varsa** uygulama önce **"Güvenli onarım"** önerir: eski sürücü temizlenir → bilgisayar yeniden başlatılır → güncel sürüm kurulur. Bu adım **atlanmamalıdır**; eski sürücünün üzerine kurulum mavi ekrana yol açabilir.
4. Kolu bağlayın → **Başlat (F5)**. İlk saniyelerde kolu serbest bırakın (merkez kalibrasyonu).
5. Oyun/sitede listede iki kol görünür; **Xbox 360 Controller (sanal)** olanı seçin.

### İpuçları

- Oyunun otomatik olarak sanal kolu almasını istersen: kolu kapat → **Başlat** → "Fiziksel kol bekleniyor..." yazınca kolu aç.
- Ölü bölge testini **her kol değişikliğinde** tekrar yapın; kayma koldan kola değişir.
- Test, fiziksel kolun **ham** kaymasını ölçer. Düzeltmeden sonra testi tekrar çalıştırırsanız yine yüksek değer görünür — bu normaldir. Düzelmiş sonucu sanal kolda görürsünüz (dinlenirken 0.00).
- Durdurmak için: **Durdur (F6)** ya da pencereyi kapatın.

## Kaynak koddan çalıştırma

Gereksinim: Windows 10/11, Python 3.10+.

```bash
pip install -r requirements.txt
python gamepad_fixer_gui.py
```

Tek dosya exe derlemek için:

```bash
python -m PyInstaller --noconfirm --clean --onefile --windowed --name GamepadFixer ^
  --collect-all vgamepad --hidden-import hid --icon assets\gamepad.ico ^
  --add-data "assets;assets" gamepad_fixer_gui.py
```

## SSS

- **Karakter kendi kendine yürüyordu / kamera kayıyordu, çözülür mü?**
  Evet — kayma makul aralıktaysa (≤ 0.30) tamamen sıfırlanır; daha büyük kaymalar da ölü bölgeyle kapatılır.
- **Sitede "Xbox Wireless Controller" neden hâlâ kayıyor?**
  O fiziksel kolun kendisidir; ham sinyali değiştirilemez. Düzeltilmiş hâli sanal koldur (028e).
- **Windows "bilinmeyen yayıncı" uyarısı veriyor.**
  Exe kod imzalı değil; "Yine de çalıştır" ile devam edebilirsiniz.
- **Sürücü otomatik kurulmazsa?**
  **Araçlar → "ViGEmBus sürücüsünü kur / güncelle"** ile tekrar deneyin; sessiz kurulum çalışmazsa uygulama kurulum penceresini kendisi açar. Alternatif: [ViGEmBus sürümleri](https://github.com/nefarius/ViGEmBus/releases).
- **Eski ViGEmBus varken ne olur?**
  v1.10+ üzerine kurmaz; **Araçlar → "ViGEmBus'u onar"** ile önce temizler, yeniden başlatır, sonra kurar. Bu, mavi ekran riskini önler.
- **Bluetooth Xbox kolu çalışır mı?**
  Evet — kol XInput olarak göründüğü sürece (USB, Bluetooth veya Xbox Wireless Adaptör) çalışır.

## ⚠️ Mavi ekran / takılı kalan eski ViGEmBus

Eski ViGEmBus sürücüleri (özellikle 2020 tarihli **1.17.333.0**) güncel Windows'larda sorun çıkarabilir. v1.10+ bunu otomatik tespit eder ve **eski sürücünün üzerine kurulum yapmaz**. Yine de sorun yaşarsanız:

1. **Panik yapmayın.** Mavi ekran sürücü kaynaklıdır; donanım arızası değildir, kalıcı zarar vermez.
2. Bilgisayar açılıyorsa: **Araçlar → ViGEmBus'u onar** → yönetici iznini onaylayın → yeniden başlatın. Açılışta güncel sürüm kurulur.
3. Açılmıyorsa / sürekli mavi ekran veriyorsa **Güvenli Mod**:
   - Açılışta **Shift** tuşunu basılı tutarak **Yeniden Başlat** → **Sorun Giderme** → **Gelişmiş Seçenekler** → **Başlangıç Ayarları** → **Yeniden Başlat** → **4) Güvenli Mod**.
   - Güvenli Mod'da **yönetici PowerShell** açın:

     ```powershell
     pnputil /enum-drivers
     # "Original Name: vigembus.inf" satırındaki "oemX.inf" adını not alın:
     pnputil /delete-driver oemX.inf /uninstall /force
     sc.exe delete ViGEmBus
     ```

   - Yeniden başlatın; ardından [güncel ViGEmBus](https://github.com/nefarius/ViGEmBus/releases/latest) kurun.
4. Kurulum sırasında **tekrar mavi ekran** alırsanız tekrar denemeyin; sorunu bildirin.

## Sürüm notları

- **v1.10.1** — 1.22.0 paketindeki resmi sürücü dosyasının (1.21.442.0) yanlışlıkla "eski" sayılması düzeltildi. Güncel sürücüyle gereksiz onarım/uyarı çıkmaz.
- **v1.10** — Eski/bozuk ViGEmBus tespiti (sürüm + dosya kontrolü), güvenli temizlik + yeniden başlatma akışı, "ViGEmBus'u onar" menüsü, kurulum öncesi sürüm doğrulama, mavi ekran uyarıları. Sürücü yönetimi `vgamepad`'den ayrıldı: ViGEmBus hiç kurulu olmasa bile uygulama açılıp kurulum önerebilir.
- **v1.9 ve öncesi** — Bu sürümlerde eski ViGEmBus üzerine kurulum koruması yoktu; lütfen v1.10.1+ kullanın.

## Teşekkürler

- [ViGEmBus](https://github.com/nefarius/ViGEmBus) — sanal kol altyapısı
- [vgamepad](https://github.com/yannbouteiller/vgamepad) — Python ViGEm istemcisi
