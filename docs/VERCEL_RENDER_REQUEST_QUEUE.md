# Vercel App -> Desktop Photoshop Worker Integration

Bu dokuman, Vercel tarafinda calisan ekibin bu projedeki lokal Photoshop render sistemine nasil baglanacagini aciklar.

Hedef:

- Vercel app kullanicidan bir isim ve stil alir.
- Vercel backend bu istegi Supabase'de yeni bir request tablosuna yazar.
- Lokal desktop app her 10 saniyede bir bu tabloyu kontrol eder.
- Yeni request varsa Photoshop ile render alir.
- Render sonucu mevcut Supabase image/cache yapisina kaydedilir.
- Request tablosu `done` veya `failed` olarak guncellenir.

Bu yapi sayesinde Vercel, lokal PC'deki Photoshop'a dogrudan baglanmak zorunda kalmaz.

## Kisa ozet

Bu sistem bir HTTP API degil, bir `job queue` gibi calisacak.

Akis:

```text
User -> Vercel app -> Vercel backend -> Supabase render_requests
Desktop app -> Supabase render_requests poll -> Photoshop render
Desktop app -> Supabase storage + ai_text_design_cache
Desktop app -> Supabase render_requests status update
Vercel app -> request status check -> sonucu goster
```

## Neden boyle yapiyoruz

Lokal Photoshop sadece masaustu bilgisayarda calisiyor.

Vercel serverless function:

- Photoshop calistiramaz
- lokal `localhost`'a guvenli sekilde ulasamaz
- uzun sureli render isini icinde tutmak icin uygun degildir

Bu nedenle Vercel tarafi sadece `request olusturan` ve `status takip eden` taraf olacak.

## Bu repoda zaten var olan seyler

Bu repo tarafinda hali hazirda su parcalar var:

- lokal Photoshop render akisi
- tek isim render
- Supabase'a render sonucu yazma
- mevcut image/cache tablosu

Ilgili dosyalar:

- [scripts/desktop_qt_app.py](../scripts/desktop_qt_app.py)
- [scripts/ps_single_renderer.py](../scripts/ps_single_renderer.py)
- [scripts/single_supabase_export.py](../scripts/single_supabase_export.py)
- [config/supabase_single_save.example.json](../config/supabase_single_save.example.json)

Yani Vercel ekibinin yeni yapacagi sey render motoru degil, sadece request kaydi olusturma ve request status gosterme tarafidir.

## Yeni tablo: `render_requests`

Vercel tarafi mevcut `ai_text_design_cache` tablosuna dogrudan yazmamalidir.

Sebep:

- o tablo sonuc tablosudur
- request queue mantigi icin ayri bir tablo gerekir
- request lifecycle takibi gerekir

Onerilen tablo:

```sql
create table if not exists public.render_requests (
  id uuid primary key default gen_random_uuid(),
  request_text text not null,
  style text not null,
  uppercase boolean not null default true,
  status text not null default 'pending',
  requested_at timestamptz not null default now(),
  claimed_at timestamptz,
  completed_at timestamptz,
  worker_id text,
  attempt_count integer not null default 0,
  result_image_url text,
  result_storage_path text,
  result_cache_key text,
  error_message text,
  source_app text not null default 'vercel-web'
);

create index if not exists render_requests_status_requested_idx
  on public.render_requests (status, requested_at);
```

Status degerleri:

- `pending`
- `processing`
- `done`
- `failed`

Not:

- Desktop worker bu tablo adini lokal config icindeki `requestTable` alanindan okur.
- O config ornegi icin bkz. [config/supabase_single_save.example.json](../config/supabase_single_save.example.json)

## Vercel ekibinin sorumlulugu

Vercel ekibi 3 sey yapacak:

1. Kullanicidan isim ve stil alan backend endpoint yazacak.
2. Bu istegi `render_requests` tablosuna `pending` olarak ekleyecek.
3. Kullaniciya request durumunu gosterecek.

## Onerilen backend endpoint'leri

### 1. Request olustur

`POST /api/render-requests`

Beklenen body:

```json
{
  "text": "KEREM",
  "style": "Gold",
  "uppercase": true
}
```

Beklenen davranis:

- `text` trim edilir
- `style` validate edilir
- request tabloya `pending` olarak insert edilir
- response olarak `requestId` donulur

Onerilen response:

```json
{
  "ok": true,
  "requestId": "0c5fce3b-2c5e-4c28-bd53-9e85862f1b81",
  "status": "pending"
}
```

### 2. Request status oku

`GET /api/render-requests/:id`

Onerilen response:

```json
{
  "ok": true,
  "request": {
    "id": "0c5fce3b-2c5e-4c28-bd53-9e85862f1b81",
    "status": "done",
    "request_text": "KEREM",
    "style": "Gold",
    "result_image_url": "https://...",
    "error_message": null,
    "requested_at": "2026-03-24T10:00:00.000Z",
    "claimed_at": "2026-03-24T10:00:08.000Z",
    "completed_at": "2026-03-24T10:00:20.000Z"
  }
}
```

Vercel frontend bu endpoint'i belli araliklarla poll ederek sonucu gosterebilir.

## Frontend davranisi

Vercel frontend'de akis su olmali:

