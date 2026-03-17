---
name: supabase-bulk-upload-name
description: Bulk upload PSD name PNG folders and subfolders into Supabase ai_text_design_cache using this repo's config and importer script. Use this when the user wants to upload one or more rendered name directories, reconcile live-growing folders, or re-run delta imports safely.
argument-hint: [input dir(s)] [dry-run or real upload]
---

# Supabase Bulk Upload Name

Use [scripts/import_ai_text_design_cache.mjs](/Users/aydin/Desktop/apps/ps-automation/scripts/import_ai_text_design_cache.mjs) for this workflow.

Read [references/commands.md](/Users/aydin/Desktop/apps/ps-automation/.github/skills/supabase-bulk-upload-name/references/commands.md) when you need exact commands, verification, or troubleshooting.

## Default assumptions

- Supabase credentials live in `config/supabase_single_save.json`.
- The target table is `ai_text_design_cache`.
- The default model is `ps-desktop-name-png-v1`.
- The default design name is `bootleg_2026`.
- Color keys come from the direct parent folder.

## Canonical run pattern

1. Count files in each input directory first.
2. Run a dry-run when the folder structure is new or suspicious.
3. Run the real import with:
   - `--recursive`
   - `--color-from-parent-dir`
   - `--black-to-alpha`
   - `--skip-existing-cache-keys`
   - `--lookup-batch-size 25`
4. If a folder is still being written to, rerun the same command until the delta drops to zero or to an expected small number.
5. Verify the final DB count against the latest folder snapshot.

## Important behavior

- Empty PNG files are skipped and reported.
- Duplicate `cache_key` values inside the same run are collapsed to the first-seen file.
- Existing DB rows are skipped by `cache_key` when `--skip-existing-cache-keys` is enabled.
- Retry support exists for transient upload failures; lower `--upload-concurrency` if failures repeat.

## When to adjust defaults

- Lower `--upload-concurrency` when Supabase upload errors appear.
- Keep `--lookup-batch-size 25` unless you have already verified a larger safe value.
- Use repeated `--input-dir` flags when multiple folders must land in the same import pass.
