# PS Automation

Bu repo artik birden fazla is akisini barindiriyor:

- Desktop Qt app
- Local single-render web API
- React frontend
- Website Orders queue worker
- Photoshop batch scripts

Bu nedenle ana giris noktalari sadece `scripts/` altindadir.

## Python Standardi

Bu repo icin tavsiye edilen ve beklenen Python setup'i:

- `uv`
- `.venv`
- `Python 3.11`
- root'taki [.python-version](.python-version)

Kurulum:

macOS / Linux:

```bash
uv python install 3.11
uv venv --python 3.11 .venv
source .venv/bin/activate
uv pip install -r requirements-desktop.txt
```

Windows:

```bat
uv python install 3.11
uv venv --python 3.11 .venv
.venv\Scripts\activate
uv pip install -r requirements-desktop.txt
```

Launcher'lar artik `.venv` bekler. Global `python` kurulu olsa bile repo icin canonical akıs `.venv` icinden calismaktir.

## Kisa Repo Yapisi

- `scripts/`
  - Uygulamanin gercek Python entrypoint'leri burada
- `frontend/`
  - React + Vite UI
- `docs/`
  - Kurulum ve entegrasyon dokumanlari
- `config/`
  - Lokal config dosyalari
- `data/`
  - PSD ve bundle edilen veri dosyalari

## En Cok Kullanilan Komutlar

### Desktop Qt app

macOS / Linux:

```bash
./start_desktop_qt.command
```

Windows:

```bat
start_desktop_qt.bat
```

Manuel:

macOS / Linux:

```bash
.venv/bin/python scripts/desktop_qt_app.py
```

Windows:

```bat
.venv\Scripts\python.exe scripts\desktop_qt_app.py
```

### Local web API + frontend

```bash
bash start.sh
```

Bu komut su iki sureci baslatir:

- Python API: `scripts/single_render_api.py`
- React frontend: `frontend/`

### API'yi tek basina calistirmak

```bash
.venv/bin/python scripts/single_render_api.py
```

## Root Duzeyinde Bilinmesi Gereken Dosyalar

- `start.sh`
  - local web API + frontend baslatir
- `start_desktop_qt.bat`
  - Windows desktop launcher
- `start_desktop_qt.command`
  - macOS desktop launcher
- `requirements-desktop.txt`
  - Desktop app dependency listesi
- `requirements-gmail.txt`
  - Gmail related tooling dependency listesi
- `.python-version`
  - Repo'nun bekledigi Python major/minor versiyonu

## Windows Dokumani

Windows'ta desktop app'i calistirmak icin:

- [docs/WINDOWS_DESKTOP_SETUP.md](docs/WINDOWS_DESKTOP_SETUP.md)

## Supabase ve Website Orders Dokumanlari

- [docs/VERCEL_RENDER_REQUEST_QUEUE.md](docs/VERCEL_RENDER_REQUEST_QUEUE.md)
- [docs/SUPABASE_BULK_NAME_UPLOAD.md](docs/SUPABASE_BULK_NAME_UPLOAD.md)
