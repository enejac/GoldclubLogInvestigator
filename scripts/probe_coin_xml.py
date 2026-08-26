import re
from pathlib import Path
ip = "10.0.0.90"
p = Path("//%s/c$/Goldclub/var/state/GoldClub.Aurum.Services/GCMessenger/gm2au/DeviceManagerData.xml_1" % ip)
raw = p.read_text(encoding="utf-8", errors="ignore")
for pat in ["coin", "hopper", "drop", "Coin"]:
    hits = [m.group(0)[:200] for m in re.finditer(r"<[^>]*%s[^>]*>" % pat, raw, re.I)][:8]
    if hits:
        print("---", pat, len(hits))
        for h in hits:
            print(h)
