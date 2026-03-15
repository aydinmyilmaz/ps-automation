# Desktop Qt App — UI Redesign

**Date:** 2026-03-10
**File:** `scripts/desktop_qt_app.py`

## Goal
Redesign `desktop_qt_app.py` UI to be more professional, user-friendly and functional while keeping all existing logic intact.

## Problems with Current UI
- Flat layout, no visual hierarchy
- Render Settings cramped (6 spinboxes in one row)
- Photoshop Exec field wastes space on macOS
- No progress bar — only small text status
- Log always visible, takes excessive space
- Color checkboxes plain — no visual color hints
- No selected color count indicator

## New Design

### 1. Header Bar
- Dark background (`#111827`)
- App title + PSD filename badge (truncated, full path in tooltip)
- Subtle bottom border

### 2. Files Section
- PSD path (truncated display, full path in tooltip)
- Output Folder + "Open in Finder" button
- Photoshop Exec: **hidden on macOS**, shown only on Windows

### 3. Render Settings — 2-row layout
- Row 1: Mode (wide) + Letters
- Row 2: Chunk / Retries / Timeout / Restart — compact spinboxes
- Custom names panel: only visible in Custom mode

### 4. Color Palette — Visual chips
- Each style has a colored dot matching the brand color
- Checkbox + colored dot + label
- Badge: "X of 16 selected" updates live

### 5. Action Bar + Progress
- QProgressBar (done / total) — shown during run, hidden when idle
- Buttons: Start/Resume (primary), Stop (danger), Open Output (secondary)
- Status pill moved inline with progress

### 6. Collapsible Log
- "Run Log ▾" toggle button
- Collapsed by default, expands on click
- Shows last result prominently when collapsed

## Color Map for Dots
| Style | Hex |
|---|---|
| Yellow | #FDE047 |
| Turkuaz | #2DD4BF |
| Rose | #FB7185 |
| Red | #EF4444 |
| Purple | #A855F7 |
| Pink | #EC4899 |
| Patina Blue | #60A5FA |
| Green | #22C55E |
| Gray | #9CA3AF |
| Gold | #F59E0B |
| Green Dark | #15803D |
| Brown Light | #D97706 |
| Brown | #92400E |
| Blue Dark | #1E40AF |
| Blue | #3B82F6 |
| Black | #1F2937 |

## Implementation Scope
- All logic methods untouched (`build_cmd`, `start_run`, `stop_run`, etc.)
- Only `_build_ui`, `_apply_theme`, `_render_styles`, minor helpers changed
- `refresh_status` updated to drive QProgressBar
- Add `_toggle_log` method
- Add `_update_style_badge` method
