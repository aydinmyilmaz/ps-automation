#!/usr/bin/env node
import fs from 'node:fs';
import { promises as fsp } from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { createClient } from '@supabase/supabase-js';
import sharp from 'sharp';

const DEFAULT_CONFIG_FILE = 'config/supabase_single_save.json';
const DEFAULT_MODEL = 'ps-desktop-name-png-v1';
const DEFAULT_DESIGN_NAME = 'bootleg_2026';
const DEFAULT_COLOR_KEY = 'purple_gloss';
const DEFAULT_CACHE_TABLE = 'ai_text_design_cache';
const DEFAULT_STORAGE_BUCKET = 'generated-maps';
const DEFAULT_STORAGE_FOLDER = 'ai-text-design-cache';
const DEFAULT_UPLOAD_CONCURRENCY = 8;
const DEFAULT_LOOKUP_BATCH_SIZE = 25;
const DEFAULT_DB_BATCH_SIZE = 500;
const DEFAULT_MAX_IMAGE_BYTES = 15_000_000;
const DEFAULT_BLACK_THRESHOLD = 16;
const DEFAULT_MAX_RETRIES = 3;
const PS_COLOR_KEY_BY_FOLDER = {
  Black: 'black_silver',
  Gray: 'silver_gray',
  Blue: 'cobalt_blue',
  Blue_Dark: 'steel_blue',
  'Blue Dark': 'steel_blue',
  Patina_Blue: 'patina_blue',
  'Patina Blue': 'patina_blue',
  Turkuaz: 'teal_cyan',
  Green: 'forest_green',
  Green_Dark: 'dark_green',
  'Green Dark': 'dark_green',
  Purple: 'deep_purple',
  Pink: 'vivid_pink',
  Red: 'crimson_red',
  Rose: 'dusty_rose',
  Gold: 'gold_amber',
  Yellow: 'olive_yellow',
  Brown: 'warm_brown',
  Brown_Light: 'light_brown',
  'Brown Light': 'light_brown'
};

function loadJsonConfig(configPath) {
  if (!fs.existsSync(configPath)) return {};
  const raw = JSON.parse(fs.readFileSync(configPath, 'utf8'));
  return {
    supabaseUrl: String(raw.supabaseUrl || '').trim().replace(/\/+$/g, ''),
    serviceRoleKey: String(raw.serviceRoleKey || '').trim(),
    bucket: String(raw.storageBucket || '').trim(),
    table: String(raw.cacheTable || '').trim(),
    folder: String(raw.storageFolder || '').trim()
  };
}

function printUsageAndExit(code = 0) {
  const usage = `
Usage:
  node scripts/import_ai_text_design_cache.mjs --input-dir <dir> [--input-dir <dir> ...] [options]

Required:
  --input-dir <dir>                Directory that contains generated PNG files. Repeatable.

Options:
  --config <path>                  JSON config file (default: config/supabase_single_save.json)
  --ext <exts>                     Comma separated extensions (default: .png)
  --recursive                      Scan subfolders recursively
  --model <name>                   Model metadata (default: ps-desktop-name-png-v1)
  --design-name <name>             Fixed design_name for all rows (default: bootleg_2026)
  --target-prefix <text>           Prefix added to derived target text
  --name-starts-with <letters>     Keep only files whose derived target starts with any of these letters
  --exact-names <csv>              Keep only files whose derived target exactly matches one of the given names
  --color-key <key>                Fixed color metadata when not using parent folders
  --color-from-parent-dir          Derive color key from direct parent folder name
  --black-to-alpha                 Convert near-black pixels to transparent alpha before upload
  --black-threshold <0-255>        Threshold used by --black-to-alpha (default: 16)
  --skip-existing-cache-keys       Skip files whose model/text/color cache_key already exists in DB
  --upload-concurrency <n>         Parallel uploads (default: 8)
  --lookup-batch-size <n>          Existing cache key lookup batch size (default: 25)
  --db-batch-size <n>              DB upsert batch size (default: 500)
  --max-image-bytes <n>            Max file size allowed (default: 15000000)
  --max-retries <n>                Retry count for transient upload failures (default: 3)
  --bucket <name>                  Storage bucket override
  --folder <path>                  Storage folder override
  --table <name>                   DB table override
  --dry-run                        Do not upload or write DB; print summary only
  --help                           Show this help

Examples:
  node scripts/import_ai_text_design_cache.mjs \\
    --input-dir output/desktop_batch_runs_0_500 \\
    --recursive \\
    --color-from-parent-dir \\
    --black-to-alpha \\
    --skip-existing-cache-keys

  node scripts/import_ai_text_design_cache.mjs \\
    --input-dir output/desktop_batch_runs_0_500 \\
    --input-dir desktop_batch_runs_5000_1000 \\
    --recursive \\
    --color-from-parent-dir \\
    --black-to-alpha \\
    --skip-existing-cache-keys \\
    --lookup-batch-size 25
`.trim();
  console.log(usage);
  process.exit(code);
}

