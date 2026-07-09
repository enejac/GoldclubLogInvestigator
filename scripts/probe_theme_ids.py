from pathlib import Path
import re

ip = "10.0.0.90"
p = Path("//%s/c$/Goldclub/var/state/GoldClub.Aurum.Services/GCMessenger/gm2au/DeviceManagerData.xml_1" % ip)
raw = p.read_text(encoding="utf-8", errors="ignore")
ids = sorted({m.group(1) for m in re.finditer('themeId="([^"]*)"', raw) if m.group(1)})
print("themeIds in perfMeter:", len(ids))
for x in ids:
    print(" ", x)
