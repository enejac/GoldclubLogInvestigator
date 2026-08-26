# Import roulette cashflow layers into Visio

Roulette equivalent of [`igt-onehand-visio-import.md`](igt-onehand-visio-import.md).

## Files

| File | Purpose |
|------|---------|
| [`igt-ruleta-layered-flow.md`](igt-ruleta-layered-flow.md) | **Main doc** — Mermaid for all 7 cash paths + slot comparison |
| [`igt-ruleta-bill-flow-report.html`](igt-ruleta-bill-flow-report.html) | **Bill flow HTML report** (open in browser) |
| [`igt-ruleta-visio-shapes.txt`](igt-ruleta-visio-shapes.txt) | Copy-paste Visio boxes (Paths A–G) |
| [`../lab/roulette/CASHFLOW-LAYERS.md`](../lab/roulette/CASHFLOW-LAYERS.md) | Operator hub, port table, sniff cheat sheet |

Slot reference PNG: generate from [`igt-onehand-layered-flow.clean.svg`](igt-onehand-layered-flow.clean.svg) via [`convert-svg-to-png.py`](convert-svg-to-png.py).  
Roulette has **no standalone SVG yet** — use Mermaid in `igt-ruleta-layered-flow.md` or build from `igt-ruleta-visio-shapes.txt`.

## Method A — Mermaid → Visio (desktop)

1. Open `igt-ruleta-layered-flow.md` in a Mermaid-capable viewer (VS Code extension, mermaid.live).
2. Export the **Master overview** or per-path diagram as PNG/SVG.
3. Visio web: **Insert → Pictures** → select the PNG.

## Method B — Editable shapes (recommended for edits)

1. Open `igt-ruleta-visio-shapes.txt`.
2. Build **Path A** (bill) top-to-bottom, then **Path B** (AFT), etc.
3. Use swimlanes: Physical | Gateway | TCP | Services | ruleta | State | Logs.
4. Paste **ARROW LABEL** text onto connectors.

Build order suggestion: Path B (AFT) first if you already know the slot diagram, then Path A (bill), C (TITO), D (Collect), E (Dallas).

## Source captures (lab .90)

| Path | Capture |
|------|---------|
| Bill physical | `aft/captures/bill_sniff_20260727_100634/dallas_capture.log` |
| Bill inject | `aft/captures/bill_inject_attempt2_captured_20260727.log` |
| HTML report | `aft/diagrams/igt-ruleta-bill-flow-report.html` |
| Protocol detail | `lab/roulette/BILL-ACCEPTOR-PROTOCOL.md` |

## Related slot files

| Slot | Roulette |
|------|----------|
| `igt-onehand-visio-shapes.txt` | `igt-ruleta-visio-shapes.txt` |
| `aft/hops/hop1`–`hop5` | Same hops for **AFT path only**; bill/TITO are roulette-only addenda |