function parseArgs(argv) {
  const args = {
    config: DEFAULT_CONFIG_FILE,
    inputDirs: [],
    ext: '.png',
    recursive: false,
    model: DEFAULT_MODEL,
    designName: DEFAULT_DESIGN_NAME,
    targetPrefix: '',
    nameStartsWith: '',
    exactNames: '',
    colorKey: DEFAULT_COLOR_KEY,
    colorFromParentDir: false,
    blackToAlpha: false,
    blackThreshold: DEFAULT_BLACK_THRESHOLD,
    skipExistingCacheKeys: false,
    uploadConcurrency: DEFAULT_UPLOAD_CONCURRENCY,
    lookupBatchSize: DEFAULT_LOOKUP_BATCH_SIZE,
    dbBatchSize: DEFAULT_DB_BATCH_SIZE,
    maxImageBytes: DEFAULT_MAX_IMAGE_BYTES,
    maxRetries: DEFAULT_MAX_RETRIES,
    bucket: '',
    folder: '',
    table: '',
    dryRun: false
  };

  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === '--help' || arg === '-h') printUsageAndExit(0);
    else if (arg === '--config') args.config = argv[++i] || args.config;
    else if (arg === '--input-dir') args.inputDirs.push((argv[++i] || '').trim());
    else if (arg === '--ext') args.ext = argv[++i] || args.ext;
    else if (arg === '--recursive') args.recursive = true;
    else if (arg === '--model') args.model = (argv[++i] || args.model).trim();
    else if (arg === '--design-name') args.designName = (argv[++i] || args.designName).trim();
    else if (arg === '--target-prefix') args.targetPrefix = (argv[++i] || '').trim();
    else if (arg === '--name-starts-with') args.nameStartsWith = (argv[++i] || '').trim();
    else if (arg === '--exact-names') args.exactNames = (argv[++i] || '').trim();
    else if (arg === '--color-key') args.colorKey = (argv[++i] || args.colorKey).trim();
    else if (arg === '--color-from-parent-dir') args.colorFromParentDir = true;
    else if (arg === '--black-to-alpha') args.blackToAlpha = true;
    else if (arg === '--black-threshold') args.blackThreshold = Number(argv[++i] || args.blackThreshold);
    else if (arg === '--skip-existing-cache-keys') args.skipExistingCacheKeys = true;
    else if (arg === '--upload-concurrency') args.uploadConcurrency = Number(argv[++i] || args.uploadConcurrency);
    else if (arg === '--lookup-batch-size') args.lookupBatchSize = Number(argv[++i] || args.lookupBatchSize);
    else if (arg === '--db-batch-size') args.dbBatchSize = Number(argv[++i] || args.dbBatchSize);
    else if (arg === '--max-image-bytes') args.maxImageBytes = Number(argv[++i] || args.maxImageBytes);
    else if (arg === '--max-retries') args.maxRetries = Number(argv[++i] || args.maxRetries);
    else if (arg === '--bucket') args.bucket = (argv[++i] || '').trim();
    else if (arg === '--folder') args.folder = (argv[++i] || '').trim();
    else if (arg === '--table') args.table = (argv[++i] || '').trim();
    else if (arg === '--dry-run') args.dryRun = true;
    else throw new Error(`Unknown argument: ${arg}`);
  }

  args.inputDirs = args.inputDirs.filter(Boolean);
  if (args.inputDirs.length === 0) throw new Error('At least one --input-dir is required');
  if (!args.model) throw new Error('--model is required');
  if (!args.designName) throw new Error('--design-name is required');
  if (!Number.isFinite(args.uploadConcurrency) || args.uploadConcurrency < 1) {
    throw new Error('--upload-concurrency must be >= 1');
  }
  if (!Number.isFinite(args.lookupBatchSize) || args.lookupBatchSize < 1) {
    throw new Error('--lookup-batch-size must be >= 1');
  }
  if (!Number.isFinite(args.dbBatchSize) || args.dbBatchSize < 1) {
    throw new Error('--db-batch-size must be >= 1');
  }
  if (!Number.isFinite(args.maxImageBytes) || args.maxImageBytes < 1024) {
    throw new Error('--max-image-bytes must be >= 1024');
  }
  if (!Number.isFinite(args.maxRetries) || args.maxRetries < 0) {
    throw new Error('--max-retries must be >= 0');
  }
  if (!Number.isFinite(args.blackThreshold) || args.blackThreshold < 0 || args.blackThreshold > 255) {
    throw new Error('--black-threshold must be between 0 and 255');
  }
  return args;
}

