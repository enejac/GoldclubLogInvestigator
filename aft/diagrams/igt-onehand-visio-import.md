# Import IGT → OneHand flow into Visio (web or desktop)

Visio for the web **cannot paste** Mermaid, markdown tables, or rich HTML from chat.  
Visio for the web **does NOT accept SVG** on **Insert → Pictures** (only raster/WMF formats).

## Files in this folder

| File | Purpose |
|------|---------|
| `igt-onehand-layered-flow.png` | **Use this in Visio web** — high-res raster (Insert → Pictures) |
| `igt-onehand-layered-flow.svg` | Source diagram (desktop tools / re-export only) |
| `igt-onehand-layered-flow.clean.svg` | UTF-8 cleaned source used to build the PNG |
| `igt-onehand-visio-shapes.txt` | Copy-paste text for manual editable shapes (one box at a time) |
| `convert-svg-to-png.py` | Regenerate PNG after editing the SVG |

---

## Method A — Insert the PNG (Visio web — recommended)

1. Open your Visio drawing (or **New blank drawing**).
2. **Insert** → **Pictures** → **This device** (or upload to OneDrive first).
3. Select **`igt-onehand-layered-flow.png`**.
4. Resize as needed on the page.

Supported picture types in Visio web: `.jpg`, `.jpeg`, `.png`, `.gif`, `.bmp`, `.wmf`, `.emf`, `.tif`, `.tiff` — **not `.svg`**.

---

## Method B — Build editable shapes manually

Use when you need native Visio boxes/connectors you can edit.

1. Open `igt-onehand-visio-shapes.txt`.
2. In Visio: **Insert** → **Shape** → **Basic shapes** → **Rectangle** (or flowchart box).
3. Copy **one** `[SHAPE N]` block at a time: paste **TITLE** as the first line, **BODY** as following lines into the shape.
4. Add **Connectors** between shapes (top to bottom, layer by layer).
5. Double-click each connector; paste the **ARROW LABEL** text from the txt file.
6. Optional: add six horizontal swimlanes — Physical | Bridge | TCP | Aurum | OneHand | Logs.

Build order: Shape 1 → 2 → 3 → 4 → 5 → (6 optional) → 7 → 8 → 9 → 10 → 11 → 12–15.

---

## Method C — Desktop Visio (optional)

Desktop Visio can **File → Open** the `.svg` for better vector edit/un-group support than the web app. For web sharing, still prefer the **PNG**.

---

## Regenerate the PNG after SVG edits

```powershell
cd C:\Users\Ezbogar\GoldclubLogInvestigator\aft\diagrams
py -3 -m pip install svglib reportlab pillow rlPyCairo pycairo
py -3 convert-svg-to-png.py
```

---

## Why chat paste failed

Visio web only accepts:

- Plain text into shapes (one field at a time)
- **Raster/WMF pictures** via Insert (not SVG)
- Native Visio formats (`.vsdx`)

It does **not** accept Mermaid code blocks, markdown, or clipboard HTML from the browser chat.

---

## Source documentation

- `aft/hops/README.md` — hop chain overview
- `aft/hops/hop1` … `hop4` — per-layer detail
- `aft/protocol-raw-traffic.md` — example `qGMID1:` hex
- `aft/RUNBOOK.md` — WinDivert inject at Hop 2
