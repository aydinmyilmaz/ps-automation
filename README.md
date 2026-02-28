# PS Automation - Single Name Web Renderer

Bu proje **batch değil**, tek seferde:
- kullanıcıdan **1 isim**
- kullanıcıdan **1 renk seçimi**
alıp Photoshop ile **transparent PNG** üretir.

## Özellik
- Web arayüzü (React + Vite)
- Backend API (Python)
- Photoshop script ile gerçek render
- Renk seçenekleri: `metal`, `ALTIN`, `MAVI`, `PEMBE`
- Çıktı: backgroundsuz PNG

## Gereksinimler
- macOS
- Adobe Photoshop 2026 (kurulu)
- Python 3
- Node.js + npm
- Font: `ClarendonBT-Black` (Photoshop içinde görünür olmalı)

## Tek Komutla Çalıştırma
Proje kökünde:

```bash
bash start.sh
```

Bu komut:
1. API server'ı açar (`http://127.0.0.1:8000`)
2. React frontend'i açar (`http://127.0.0.1:5173`)

Tarayıcıda:

```text
http://127.0.0.1:5173
```

## Kullanım
1. Text gir
2. Renk seç
3. `Render PNG` bas
4. Görseli önizle
5. `Download PNG` ile indir

## Çıktı Konumu
Üretilen dosyalar:

```text
output/web_single/
```

## Durdurma
Terminalde:

```text
Ctrl + C
```

`start.sh` hem API hem frontend süreçlerini birlikte kapatır.

## Manuel Çalıştırma (isteğe bağlı)
API:

```bash
python3 single_render_api.py
```

Frontend:

```bash
cd frontend
npm run dev
```

## Sorun Giderme
- `Font missing: ClarendonBT-Black`:
  - Fontu kur, Photoshop’u kapat/aç.
- `PSD not found`:
  - `data/text_only_2.psd` dosyasının yerini kontrol et.
- Port hatası:
  - 8000 veya 5173 portunu kullanan başka uygulamayı kapat.
