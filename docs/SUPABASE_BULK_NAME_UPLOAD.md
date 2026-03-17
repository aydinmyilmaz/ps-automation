# Supabase Bulk Name Upload

Bu repo, klasor bazli PSD name PNG ciktularini `ai_text_design_cache` tablosuna toplu yuklemek icin [scripts/import_ai_text_design_cache.mjs](/Users/aydin/Desktop/apps/ps-automation/scripts/import_ai_text_design_cache.mjs) scriptini kullanir.

## Kullandigi kaynaklar

- Supabase config: [config/supabase_single_save.example.json](/Users/aydin/Desktop/apps/ps-automation/config/supabase_single_save.example.json)
- Lokal config: `config/supabase_single_save.json`
- Varsayilan model: `ps-desktop-name-png-v1`
- Varsayilan design name: `bootleg_2026`
- Varsayilan tablo: `ai_text_design_cache`
- Varsayilan bucket: `generated-maps`
- Varsayilan folder: `ai-text-design-cache`

## Beklenen klasor yapisi

Script su yapida klasorleri bekler:

```text
<input-dir>/
  Brown/
    DAVID.png
  Green_Dark/
    AUSTIN.png
  Turkuaz/
    ANGEL.png
```

- Parent klasor renk/stil olarak yorumlanir.
- Dosya adi `target_text` olur.
- `_` ve `-` karakterleri bosluga cevrilir.
- `transparent`, `nobg`, `no-bg`, `removebg`, `final`, `vN` gibi suffix'ler temizlenir.

## Renk map'i

- `Black -> black_silver`
- `Gray -> silver_gray`
- `Blue -> cobalt_blue`
- `Blue_Dark` / `Blue Dark -> steel_blue`
- `Patina_Blue` / `Patina Blue -> patina_blue`
- `Turkuaz -> teal_cyan`
- `Green -> forest_green`
- `Green_Dark` / `Green Dark -> dark_green`
- `Purple -> deep_purple`
- `Pink -> vivid_pink`
- `Red -> crimson_red`
- `Rose -> dusty_rose`
- `Gold -> gold_amber`
- `Yellow -> olive_yellow`
- `Brown -> warm_brown`
- `Brown_Light` / `Brown Light -> light_brown`

## On hazirlik

1. Lokal Supabase config'i doldur:
   - `config/supabase_single_save.json`
2. Root dependency'leri kur:

```bash
npm install
```

## Canonical komut

Iki veya daha fazla klasoru ayni run icinde yuklemek icin:

```bash
npm run import:ai-text-cache -- \
  --input-dir /Users/aydin/Desktop/apps/ps-automation/output/desktop_batch_runs_0_500 \
  --input-dir /Users/aydin/Desktop/apps/ps-automation/desktop_batch_runs_5000_1000 \
  --recursive \
  --color-from-parent-dir \
  --black-to-alpha \
  --skip-existing-cache-keys \
  --lookup-batch-size 25
```

## Onerilen akış

1. Once `--dry-run` ile klasor parse'ini kontrol et.
2. Sonra gercek import'u calistir.
3. Import edilen klasor canli olarak buyuyorsa ayni komutu bir kez daha calistir.
4. `--skip-existing-cache-keys` acikken script mevcut `cache_key` satirlarini atlar.

## Neden `lookup-batch-size 25`

Bu projede buyuk `cache_key` lookup batch'leri Supabase tarafinda `Bad Request` uretebildi. `25` bu veri setinde stabil calisti.

## Duplicate davranisi

- Ayni run icinde ayni `cache_key` birden fazla dosyada varsa ilk gorulen dosya tutulur.
- DB'de zaten var olan `cache_key` satirlari `--skip-existing-cache-keys` ile atlanir.
- DB upsert `cache_key,image_hash` uzerinden yapilir.

## Retry davranisi

Script transient upload hatalarinda otomatik retry yapar.

- Varsayilan retry sayisi: `3`
- Override: `--max-retries <n>`

## Dikkat edilmesi gerekenler

- Bos dosyalar import edilmez; summary'de raporlanir.
- Input klasoru import sirasinda hala yaziliyorsa sayilar run'dan run'a degisebilir.
- Bu durumda ayni komutu tekrar calistirip delta reconciliation yap.

## Yardim

Tum parametreleri gormek icin:

```bash
node scripts/import_ai_text_design_cache.mjs --help
```