1. User formu doldurur.
2. Frontend `POST /api/render-requests` cagirir.
3. `requestId` alir.
4. Frontend her 2 ila 5 saniyede bir `GET /api/render-requests/:id` cagirir.
5. Status:
   - `pending`: "Sirada bekliyor"
   - `processing`: "Render aliniyor"
   - `done`: sonucu goster
   - `failed`: hata mesaji goster

Not:

- Frontend browser'dan Supabase `service_role_key` kullanmamalidir.
- Supabase insert ve select isleri Vercel backend tarafinda yapilmalidir.

## Desktop worker ile veri sozlesmesi

Desktop tarafi bu tablodan su alanlari okuyacak:

- `id`
- `request_text`
- `style`
- `uppercase`
- `status`

Desktop tarafi is bittiginde su alanlari yazacak:

- `status`
- `claimed_at`
- `completed_at`
- `worker_id`
- `attempt_count`
- `result_image_url`
- `result_storage_path`
- `result_cache_key`
- `error_message`

## Claim mantigi neden onemli

Ayni request'in iki kez render edilmemesi gerekir.

Bu nedenle desktop worker bir `pending` kaydi buldugunda onu atomik sekilde `processing` yapmalidir.

Onerilen mantik:

1. En eski `pending` request bulunur.
2. Sadece o satir hala `pending` ise `processing` olarak update edilir.
3. Update basariliysa worker o request'i isler.

Tek worker olsa bile bu mantik kullanilmalidir.

## Onerilen request yasam dongusu

### Pending

Vercel backend yeni kaydi olusturur:

- `status = pending`
- `attempt_count = 0`

### Processing

Desktop worker isi aldiginda:

- `status = processing`
- `claimed_at = now()`
- `worker_id = local-machine-name veya local-app-instance`
- `attempt_count = attempt_count + 1`

### Done

Render ve upload tamamlandiginda:

- `status = done`
- `completed_at = now()`
- `result_image_url = public image url`
- `result_storage_path = Supabase storage path`
- `result_cache_key = cache key if available`
- `error_message = null`

### Failed

Render veya upload hata verirse:

- `status = failed`
- `completed_at = now()`
- `error_message = hata detayi`

## Beklenen stil degerleri

Desktop render tarafi su style degerlerini destekliyor:

- `Yellow`
- `Turkuaz`
- `Rose`
- `Red`
- `Purple`
- `Pink`
- `Patina Blue`
- `Green`
- `Gray`
- `Gold`
- `Green Dark`
- `Brown Light`
- `Brown`
- `Blue Dark`
- `Blue`
- `Black`

Vercel formu bu degerlerle sinirlanmalidir.

Kaynak:

- [scripts/ps_single_renderer.py](../scripts/ps_single_renderer.py)

## Result nereye yaziliyor

Desktop tarafi render sonucunu mevcut Supabase sonuc yapisina yazacak.

Bu projede mevcut sonuc tablosu:

- `ai_text_design_cache`

Bu tablo request queue degildir.
Bu tablo "uretilmis ve yuklenmis render sonucu" tablosudur.

Vercel tarafi normal durumda bu tabloya insert yapmayacak.
Sadece request tablosu uzerinden ilerleyecek.

## Guvenlik notlari

Vercel ekibi su kurallari uygulamali:

- `service_role_key` browser'a cikmamalidir
- Supabase yazma islemleri sadece server-side yapilmalidir
- Input validation yapilmalidir
- `text` bos olamaz
- `style` whitelist ile validate edilmelidir
- Rate limit eklenmelidir

## Hata senaryolari

Vercel ekibi su durumlari beklemelidir:

- Desktop app kapali olabilir
- Desktop app acik ama worker mode kapali olabilir
- Photoshop acilmamis olabilir
- PSD dosyasi eksik olabilir
- Font eksik olabilir
- Supabase upload basarisiz olabilir

Bu yuzden frontend "anlik response" beklememeli, "async job" mantigi ile calismalidir.

## Basit UX onerisi

Kullanici submit ettikten sonra:

- "Istek alindi"
- "Siraya eklendi"
- "Render isleniyor"
- "Render hazir"

Status `failed` ise:

- "Render alinamadi"
- teknik detay istenirse `error_message` gosterilebilir

## Vercel ekibi icin yapilacaklar listesi

1. Supabase'de `render_requests` tablosunu olustur.
2. Vercel backend'e `POST /api/render-requests` endpoint'i ekle.
3. Vercel backend'e `GET /api/render-requests/:id` endpoint'i ekle.
4. Form submit akisini bu yeni endpoint'e bagla.
5. Frontend'de request status polling ekle.
6. `done` durumunda `result_image_url` gorselini goster.
7. `failed` durumunda hata ekrani goster.
8. Tum Supabase yazma islerini server-side tut.

## Desktop ekibi ile handoff kontrati

Vercel ekibi bu alanlari garanti etmelidir:

- `request_text`
- `style`
- `uppercase`
- `status = pending`

Desktop ekibi su alanlari dolduracaktir:

- `status`
- `claimed_at`
- `completed_at`
- `worker_id`
- `attempt_count`
- `result_image_url`
- `result_storage_path`
- `result_cache_key`
- `error_message`

## Bu dokumanin en kisa versiyonu

Vercel tarafi render yapmayacak.

Vercel tarafi sadece:

1. request olusturacak
2. request status okuyacak
3. sonucu gosterecek

Gercek render isi lokal desktop app tarafinda calisacak.