function toSlug(value, fallback = 'text') {
  const normalized = value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 48);
  return normalized || fallback;
}

function normalizeTargetText(value) {
  return value.trim().replace(/\s+/g, ' ').toLowerCase();
}

function normalizeColorKey(value) {
  return value.trim().toLowerCase();
}

function buildCacheKey(model, targetText, colorKey) {
  return `${model.trim()}::${normalizeTargetText(targetText)}::${normalizeColorKey(colorKey)}`;
}

function parseExtensions(rawExt) {
  return rawExt
    .split(',')
    .map((x) => x.trim().toLowerCase())
    .filter(Boolean)
    .map((x) => (x.startsWith('.') ? x : `.${x}`));
}

async function listFiles(inputDirs, recursive, extSet) {
  const out = [];

  async function walk(inputRoot, currentDir) {
    const entries = await fsp.readdir(currentDir, { withFileTypes: true });
    for (const entry of entries) {
      const absPath = path.join(currentDir, entry.name);
      if (entry.isDirectory()) {
        if (recursive) await walk(inputRoot, absPath);
        continue;
      }
      if (!entry.isFile()) continue;
      const ext = path.extname(entry.name).toLowerCase();
      if (!extSet.has(ext)) continue;
      out.push({
        inputRoot,
        absPath,
        relPath: path.relative(inputRoot, absPath).replaceAll('\\', '/')
      });
    }
  }

  for (const inputRoot of inputDirs) {
    await walk(inputRoot, inputRoot);
  }

  return out.sort((a, b) => `${a.inputRoot}\u0000${a.relPath}`.localeCompare(`${b.inputRoot}\u0000${b.relPath}`));
}

function inferContentType(filePath) {
  const ext = path.extname(filePath).toLowerCase();
  if (ext === '.png') return { ext: 'png', contentType: 'image/png' };
  if (ext === '.webp') return { ext: 'webp', contentType: 'image/webp' };
  return { ext: 'jpg', contentType: 'image/jpeg' };
}

function deriveTargetTextFromFileName(filePath) {
  const rawBase = path.basename(filePath, path.extname(filePath));
  const withoutSuffix = rawBase.replace(/(?:[_-](transparent|nobg|no-bg|removebg|final|v\d+))+$/gi, '');
  const spaced = withoutSuffix.replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim();
  return spaced || rawBase;
}

function parseNameStartsWith(raw) {
  return [...new Set(raw.trim().toUpperCase().split('').filter((char) => /[A-Z]/.test(char)))];
}

function parseExactNames(raw) {
  return new Set(
    raw
      .split(',')
      .map((item) => item.trim().toUpperCase())
      .filter(Boolean)
  );
}

function shouldKeepTargetText(targetText, startsWithChars, exactNames) {
  const normalizedTarget = targetText.trim().toUpperCase();
  if (exactNames.size > 0) {
    return exactNames.has(normalizedTarget);
  }
  if (startsWithChars.length === 0) return true;
  const first = normalizedTarget.charAt(0);
  return startsWithChars.includes(first);
}

function deriveColorKeyFromParentDir(filePath) {
  const folderName = path.basename(path.dirname(filePath));
  const direct = PS_COLOR_KEY_BY_FOLDER[folderName];
  if (direct) return direct;
  const normalized = folderName.replace(/[\s-]+/g, '_');
  return PS_COLOR_KEY_BY_FOLDER[normalized] || '';
}

