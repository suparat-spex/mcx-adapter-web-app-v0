# Design System: "Spex Explorer" desktop look

Extracted from `soil_weather_explorer_v10.py`. Reference code is in `ui_kit.py` (PySide6). Run `python ui_kit.py` to see the demo.

The style is a dense, neutral desktop tool, similar to QGIS or VS Code: grey surfaces, one warm orange accent, and status colours used only for meaning. Dark is the default. Light and Follow-system are included.

---

## 1. Principles

1. **Only tokens.** Widgets never contain hex colours. They use tokens (`@accent@` in QSS, `T["accent"]` in Python, `var(--accent)` on the web).
2. **One stylesheet template** for the whole app. To switch themes, swap the token dict and re-apply it. No per-widget `setStyleSheet`.
3. **Style by role, not by instance.** Set `objectName` (`card`, `banner`, `chip`…) or a dynamic property (`primary`, `muted`, `level`) and let the template style it.
4. **The accent shows the active thing or the primary action.** Examples: selected tab underline, focus border, primary button, slider, progress bar, selection.
5. **Status colours carry meaning only.** `ok`, `warn`, `bad` and `info` appear on chips, banner stripes, status text and validation borders.
6. **Custom painting and charts read the live `T` dict**, then redraw when the theme changes.

---

## 2. Colour tokens

| Token | Dark | Light | Used for |
|---|---|---|---|
| `bg` | `#232323` | `#f3f4f6` | window background |
| `panel` | `#2d2d2d` | `#ffffff` | menu bar, toolbar, dock titles, tab pane, status bar, menus, tooltips |
| `panel2` | `#262626` | `#f9fafb` | cards, tiles, buttons, accordion headers, table alt rows, banners |
| `input` | `#191919` | `#ffffff` | line edits, combos, text areas, tables |
| `border` | `#444444` | `#d1d5db` | all 1px borders, scrollbar handles, slider groove, splitter |
| `hover` | `#3a3a3a` | `#e5e7eb` | hover background |
| `text` | `#e6e6e6` | `#1f2937` | primary text |
| `muted` | `#b0b0b0` | `#4b5563` | secondary text, unselected tabs, table headers, disabled |
| `accent` | `#f29f43` | `#d97706` | primary / active / focus / selection |
| `accent_hover` | `#f5b26a` | `#b45309` | primary button hover |
| `accent_text` | `#1a1a1a` | `#ffffff` | text on accent and on chips |
| `ok` | `#4ade80` | `#15803d` | success / optimal |
| `warn` | `#fbbf24` | `#b45309` | warning / dry |
| `bad` | `#f87171` | `#b91c1c` | error / invalid |
| `info` | `#60a5fa` | `#1d4ed8` | info / wet |
| `toast_bg` / `toast_text` | `#e6e6e6` / `#1a1a1a` | `#1f2937` / `#ffffff` | toast (inverted surface) |

Surface order in dark mode, from darkest to lightest: `input` < `bg` < `panel2` < `panel`. The working surfaces are slightly lighter than the window background.

### Same tokens as CSS (for web projects)

```css
:root, :root[data-theme="dark"] {
  --bg:#232323; --panel:#2d2d2d; --panel2:#262626; --input:#191919;
  --border:#444444; --hover:#3a3a3a; --text:#e6e6e6; --muted:#b0b0b0;
  --accent:#f29f43; --accent-hover:#f5b26a; --accent-text:#1a1a1a;
  --ok:#4ade80; --warn:#fbbf24; --bad:#f87171; --info:#60a5fa;
  --toast-bg:#e6e6e6; --toast-text:#1a1a1a;
}
:root[data-theme="light"] {
  --bg:#f3f4f6; --panel:#ffffff; --panel2:#f9fafb; --input:#ffffff;
  --border:#d1d5db; --hover:#e5e7eb; --text:#1f2937; --muted:#4b5563;
  --accent:#d97706; --accent-hover:#b45309; --accent-text:#ffffff;
  --ok:#15803d; --warn:#b45309; --bad:#b91c1c; --info:#1d4ed8;
  --toast-bg:#1f2937; --toast-text:#ffffff;
}
@media (prefers-color-scheme: light) {
  :root:not([data-theme="dark"]) { /* copy the light block here for "Follow system" */ }
}
body { background:var(--bg); color:var(--text);
  font:13px Tahoma, "Leelawadee UI", "Noto Sans Thai", "Segoe UI", sans-serif; }
```

