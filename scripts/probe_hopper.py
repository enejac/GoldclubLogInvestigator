from pathlib import Path
import xml.etree.ElementTree as ET
ip = "10.0.0.90"
p = Path("//%s/c$/Goldclub/var/state/GoldClub.Aurum.Services/GCMessenger/gm2au/DeviceManagerData.xml_1" % ip)
raw = p.read_text(encoding="utf-8", errors="ignore")
root = ET.fromstring(raw[raw.find("<?xml"):])
ln = lambda s: (s or "").split("}")[-1].split(":")[-1].strip()

def walk(e, path=""):
    tag = ln(e.tag).lower()
    p = f"{path}/{tag}" if path else tag
    if tag in ("hopperdata", "coin", "meter", "transmeter", "devicemanagerdata") or "coin" in tag or "hopper" in tag or "drop" in tag:
        attrs = {ln(k): v for k,v in (e.attrib or {}).items()}
        if attrs or (e.text and e.text.strip()):
            print(p, attrs, (e.text or "").strip()[:80])
    for c in list(e):
        walk(c, p)
walk(root)
