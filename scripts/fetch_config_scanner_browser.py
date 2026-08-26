#!/usr/bin/env python3
"""Download Chrome for Testing into config-scanner/browser (portable HTML viewer)."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

LKGR_URL = (
    "https://googlechromelabs.github.io/chrome-for-testing/"
    "last-known-good-versions-with-downloads.json"
)
FALLBACK_VERSION = "152.0.7977.42"
FALLBACK_ZIP = (
    "https://storage.googleapis.com/chrome-for-testing-public/"
    f"{FALLBACK_VERSION}/win64/chrome-win64.zip"
)
MIN_FREE_BYTES = 400 * 1024 * 1024


def _default_dest() -> Path:
    from config_scanner.paths import _resolve_tool_root

    return _resolve_tool_root() / "browser"


def _usb_dests() -> list[Path]:
    dests: list[Path] = []
    for letter in ("H", "D"):
        root = Path(f"{letter}:/ConfigScanner")
        try:
            if root.is_dir():
                dests.append(root / "browser")
        except OSError:
            continue
    return dests


def _chrome_exe(dest: Path) -> Path:
    return dest / "chrome-win64" / "chrome.exe"


def _free_bytes(path: Path) -> int:
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    if not probe.exists():
        probe = Path(path.anchor or path)
    return shutil.disk_usage(probe).free


def _lookup_stable_zip() -> tuple[str, str]:
    try:
        with urllib.request.urlopen(LKGR_URL, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
        stable = data["channels"]["Stable"]
        version = str(stable["version"])
        for item in stable["downloads"]["chrome"]:
            if item.get("platform") == "win64":
                return version, str(item["url"])
    except (OSError, KeyError, ValueError, json.JSONDecodeError):
        pass
    return FALLBACK_VERSION, FALLBACK_ZIP


def _download(url: str, dest_zip: Path) -> None:
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest_zip.with_suffix(".zip.tmp")
    if tmp.is_file():
        tmp.unlink()
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if curl:
        completed = subprocess.run(
            [curl, "-L", "--fail", "--retry", "3", "-o", str(tmp), url],
            check=False,
        )
        if completed.returncode != 0:
            if tmp.is_file():
                tmp.unlink()
            raise RuntimeError(f"curl failed with exit {completed.returncode}")
    else:
        with urllib.request.urlopen(url, timeout=120) as response, tmp.open("wb") as handle:
            shutil.copyfileobj(response, handle)
    tmp.replace(dest_zip)


def _safe_extract(zip_path: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    dest_resolved = dest.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            target = (dest / info.filename).resolve()
            if dest_resolved not in target.parents and target != dest_resolved:
                raise RuntimeError(f"unsafe zip path: {info.filename}")
        archive.extractall(dest)


def _install_into(dest: Path, zip_path: Path, version: str, *, force: bool) -> Path:
    exe = _chrome_exe(dest)
    if exe.is_file() and not force:
        print(f"Already present: {exe}")
        return exe
    free = _free_bytes(dest)
    if free < MIN_FREE_BYTES:
        raise RuntimeError(
            f"Not enough free space on {dest.anchor or dest} "
            f"({free // (1024 * 1024)} MB free; need ~400 MB)."
        )
    chrome_dir = dest / "chrome-win64"
    if chrome_dir.exists():
        shutil.rmtree(chrome_dir)
    print(f"Extracting to {dest} ...")
    _safe_extract(zip_path, dest)
    if not exe.is_file():
        raise RuntimeError(f"chrome.exe missing after extract: {exe}")
    (dest / "VERSION.txt").write_text(
        f"Chrome for Testing {version}\n{exe}\n",
        encoding="utf-8",
    )
    print(f"Installed: {exe}")
    return exe


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dest",
        type=Path,
        help="browser folder (default: config-scanner/browser next to the tool root)",
    )
    parser.add_argument(
        "--also-usb",
        action="store_true",
        default=True,
        help="also copy into H:\\ConfigScanner\\browser and D:\\ConfigScanner\\browser when those folders exist",
    )
    parser.add_argument("--no-usb", action="store_true", help="do not copy onto H: or D:")
    parser.add_argument("--force", action="store_true", help="re-download even if chrome.exe is present")
    args = parser.parse_args()

    dest = (args.dest or _default_dest()).resolve()
    exe = _chrome_exe(dest)
    if exe.is_file() and not args.force:
        print(f"Already present: {exe}")
        zip_path = None
        version = "existing"
        if (dest / "VERSION.txt").is_file():
            version = (dest / "VERSION.txt").read_text(encoding="utf-8").splitlines()[0]
    else:
        version, url = _lookup_stable_zip()
        print(f"Chrome for Testing {version}")
        print(f"  {url}")
        dest.mkdir(parents=True, exist_ok=True)
        zip_path = dest / f"chrome-win64-{version}.zip"
        if args.force and zip_path.is_file():
            zip_path.unlink()
        if not zip_path.is_file():
            print(f"Downloading (~160 MB) to {zip_path} ...")
            _download(url, zip_path)
        _install_into(dest, zip_path, version, force=True)
        if zip_path.is_file():
            zip_path.unlink()
            print("Removed zip after extract.")

    if not args.no_usb and args.also_usb:
        for extra in _usb_dests():
            extra_resolved = extra.resolve()
            if extra_resolved == dest:
                continue
            extra_exe = _chrome_exe(extra_resolved)
            if extra_exe.is_file() and not args.force:
                print(f"USB already has browser: {extra_exe}")
                continue
            try:
                if _free_bytes(extra_resolved) < MIN_FREE_BYTES:
                    print(
                        f"Skip {extra_resolved}: not enough free space "
                        f"({_free_bytes(extra_resolved) // (1024 * 1024)} MB)."
                    )
                    continue
                extra_resolved.mkdir(parents=True, exist_ok=True)
                src_chrome = dest / "chrome-win64"
                tgt_chrome = extra_resolved / "chrome-win64"
                if not src_chrome.is_dir():
                    continue
                if tgt_chrome.exists():
                    shutil.rmtree(tgt_chrome)
                print(f"Copying chrome-win64 -> {tgt_chrome}")
                shutil.copytree(src_chrome, tgt_chrome)
                (extra_resolved / "VERSION.txt").write_text(
                    f"{version}\n{extra_exe}\n",
                    encoding="utf-8",
                )
                print(f"Installed: {extra_exe}")
            except OSError as exc:
                print(f"Skip {extra_resolved}: {exc}", file=sys.stderr)

    print(f"Ready: {_chrome_exe(dest)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