---

## 3. Typography

The font stack is `Tahoma, Leelawadee UI, Noto Sans Thai, Segoe UI, DejaVu Sans`. It renders Thai correctly, and matplotlib uses the same stack.

| Role | Size | Weight | Selector |
|---|---|---|---|
| Body | 13px | 400 | `QWidget` |
| Small / caption | 11px | 400 | `QLabel[small="true"]`, `#cardTitle`, `#chip` |
| Section title | 13px | 600 | `QLabel#sectionTitle`, dock titles, accordion headers |
| Heading (h1) | 16px | 600 | `QLabel#h1`, `#emptyTitle` |
| KPI value | 17px | 600 | `QLabel#cardValue` |
| Empty-state icon | 34px emoji | – | `QLabel#emptyIcon` |

There are only two weights: 400 and 600. For secondary text, use the `muted` colour rather than a smaller size.

---

## 4. Spacing, radius and sizes

- **Spacing scale:** 2 · 6 · 8 · 10 · 12 · 14 · 16 · 18 px
  - Tab page padding: `16, 14, 16, 14`, with 10px between blocks
  - Accordion: 10px outer margin and 10px between sections. Header to content is 6px.
  - Card padding: `12, 8, 12, 10`, with 2px between title and value. Cards sit in a grid with 8px gaps.
  - Tile padding: `18, 14, 18, 14`, with 6px between rows. Tiles sit 10px apart.
  - Banner padding: `12, 6, 8, 6`. The banner host has 8px margins and 6px between banners.
  - Bottom bar padding: `10, 6, 10, 6`
- **Radius:** 3px (menu item), 4px (inputs, buttons, banners), 6px (cards, toast), 8px (tiles), 9px (chip pill)
- **Borders:** always 1px `border`. There are two exceptions: the banner's 4px left stripe in a status colour, and the selected tab's 2px bottom line in `accent`.
- **Controls:** the slider groove is 4px with a 14px round handle. The progress bar is 6px. Scrollbars are 10px, with no arrow buttons and a rounded handle.
- **Icons:** Lucide-style outline SVGs at 24px viewBox, stroke 2, tinted with a token. They are rendered at 48px and shown at 2x (HiDPI).

---

## 5. Layout (application shell)

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Menu bar:  File   View(Theme ▸ Dark/Light/System, panels, Reset)  Help   │ panel
├──────────────────────────────────────────────────────────────────────────┤
│ Toolbar: [icon Open] [icon Folder] | [🔍 Search … (Ctrl+L)] | [Fetch]    │ panel
│          [Export ▾]  ←—————— expanding spacer ——————→  [Settings]        │
├──────────────┬──────────────────────────────────┬────────────────────────┤
│ LEFT DOCK    │ Banner host (dismissible stack)  │ RIGHT DOCK             │
│ ~330px       ├──────────────────────────────────┤ ~680px (min 460)       │
│ (min 300)    │                                  │ ┌Summary│Forecast│…┐   │
│ Scroll area  │      MAIN VIEW (map / canvas)    │ │ cards grid 2-col │   │
│  ▼ Section   │         stretch = 1              │ │ chips, tiles row │   │
│  ▼ Section   │                                  │ │ charts / tables  │   │
│  ▶ Section   ├──────────────────────────────────┤ └──────────────────┘   │
│ (accordion)  │ Bottom bar (slider / controls)   │                        │
├──────────────┴──────────────────────────────────┴────────────────────────┤
│ Status: message (level colour) …… [progress 160px][Cancel] labels muted  │ panel
└──────────────────────────────────────────────────────────────────────────┘
                                   ▲ toast floats bottom-centre, 56px up
