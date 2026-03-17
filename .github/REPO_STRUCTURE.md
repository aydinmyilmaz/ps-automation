# Repo Structure & Maintenance Guide

## Directory Layout

```
ps-automation/
├── .github/
│   ├── skills/                # Repo-local Copilot/Codex skills
│   └── REPO_STRUCTURE.md      # Repo conventions and maintenance notes
│
├── frontend/                  # React + Vite web UI
│   ├── src/App.jsx            # Single name renderer component
│   ├── src/styles.css         # Styling
│   ├── vite.config.js         # Dev server + API proxy config
│   └── package.json           # Frontend dependencies
│
├── scripts/                   # All Python backend & automation
│   ├── ps_single_renderer.py  # Core: Photoshop JSX builder + runner
│   ├── single_render_api.py   # HTTP API server (web UI backend)
│   ├── onecall_unattended_batch.py  # CLI batch processor
│   ├── desktop_qt_app.py      # Qt/PySide6 native batch app
│   ├── batch_desktop_app.py   # Tkinter batch app (legacy)
│   ├── desktop_web_app.py     # Web-based batch app (fallback)
│   ├── single_supabase_export.py  # Optional custom-mode archive + Supabase import helper
│   └── build_desktop_app.py   # PyInstaller packager
│
├── data/
│   ├── selected-psd/          # Active PSD template (1 file)
│   ├── psd-files/             # All PSD versions (local only, gitignored)
│   └── final_names/           # Production name list (3000 names)
│
├── output/                    # All generated files (gitignored)
│   ├── web_single/            # Web renderer PNG output
│   ├── batch_runs/            # Batch process results
│   │   ├── desktop/           # Desktop app runs
│   │   └── onecall_20xall/    # CLI batch runs
│   └── archive/               # Old test outputs
│
├── archive/                   # Deprecated scripts (gitignored)
├── plan/                      # Project planning docs
│
├── start.sh                   # Launch web app (API + frontend)
├── start_desktop_qt.command   # Launch Qt batch app (macOS)
├── start_desktop_qt.bat       # Launch Qt batch app (Windows)
├── package.json               # npm scripts wrapper
├── requirements-desktop.txt   # Python deps for desktop apps
└── .gitignore
```

## Key Rules

### Scripts go in `scripts/`
All Python files live in `scripts/`. Never put `.py` files at the project root.
Root-level launchers (`start.sh`, `*.command`, `*.bat`) call into `scripts/`.

### Path convention
Scripts use two path constants:
- `SCRIPTS_DIR` = directory of the script itself (`scripts/`)
- `PROJECT_ROOT` = `SCRIPTS_DIR.parent` (repo root)

Use `SCRIPTS_DIR` for referencing sibling scripts.
Use `PROJECT_ROOT` for `data/`, `output/`, and other project-level paths.

### PSD management
- **Active template**: `data/selected-psd/` (1 file, the one the app uses)
- **All versions**: `data/psd-files/` (local archive, gitignored)
- When switching to a new PSD, place it in `selected-psd/` and update `PSD_PATH` in `ps_single_renderer.py`

### Output is ephemeral
Everything in `output/` is gitignored and reproducible.
- `web_single/` = web app renders
- `batch_runs/` = batch process results
- `archive/` = old test outputs (can be deleted anytime)

### Desktop single-save exports
- Optional custom-mode saves are archived under `~/Desktop/ps_single_supabase_exports/`
- The desktop app writes to Supabase directly using `config/supabase_single_save.json`
- That config is local-only and can be bundled into packaged desktop builds
- These files are user-local and are not tracked in git

### What gets committed
- `scripts/*.py` (all backend code)
- `frontend/src/` (React components + styles)
- `frontend/package.json`, `vite.config.js`
- Root config: `start.sh`, `package.json`, `.gitignore`, `README.md`
- Launcher scripts: `start_desktop_qt.*`
- Example config files in `config/*.example.json`
- Local bundle config template: `config/supabase_single_save.example.json`
- Build metadata in `build/spec/`

### What stays local (gitignored)
- `output/` (generated PNGs)
- `data/final_names/` (name lists)
- `data/psd-files/` (large PSD archive)
- `data/selected-psd/` (active PSD, too large for git)
- `archive/` (deprecated code)
- `credentials/` and local `config/*.json`
- `.env.local` and similar local env files
- `frontend/node_modules/`, `frontend/dist/`

## Adding New Features

### New script
1. Create in `scripts/`
2. Use `SCRIPTS_DIR` / `PROJECT_ROOT` pattern for paths
3. Add `sys.path.insert(0, str(SCRIPTS_DIR))` before sibling imports if needed

### New skill
1. Create in `.github/skills/<skill-name>/`
2. Put the reusable workflow in `SKILL.md`
3. Add `references/` only when the skill body would otherwise get long
4. Keep executable logic in the main repo, usually `scripts/`

### New PSD template
1. Place in `data/selected-psd/`
2. Update `PSD_PATH` in `scripts/ps_single_renderer.py`
3. Verify style group names match `STYLE_CHOICES`

### New name list
1. Place in `data/final_names/`
2. Update `DEFAULT_NAMES_FILE` in `onecall_unattended_batch.py`

### New frontend page
1. Add component in `frontend/src/`
2. API proxy routes in `frontend/vite.config.js`
3. Backend endpoints in `scripts/single_render_api.py`
