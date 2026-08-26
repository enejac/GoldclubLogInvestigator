import re
from pathlib import Path
from PIL import Image
from reportlab.graphics import renderPM
from svglib.svglib import svg2rlg

SRC = Path(r"C:\Users\Ezbogar\GoldclubLogInvestigator\aft\diagrams\igt-onehand-layered-flow.svg")
OUT = Path(r"C:\Users\Ezbogar\GoldclubLogInvestigator\aft\diagrams\igt-onehand-layered-flow.png")
CLEAN = Path(r"C:\Users\Ezbogar\GoldclubLogInvestigator\aft\diagrams\igt-onehand-layered-flow.clean.svg")

text = SRC.read_bytes().decode("utf-8", errors="replace")
for bad in ("\ufffd", "\x14", "\x13"):
    text = text.replace(bad, "")
text = re.sub(r"IGT SAS Simulator[^<]*OneHand\.exe[^<]*Layered byte flow", "IGT SAS Simulator to OneHand.exe - Layered byte flow", text)
text = re.sub(r"GST20664 / GCC_ST_20664_01\)[^<]*verified", "GST20664 / GCC_ST_20664_01) - verified", text)
for old, new in {
    "Serial SAS loopback": "Serial SAS to loopback",
    "CommCtrlSAS Aurum": "CommCtrlSAS to Aurum",
    "1B017245&": "1B017245...",
    "01 72 45&": "01 72 45...",
    "qGMID1:0172450000&100000&": "qGMID1:0172450000...100000...",
    "TRANSFER REQUEST FROM SERVER FINISHED": "TRANSFER REQUEST FROM SERVER - FINISHED",
    "EGM reply leg   1B00": "EGM reply leg - 1B00",
    "~150230 ms": "~150-230 ms",
    "GCC_ST_&": "GCC_ST_...",
}.items():
    text = text.replace(old, new)
CLEAN.write_text(text, encoding="utf-8")
drawing = svg2rlg(str(CLEAN))
if drawing is None:
    raise SystemExit("svg2rlg failed")
scale = 2400 / drawing.width
drawing.width *= scale
drawing.height *= scale
drawing.scale(scale, scale)
renderPM.drawToFile(drawing, str(OUT), fmt="PNG", dpi=144)
img = Image.open(OUT)
print("PNG:", OUT)
print(" bytes:", OUT.stat().st_size)
print(" size:", img.size)