```

- **Default window size:** 1640 × 940. Docks can be moved and floated. Window geometry, dock state and theme are saved in `QSettings`. View → Reset panel layout restores the default.
- **Left dock:** inputs and filters inside an accordion, wrapped in a scroll area. There is no horizontal scroll.
- **Centre:** the main visual. Banners sit above it and an optional bottom bar sits below it. The centre has no margins, so it runs edge to edge.
- **Right dock:** results as tabs with underline styling, in this order: Summary, then detail tabs, then raw data last.
- **Status bar:** the current message on the left, which stretches. On the right are progress and Cancel (visible only while busy), then permanent muted labels such as coordinates, counts and API state.

---

## 6. Components

| Component | How to make it | Look |
|---|---|---|
| Primary button | `QPushButton` + `primary=true` (`primary_button()`) | accent fill, `accent_text`, 600 |
| Secondary button | plain `QPushButton` | `panel2` fill, 1px border, hover → `hover` |
| Toolbar button | `QAction` in `QToolBar`, text beside icon | transparent, hover `hover`, checked `border` |
| Input | `QLineEdit` / `QComboBox` | `input` bg, 4px radius. Focus → accent border. `invalid=true` → `bad` border |
| Tabs | `QTabWidget` | transparent tabs, muted text. Selected → text colour + 2px accent underline |
| Accordion | `Accordion().add(title, widget)` | `panel2` header with ▼/▶ and 600 weight. Content shows below |
| Card (KPI) | `Card(title).set_value(v)` | `panel2`, 6px radius, 11px muted title over a 17px/600 value |
| Chip | `Chip(text, level)` with level `ok|warn|bad|info` | pill with 9px radius, status fill, 11px/600, `accent_text` |
| Tile | `tile()` returns `(frame, layout)` | `panel2`, 8px radius, centred content. Siblings share the same fixed size |
| Banner | `banners.post(key, text, level, action_text, action)` | `panel2` with a 4px left stripe in the level colour, optional action button and ✕ |
| Toast | `toast.popup(text, ms=2600)` | inverted colours, 6px radius, bottom-centre, hides automatically |
| Empty state | `EmptyState(icon, title, text, button_text, cb)` | centred 34px emoji, 16px title, muted text (max 360px), optional primary button |
| Status message | `set_status(text, level)` with level `ok|bad|muted` | coloured text in the status bar |
| Table | `QTableView` with alternating rows | `input` / `panel2` rows, header `panel2` + muted 600 |
| Busy | `busy(True / False)` | 6px indeterminate accent progress bar in the status bar |

**Feedback ladder:**
1. Status bar message for routine progress.
2. Toast for a quick confirmation, such as "Copied" or "Saved".
3. Banner for a problem that needs action, such as missing credentials or an error with Retry.
4. Modal dialog only for settings or credentials.

**Empty states:** every tab and view must show an `EmptyState` before data exists. It should say what to do next ("Click the map to fetch one."). A view should never be blank.

---

## 7. Charts (matplotlib)

- Figure and axes background are `T["panel"]`. Ticks and labels use `T["text"]`. Spines use `T["border"]`. Grid lines are dotted `T["muted"]` at alpha 0.45. See `style_axes(ax)`.
- The selection or "current" marker is a vertical line in `T["accent"]`.
- Messages with no data are centred text in `T["muted"]`.
- Register the chart's `redraw` in `window.theme_listeners` so it repaints when the theme changes.
- Data ramps are separate from UI tokens. For example, the moisture ramp goes from brown to blue: `#7a4b1e #b9822f #e3c27a #f1e6c0 #b9e0d6 #5fb4cf #2a7ab8 #14407f`.

---

## 8. Using it in a new project

**PySide6:** copy `ui_kit.py`, then:

```python
from ui_kit import AppWindow, Accordion, Card, Chip, EmptyState, run

class MyWindow(AppWindow):
    TITLE = "My Tool"
    LEFT_TITLE, RIGHT_TITLE = "Inputs", "Results"
    def build_left(self):   ...   # return an Accordion
    def build_center(self): ...   # return the main view widget
    def build_right(self):  ...   # return a QTabWidget
    def build_toolbar_items(self, tb):
        tb.addAction(self.act("Open...", self.open, "Ctrl+O", "folder-open"))
        self.add_search(tb, on_enter=self.search)
        self.add_spacer(tb)

run(MyWindow, org="Spex", app="MyTool")
```

- **Rebrand:** change only `accent`, `accent_hover` and `accent_text` in both `DARK` and `LIGHT`.
- **New styled widget:** give it an `objectName` and add a rule to `QSS` (or pass `extra_qss` to `apply_theme`). Use `@token@` placeholders only.
- **Other stacks** (web, Streamlit, Tkinter): use the CSS block in §2, the type scale in §3, the spacing in §4 and the layout in §5. You can also give this file to an AI assistant and ask it to follow it.


## Editor tabs and pop-ups (NX Logger)

Which window to use - editor tab, work page, Properties panel or pop-up - and how each is
laid out (path bar, collapsible sections, Apply / Refresh / Close / New... button order,
verb buttons, safe defaults) is in `docs/ui-guidelines.md` at the repo root.
