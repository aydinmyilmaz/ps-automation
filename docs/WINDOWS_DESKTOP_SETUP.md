# Windows Desktop Setup

Bu dokuman, repo GitHub'dan clone edildikten sonra desktop app'in Windows'ta nasil calistirilacagini anlatir.

## Gerekli Olanlar

Asagidaki seyler kurulu olmali:

- Windows 10 veya 11
- Adobe Photoshop 2026
- `uv`
- Python 3.11

Desktop app Python paketleri:

- `PySide6`
- `Pillow`

Bu paketler su dosyada tanimlidir:

- [requirements-desktop.txt](../requirements-desktop.txt)

## 1. Repo'yu clone et

```bat
git clone <REPO_URL>
cd ps-automation
```

## 2. `uv` kur

Tavsiye edilen yol:

```bat
winget install --id=astral-sh.uv -e
```

Alternatif resmi installer:

```bat
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Kurulumdan sonra yeni bir terminal ac ve kontrol et:

```bat
uv --version
```

## 3. Python 3.11'i `uv` ile kur

```bat
uv python install 3.11
```

Kontrol:

```bat
uv python list
```

Not:

- Bu repo icin hedef surum `Python 3.11`'dir.
- Root'taki [`.python-version`](../.python-version) dosyasi da bunu belirtir.

## 4. `.venv` olustur

Bu repo icin tavsiye edilen yol `uv` ile local virtual environment kullanmaktir.

```bat
uv venv --python 3.11 .venv
```

Aktif et:

```bat
.venv\Scripts\activate
```

Kontrol:

```bat
python --version
```

Burada `Python 3.11.x` gormelisin.

## 5. Desktop dependency'lerini kur

```bat
uv pip install -r requirements-desktop.txt
```

## 6. Photoshop tarafini hazirla

Asagidaki seyler gerekli:

- Photoshop kurulu olmali
- PSD dosyasi repo icinde mevcut olmali
  - Varsayilan PSD yolu: `data/selected-psd/Bootleg STARTUP 2026 v4 all colors 3.psd`
- Kullanilan font Photoshop tarafinda gorunmeli
  - Beklenen font adi: `ClarendonBT-Black`
  - Windows'ta font genelde su klasore kurulur:
    - `C:\Windows\Fonts`
  - En guvenli yol:
    - font dosyasina sag tikla
    - `Install for all users` sec
    - sonra Photoshop'u tamamen kapatip yeniden ac

Not:

- Uygulama Photoshop'u lokal makinede kullanir
- Photoshop yoksa render calismaz

## 7. Desktop app'i baslat

En kolay yol:

```bat
start_desktop_qt.bat
```

Alternatif:

```bat
.venv\Scripts\python.exe scripts\desktop_qt_app.py
```

Not:

- `start_desktop_qt.bat` artik `.venv\Scripts\python.exe` bekler.
- `.venv` yoksa sana gerekli `uv` kurulum komutlarini gosterir.

## 8. Website Orders kullanacaksan

Su dosyayi doldur:

- `config\supabase_single_save.json`

Ornek:

- [config/supabase_single_save.example.json](../config/supabase_single_save.example.json)

Bu config gerekli:

- Website Orders
- Supabase upload
- request queue akisi

## 9. Uygulama icinde neyi kontrol etmelisin

Desktop app acildiginda:

1. PSD path dogru mu
2. Output folder dogru mu
3. Color secimleri dogru mu
4. Gerekirse `Website Orders` acik mi

## Sik gorulen problemler

### `uv` bulunamiyor

Hata:

```text
'uv' is not recognized ...
```

Cozum:

- terminali kapatip yeniden ac
- `winget install --id=astral-sh.uv -e` komutunu yeniden calistir
- gerekirse resmi installer kullan

### PySide6 eksik

Hata:

```text
PySide6 is required
```

Cozum:

```bat
uv pip install -r requirements-desktop.txt
```

### Photoshop render fail oluyor

Kontrol et:

- Photoshop acik mi
- PSD dosyasi mevcut mu
- Font mevcut mu
- Scratch disk yeterli mi

### Website Orders calismiyor

Kontrol et:

- `config\supabase_single_save.json` var mi
- `supabaseUrl` ve `serviceRoleKey` dogru mu
- `requestTable` dogru mu

## Canonical Windows komutu

Bu repo icin en guvenli manuel baslatma komutu:

```bat
.venv\Scripts\python.exe scripts\desktop_qt_app.py
```
