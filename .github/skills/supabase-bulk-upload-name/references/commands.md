# Supabase Bulk Upload Commands

## Dry-run

```bash
npm run import:ai-text-cache -- \
  --input-dir /absolute/path/to/folder \
  --recursive \
  --color-from-parent-dir \
  --black-to-alpha \
  --skip-existing-cache-keys \
  --lookup-batch-size 25 \
  --dry-run
```

## Real upload

```bash
npm run import:ai-text-cache -- \
  --input-dir /absolute/path/to/folder-a \
  --input-dir /absolute/path/to/folder-b \
  --recursive \
  --color-from-parent-dir \
  --black-to-alpha \
  --skip-existing-cache-keys \
  --lookup-batch-size 25
```

## Lower-concurrency retry pass

Use this when the first pass reports transient upload failures:

```bash
npm run import:ai-text-cache -- \
  --input-dir /absolute/path/to/folder \
  --recursive \
  --color-from-parent-dir \
  --black-to-alpha \
  --skip-existing-cache-keys \
  --lookup-batch-size 25 \
  --upload-concurrency 4
```

## Quick verification ideas

- Count source PNG files with `find <dir> -type f -name '*.png' | wc -l`
- Check for empty PNGs with:

```bash
find <dir> -type f -name '*.png' -size 0
```

- Inspect script help with:

```bash
node scripts/import_ai_text_design_cache.mjs --help
```

## Troubleshooting

- `Bad Request` during existing cache key lookup:
  Use `--lookup-batch-size 25`.
- `fetch failed` during upload:
  Re-run with `--upload-concurrency 4` or lower.
- Folder is still growing during import:
  Re-run the same command once the producer slows down; the script will skip existing cache keys.
- Unexpected duplicates across folders:
  Pass all input dirs in one command so first-seen cache keys win consistently.