async function convertBlackToAlpha(bytes, threshold) {
  const input = sharp(bytes, { failOn: 'none' });
  const { data, info } = await input.ensureAlpha().raw().toBuffer({ resolveWithObject: true });
  for (let i = 0; i < data.length; i += info.channels) {
    const r = data[i];
    const g = data[i + 1];
    const b = data[i + 2];
    if (r <= threshold && g <= threshold && b <= threshold) {
      data[i + 3] = 0;
    }
  }
  return sharp(data, {
    raw: {
      width: info.width,
      height: info.height,
      channels: info.channels
    }
  }).png().toBuffer();
}

function chunkArray(items, size) {
  const out = [];
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size));
  return out;
}

async function mapLimit(items, concurrency, worker) {
  const results = new Array(items.length);
  let cursor = 0;
  const runners = Array.from({ length: concurrency }, async () => {
    while (true) {
      const index = cursor++;
      if (index >= items.length) break;
      results[index] = await worker(items[index], index);
    }
  });
  await Promise.all(runners);
  return results;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function withRetries(label, retries, worker) {
  let attempt = 0;
  while (true) {
    try {
      return await worker();
    } catch (error) {
      if (attempt >= retries) throw error;
      attempt += 1;
      const waitMs = Math.min(5000, 300 * 2 ** (attempt - 1));
      const message = error instanceof Error ? error.message : String(error);
      console.warn(`[retry ${attempt}/${retries}] ${label}: ${message}`);
      await sleep(waitMs);
    }
  }
}

function buildPlannedFile(file, args) {
  const rawTarget = deriveTargetTextFromFileName(file.absPath);
  const targetText = args.targetPrefix ? `${args.targetPrefix} ${rawTarget}`.trim() : rawTarget;
  const colorKey = args.colorFromParentDir ? deriveColorKeyFromParentDir(file.absPath) : args.colorKey;
  if (!colorKey) {
    throw new Error(`Could not resolve color key for ${file.relPath}`);
  }
  return {
    ...file,
    targetText,
    colorKey,
    cacheKey: buildCacheKey(args.model, targetText, colorKey)
  };
}

async function fetchExistingCacheKeys(supabase, table, cacheKeys, batchSize) {
  const existing = new Set();
  const uniqueCacheKeys = [...new Set(cacheKeys.filter(Boolean))];
  if (uniqueCacheKeys.length === 0) return existing;

  const chunks = chunkArray(uniqueCacheKeys, batchSize);
  for (const chunk of chunks) {
    const { data, error } = await supabase
      .from(table)
      .select('cache_key')
      .in('cache_key', chunk);
    if (error) {
      throw new Error(`Existing cache key lookup failed: ${error.message}`);
    }
    for (const row of data || []) {
      if (row?.cache_key) existing.add(row.cache_key);
    }
  }

  return existing;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const config = loadJsonConfig(path.resolve(process.cwd(), args.config));

  const supabaseUrl = process.env.SUPABASE_URL || process.env.NEXT_PUBLIC_SUPABASE_URL || config.supabaseUrl;
  const serviceRole = process.env.SUPABASE_SERVICE_ROLE_KEY || config.serviceRoleKey;
  const bucket = args.bucket || process.env.SUPABASE_AI_TEXT_CACHE_BUCKET || process.env.SUPABASE_STORAGE_BUCKET || config.bucket || DEFAULT_STORAGE_BUCKET;
  const folder = args.folder || process.env.SUPABASE_AI_TEXT_CACHE_FOLDER || config.folder || DEFAULT_STORAGE_FOLDER;
  const table = args.table || process.env.SUPABASE_AI_TEXT_CACHE_TABLE || config.table || DEFAULT_CACHE_TABLE;
  const inputDirs = args.inputDirs.map((item) => path.resolve(process.cwd(), item));

  if (!supabaseUrl) throw new Error('Missing SUPABASE_URL / NEXT_PUBLIC_SUPABASE_URL / supabaseUrl config');
  if (!serviceRole) throw new Error('Missing SUPABASE_SERVICE_ROLE_KEY / serviceRoleKey config');
  if (!folder) throw new Error('Storage folder is required');
  if (!table) throw new Error('Cache table is required');

  for (const inputDir of inputDirs) {
    if (!fs.existsSync(inputDir) || !fs.statSync(inputDir).isDirectory()) {
      throw new Error(`Input directory does not exist: ${inputDir}`);
    }
  }

  const extSet = new Set(parseExtensions(args.ext));
  if (extSet.size === 0) throw new Error('No extensions provided');

  const discoveredFiles = await listFiles(inputDirs, args.recursive, extSet);
  if (discoveredFiles.length === 0) {
    console.log('No matching files found.');
    return;
  }

  const nameStartsWithChars = parseNameStartsWith(args.nameStartsWith);
  const exactNames = parseExactNames(args.exactNames);
  const filteredFiles = discoveredFiles.filter((file) => {
    const targetText = deriveTargetTextFromFileName(file.absPath);
    return shouldKeepTargetText(targetText, nameStartsWithChars, exactNames);
  });
  if (filteredFiles.length === 0) {
    console.log('No files matched the requested filters.');
    return;
  }

  const plannedFiles = filteredFiles.map((file) => buildPlannedFile(file, args));
  const dedupedFiles = [];
  const seenCacheKeys = new Set();
  let skippedDuplicateInputs = 0;
  for (const file of plannedFiles) {
    if (seenCacheKeys.has(file.cacheKey)) {
      skippedDuplicateInputs += 1;
      continue;
    }
    seenCacheKeys.add(file.cacheKey);
    dedupedFiles.push(file);
  }

  const supabase = createClient(supabaseUrl, serviceRole, {
    auth: { autoRefreshToken: false, persistSession: false }
  });

  let files = dedupedFiles;
  let skippedExistingCount = 0;

  if (args.skipExistingCacheKeys) {
    const existingCacheKeys = await fetchExistingCacheKeys(
      supabase,
      table,
      dedupedFiles.map((file) => file.cacheKey),
      args.lookupBatchSize
    );
    files = dedupedFiles.filter((file) => !existingCacheKeys.has(file.cacheKey));
    skippedExistingCount = dedupedFiles.length - files.length;
  }

  if (files.length === 0) {
    console.log('No files left to import after dedupe.');
    console.log(`[import_ai_text_design_cache] Files before filters: ${discoveredFiles.length}`);
    console.log(`[import_ai_text_design_cache] Files after name filters: ${filteredFiles.length}`);
    console.log(`[import_ai_text_design_cache] Skipped duplicate inputs: ${skippedDuplicateInputs}`);
    console.log(`[import_ai_text_design_cache] Skipped existing cache keys: ${skippedExistingCount}`);
    return;
  }

  console.log(`[import_ai_text_design_cache] Files: ${files.length}`);
  console.log(`[import_ai_text_design_cache] Files before filters: ${discoveredFiles.length}`);
  console.log(`[import_ai_text_design_cache] Files after name filters: ${filteredFiles.length}`);
  console.log(`[import_ai_text_design_cache] Skipped duplicate inputs: ${skippedDuplicateInputs}`);
  console.log(`[import_ai_text_design_cache] Model: ${args.model}`);
  console.log(`[import_ai_text_design_cache] Design name: ${args.designName}`);
  console.log(`[import_ai_text_design_cache] Color mode: ${args.colorFromParentDir ? 'parent-folder mapping' : args.colorKey}`);
  console.log(`[import_ai_text_design_cache] Name prefix filter: ${nameStartsWithChars.length > 0 ? nameStartsWithChars.join(',') : 'none'}`);
  console.log(`[import_ai_text_design_cache] Exact names filter: ${exactNames.size > 0 ? [...exactNames].join(',') : 'none'}`);
  console.log(`[import_ai_text_design_cache] Black to alpha: ${args.blackToAlpha ? `yes (threshold ${args.blackThreshold})` : 'no'}`);
  console.log(`[import_ai_text_design_cache] Skip existing cache keys: ${args.skipExistingCacheKeys ? `yes (${skippedExistingCount} skipped)` : 'no'}`);
  console.log(`[import_ai_text_design_cache] Upload concurrency: ${args.uploadConcurrency}`);
  console.log(`[import_ai_text_design_cache] Lookup batch size: ${args.lookupBatchSize}`);
  console.log(`[import_ai_text_design_cache] DB batch size: ${args.dbBatchSize}`);
  console.log(`[import_ai_text_design_cache] Bucket: ${bucket}`);
  console.log(`[import_ai_text_design_cache] Folder: ${folder}`);
  console.log(`[import_ai_text_design_cache] Table: ${table}`);
  console.log(`[import_ai_text_design_cache] Dry run: ${args.dryRun ? 'yes' : 'no'}`);

  const uploadedRows = [];
  const skippedTooLarge = [];
  const skippedEmpty = [];
  const failedFiles = [];

  await mapLimit(files, args.uploadConcurrency, async (file, idx) => {
    try {
      const originalBytes = await fsp.readFile(file.absPath);
      if (originalBytes.length === 0) {
        skippedEmpty.push({ filePath: file.relPath });
        return;
      }
      if (originalBytes.length > args.maxImageBytes) {
        skippedTooLarge.push({ filePath: file.relPath, bytes: originalBytes.length });
        return;
      }

      const bytes = args.blackToAlpha ? await convertBlackToAlpha(originalBytes, args.blackThreshold) : originalBytes;
      const imageHash = createHash('sha256').update(bytes).digest('hex');
      const keyHash = createHash('sha256').update(file.cacheKey).digest('hex').slice(0, 16);
      const colorSegment = normalizeColorKey(file.colorKey).replace(/[^a-z0-9_-]/g, '-').slice(0, 32) || 'color';
      const textSegment = toSlug(file.targetText, 'text');
      const designSegment = toSlug(args.designName, 'design');
      const imageMeta = inferContentType(file.absPath);
      const storagePath = `${folder}/${designSegment}/${colorSegment}/${textSegment}-${keyHash}/${imageHash}.${imageMeta.ext}`;

      if (!args.dryRun) {
        await withRetries(file.relPath, args.maxRetries, async () => {
          const upload = await supabase.storage.from(bucket).upload(storagePath, bytes, {
            contentType: imageMeta.contentType,
            upsert: true
          });
          if (upload.error) {
            throw new Error(upload.error.message);
          }
        });
      }

      const { data: publicData } = supabase.storage.from(bucket).getPublicUrl(storagePath);
      uploadedRows.push({
        model: args.model,
        target_text: file.targetText,
        color_key: file.colorKey,
        design_name: args.designName,
        normalized_text: normalizeTargetText(file.targetText),
        cache_key: file.cacheKey,
        image_url: publicData.publicUrl,
        image_hash: imageHash,
        storage_path: storagePath,
        use_count: 1,
        last_used_at: new Date().toISOString()
      });

      if ((idx + 1) % 250 === 0 || idx + 1 === files.length) {
        console.log(`Processed ${idx + 1}/${files.length}`);
      }
    } catch (error) {
      failedFiles.push({
        filePath: file.relPath,
        error: error instanceof Error ? error.message : String(error)
      });
    }
  });

  let dbChunks = 0;
  if (!args.dryRun && uploadedRows.length > 0) {
    const chunks = chunkArray(uploadedRows, args.dbBatchSize);
    for (const chunk of chunks) {
      const { error } = await supabase
        .from(table)
        .upsert(chunk, { onConflict: 'cache_key,image_hash', ignoreDuplicates: true });
      if (error) {
        throw new Error(`DB upsert failed: ${error.message}`);
      }
      dbChunks += 1;
    }
  }

  console.log('');
  console.log('Import summary');
  console.log(`- Scanned files: ${files.length}`);
  console.log(`- Skipped duplicate inputs: ${skippedDuplicateInputs}`);
  console.log(`- Skipped existing cache keys: ${skippedExistingCount}`);
  console.log(`- Prepared rows: ${uploadedRows.length}`);
  console.log(`- Skipped (empty): ${skippedEmpty.length}`);
  console.log(`- Skipped (too large): ${skippedTooLarge.length}`);
  console.log(`- Failed: ${failedFiles.length}`);
  console.log(`- DB batches: ${dbChunks}`);

  if (skippedEmpty.length > 0) {
    console.log('');
    console.log('Skipped empty files (first 20):');
    for (const item of skippedEmpty.slice(0, 20)) {
      console.log(`- ${item.filePath}`);
    }
  }

  if (skippedTooLarge.length > 0) {
    console.log('');
    console.log('Skipped oversized files (first 20):');
    for (const item of skippedTooLarge.slice(0, 20)) {
      console.log(`- ${item.filePath} (${item.bytes} bytes)`);
    }
  }

  if (failedFiles.length > 0) {
    console.log('');
    console.log('Failed files (first 20):');
    for (const item of failedFiles.slice(0, 20)) {
      console.log(`- ${item.filePath}: ${item.error}`);
    }
    process.exitCode = 1;
  }
}

main().catch((error) => {
  console.error('[import_ai_text_design_cache] failed:', error.message || error);
  process.exit(1);
});
