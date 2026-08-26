# Portable Chromium for Config Scanner reports

HTML diff reports need a real browser. This folder holds **Chrome for Testing**
(official Chromium build) so Open Report works on a USB stick or cabinet that
has no HTML file association.

## Layout

```
browser/
  chrome-win64/chrome.exe   (gitignored; fetch script)
  user-data/                (created on first launch; gitignored)
  VERSION.txt
```

## Fetch

From the repo root:

```powershell
python scripts/fetch_config_scanner_browser.py
```

That installs into `config-scanner/browser` and, when those folders exist, also
copies onto `H:\ConfigScanner\browser` and `D:\ConfigScanner\browser`.

Do not put the browser on a tiny recovery `D:` — only on the Config Scanner USB
or next to `LogInvestigator.exe`.
