"""Decrypt GoldClub gcxml ruleta setup.xml to plain settings XML.

Uses cabinet_tools/roulette/Convert-GcxmlSetup.ps1 with GoldClub.Settings.dll.
UNC paths are staged locally (Assembly.LoadFrom cannot load from SMB URLs).
Plain XML is cached per process for AI Helper search and Config Scanner.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

_CACHE_LOCK = threading.RLock()
# content SHA1 -> flattened searchable text (Helper)
_CACHE: dict[str, str] = {}
# content SHA1 -> hierarchical Convert-GcxmlSetup plain XML
_RAW_CACHE: dict[str, str] = {}
# Hard failures only (missing script/DLL) — path -> monotonic expiry.
_HARD_FAILED_UNTIL: dict[str, float] = {}
_HARD_FAIL_TTL_SEC = 60.0

_LEAF_NODE = re.compile(
    r'<node\s+name="([^"]+)"\s*>([^<]*)</node>',
    re.I,
)
_SOURCE_ENCRYPTED_RE = re.compile(
    r'\bsourceEncrypted="[^"]*"',
    re.I,
)


def clear_gcxml_decrypt_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
        _RAW_CACHE.clear()
        _HARD_FAILED_UNTIL.clear()


def _sha1_file(path: Path) -> str | None:
    """Full-file SHA1 hex (lowercase), or None if unreadable."""
    try:
        digest = hashlib.sha1()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _content_cache_key(content_sha1: str) -> str:
    return f"sha1:{content_sha1}"


def _path_hard_fail_key(path: Path) -> str:
    try:
        return str(path.resolve()).lower()
    except OSError:
        return str(path).lower()


def _is_hard_failed(path_key: str) -> bool:
    with _CACHE_LOCK:
        until = _HARD_FAILED_UNTIL.get(path_key)
        if until is None:
            return False
        if time.monotonic() >= until:
            _HARD_FAILED_UNTIL.pop(path_key, None)
            return False
        return True


def _mark_hard_failed(path_key: str) -> None:
    with _CACHE_LOCK:
        _HARD_FAILED_UNTIL[path_key] = time.monotonic() + _HARD_FAIL_TTL_SEC


def invalidate_gcxml_decrypt_cache_for(path: Path) -> None:
    """Drop hard-fail marker and any content cache entry for the current file bytes."""
    path_key = _path_hard_fail_key(path)
    digest = _sha1_file(path)
    with _CACHE_LOCK:
        _HARD_FAILED_UNTIL.pop(path_key, None)
        if digest:
            key = _content_cache_key(digest)
            _CACHE.pop(key, None)
            _RAW_CACHE.pop(key, None)


def looks_like_gcxml_encrypted(raw: str | bytes) -> bool:
    """True when file looks like live ruleta setup (mangled gcxml), not plain."""
    if isinstance(raw, bytes):
        head = raw[:8000].decode("utf-8", errors="ignore")
    else:
        head = (raw or "")[:8000]
    low = head.lower()
    if "gcxml-plain-memory" in low or 'content-type="gcxml-plain"' in low:
        return False
    if 'content-type="gcxml"' in low:
        return True
    # Encrypted tags often use _x00NN_ local names or opaque tokens
    if "_x00" in low and 'xmlns="config"' in low.replace("'", '"'):
        return True
    # xml-configs mirror: <ITEM____HEX… name="HEX…">
    if "item____" in low and 'xmlns="config"' in low.replace("'", '"'):
        return True
    return False


def looks_like_gcxml_plain(raw: str | bytes) -> bool:
    """True when content is Convert-GcxmlSetup plain (node/@name) XML."""
    if isinstance(raw, bytes):
        head = raw[:4000].decode("utf-8", errors="ignore")
    else:
        head = (raw or "")[:4000]
    low = head.lower()
    return 'content-type="gcxml-plain"' in low or "gcxml-plain-memory" in low


def looks_like_encrypted_excerpt(text: str) -> bool:
    """True when Helper excerpt still looks like ciphertext (should not be shown)."""
    if not text or not text.strip():
        return False
    sample = text[:4000]
    low = sample.lower()
    if "gcxml-plain-memory" in low or 'content-type="gcxml-plain"' in low:
        return False
    if "item____" in low:
        return True
    if 'content-type="gcxml"' in low and "<node name=" not in low:
        return True
    # Long hex-ish element names without readable leaves
    if re.search(r"<ITEM_{2,}[0-9A-Fa-f]{16,}", sample):
        return True
    return False


def is_ruleta_setup_xml(path: Path) -> bool:
    """True for any ruleta ``setup.xml`` (live settings or xml-configs schema)."""
    name = path.name.lower()
    if name != "setup.xml":
        return False
    low = str(path).lower().replace("/", "\\")
    return "\\ruleta\\" in low or low.endswith("\\ruleta\\setup.xml")


def is_live_ruleta_settings_xml(path: Path) -> bool:
    """True for live BiOS settings ``…/application/ruleta/setup.xml`` only.

    Excludes ``xml-configs`` schema mirrors (EDIT_* metadata, not payoutAutoConfirm values).
    """
    if not is_ruleta_setup_xml(path):
        return False
    low = str(path).lower().replace("/", "\\")
    if "\\xml-configs\\" in low:
        return False
    return "\\application\\ruleta\\setup.xml" in low


def normalize_gcxml_plain_for_compare(
    plain_xml: str,
    *,
    source_label: str = "setup.xml",
) -> str:
    """Stabilize plain XML so temp/UNC staging paths do not change SHA1/diff."""
    label = (source_label or "setup.xml").replace('"', "")
    return _SOURCE_ENCRYPTED_RE.sub(f'sourceEncrypted="{label}"', plain_xml, count=1)


def flatten_gcxml_plain_for_search(plain_xml: str) -> str:
    """
    Turn Convert-GcxmlSetup ``<node name="…">value</node>`` leaves into
    searchable ``<logical name>value</logical name>`` lines for the Helper.
    """
    lines: list[str] = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<config content-type="gcxml-plain-memory">',
    ]
    for m in _LEAF_NODE.finditer(plain_xml or ""):
        name = m.group(1).strip()
        val = (m.group(2) or "").strip()
        if not name:
            continue
        # Keep original plain XML leaf for context too
        lines.append(f'  <node name="{name}">{val}</node>')
        # Search-friendly tag form (spaces allowed in element names in our excerpts)
        lines.append(f"  <{name}>{val}</{name}>")
    lines.append("</config>")
    return "\n".join(lines)


def _find_convert_script() -> Path | None:
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.extend(
            [
                exe_dir / "cabinet_tools" / "roulette" / "Convert-GcxmlSetup.ps1",
                exe_dir / "scripts" / "roulette" / "Convert-GcxmlSetup.ps1",
            ]
        )
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(
                Path(meipass) / "cabinet_tools" / "roulette" / "Convert-GcxmlSetup.ps1"
            )
    here = Path(__file__).resolve().parents[1]
    candidates.append(
        here / "cabinet_tools" / "roulette" / "Convert-GcxmlSetup.ps1"
    )
    for p in candidates:
        try:
            if p.is_file():
                return p
        except OSError:
            continue
    return None


def _path_is_unc(path: Path) -> bool:
    text = str(path)
    return text.startswith("\\\\") or text.startswith("//")


def _find_settings_dll(setup_path: Path) -> Path | None:
    candidates: list[Path] = [
        Path(r"C:\Goldclub\bin\lib\GoldClub.Settings.dll"),
        Path(r"C:\goldclub\bin\lib\GoldClub.Settings.dll"),
    ]
    # Walk parents for …/bin/lib next to config tree (G:\ or C:\Goldclub or UNC).
    try:
        cur = setup_path.resolve().parent
        for _ in range(10):
            candidates.append(cur / "bin" / "lib" / "GoldClub.Settings.dll")
            if cur.parent == cur:
                break
            cur = cur.parent
    except OSError:
        pass
    # Drive-root game image: G:\bin\lib or G:\Goldclub\bin\lib
    try:
        drive = setup_path.resolve().drive
        if drive:
            root = Path(drive + "\\")
            candidates.extend(
                [
                    root / "bin" / "lib" / "GoldClub.Settings.dll",
                    root / "Goldclub" / "bin" / "lib" / "GoldClub.Settings.dll",
                    root / "goldclub" / "bin" / "lib" / "GoldClub.Settings.dll",
                ]
            )
    except OSError:
        pass
    # UNC share root: \\host\c$\Goldclub\bin\lib
    text = str(setup_path)
    if text.startswith("\\\\") or text.startswith("//"):
        parts = text.replace("/", "\\").split("\\")
        # ['', '', host, share, ...]
        if len(parts) >= 4:
            share_root = Path("\\\\" + parts[2] + "\\" + parts[3])
            candidates.extend(
                [
                    share_root / "Goldclub" / "bin" / "lib" / "GoldClub.Settings.dll",
                    share_root / "goldclub" / "bin" / "lib" / "GoldClub.Settings.dll",
                    share_root / "bin" / "lib" / "GoldClub.Settings.dll",
                ]
            )
    for p in candidates:
        try:
            if p.is_file():
                return p
        except OSError:
            continue
    # Fall back to any previously cached UNC lib (snapshot paths have no bin\lib nearby).
    cached = _find_cached_settings_dll()
    if cached is not None:
        return cached
    return None


def _find_cached_settings_dll() -> Path | None:
    base = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())
    root = base / "GoldclubLogInvestigator" / "gcxml-lib"
    try:
        if not root.is_dir():
            return None
        for dll in sorted(root.glob("*/GoldClub.Settings.dll"), reverse=True):
            if dll.is_file():
                return dll
    except OSError:
        return None
    return None


def _lib_cache_dir(settings_dll: Path) -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())
    root = base / "GoldclubLogInvestigator" / "gcxml-lib"
    try:
        st = settings_dll.stat()
        key_src = f"{settings_dll}|{st.st_mtime_ns}|{st.st_size}"
    except OSError:
        key_src = str(settings_dll)
    digest = hashlib.sha1(key_src.encode("utf-8", errors="replace")).hexdigest()[:16]
    return root / digest


def ensure_local_settings_dll(settings_dll: Path) -> Path | None:
    """
    Return a local path to GoldClub.Settings.dll with sibling GoldClub*.dll present.

    UNC LoadFrom fails; stage the lib folder into a persistent local cache.
    """
    dll = Path(settings_dll)
    try:
        if not dll.is_file():
            return None
    except OSError:
        return None

    if not _path_is_unc(dll):
        return dll

    cache = _lib_cache_dir(dll)
    local_dll = cache / "GoldClub.Settings.dll"
    marker = cache / ".complete"
    if local_dll.is_file() and marker.is_file():
        return local_dll

    cache.mkdir(parents=True, exist_ok=True)
    src_dir = dll.parent
    try:
        for src in src_dir.glob("GoldClub*.dll"):
            dest = cache / src.name
            if dest.is_file():
                try:
                    src_st, dest_st = src.stat(), dest.stat()
                    if (
                        dest_st.st_size == src_st.st_size
                        and dest_st.st_mtime_ns == src_st.st_mtime_ns
                    ):
                        continue
                except OSError:
                    pass
            shutil.copy2(src, dest)
        if not local_dll.is_file():
            shutil.copy2(dll, local_dll)
        marker.write_text("ok", encoding="ascii")
    except OSError as exc:
        logger.debug("gcxml lib cache failed for %s: %s", dll, exc)
        return None
    return local_dll if local_dll.is_file() else None


def has_memory_decrypt(path: Path) -> bool:
    digest = _sha1_file(Path(path))
    if not digest:
        return False
    key = _content_cache_key(digest)
    with _CACHE_LOCK:
        return key in _CACHE or key in _RAW_CACHE


def decrypt_gcxml_setup_to_plain_xml(
    setup_path: Path,
    *,
    settings_dll: Path | None = None,
    timeout_sec: float = 120.0,
    source_label: str | None = None,
) -> str | None:
    """
    Decrypt ``setup.xml`` via Convert-GcxmlSetup. Returns hierarchical plain XML
    (``<node name="…">`` tree) or None. Does not write beside the live file.

    Cache is content-addressed (ciphertext SHA1) so config/bios duplicates share
    one decrypt and BiOS edits invalidate automatically.
    """
    path = Path(setup_path)
    hard_key = _path_hard_fail_key(path)
    if _is_hard_failed(hard_key):
        return None

    content_sha = _sha1_file(path)
    if not content_sha:
        return None
    key = _content_cache_key(content_sha)
    with _CACHE_LOCK:
        cached = _RAW_CACHE.get(key)
    if cached is not None:
        return cached

    script = _find_convert_script()
    dll_src = settings_dll or _find_settings_dll(path)
    if script is None or dll_src is None:
        logger.debug(
            "gcxml decrypt skipped (script=%s dll=%s) for %s",
            script,
            dll_src,
            path,
        )
        _mark_hard_failed(hard_key)
        return None

    local_dll = ensure_local_settings_dll(dll_src)
    if local_dll is None:
        _mark_hard_failed(hard_key)
        return None

    label = source_label or path.name
    tmp_dir: Path | None = None
    try:
        tmp_dir = Path(tempfile.mkdtemp(prefix="gcxml-decrypt-"))
        local_enc = tmp_dir / "setup.xml"
        local_plain = tmp_dir / "setup.plain.xml"
        shutil.copy2(path, local_enc)
        # Re-key from the staged bytes (TOCTOU-safe vs live path changing mid-call).
        staged_sha = _sha1_file(local_enc) or content_sha
        key = _content_cache_key(staged_sha)
        with _CACHE_LOCK:
            cached = _RAW_CACHE.get(key)
        if cached is not None:
            return cached

        cmd = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Decrypt",
            "-EncryptedPath",
            str(local_enc),
            "-PlainPath",
            str(local_plain),
            "-SettingsDll",
            str(local_dll),
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=timeout_sec,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if os.name == "nt"
                else 0,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            # Retryable — do not poison hard-fail cache.
            logger.debug("gcxml decrypt failed for %s: %s", path, exc)
            return None

        if proc.returncode != 0 or not local_plain.is_file():
            err = (proc.stderr or b"").decode("utf-8", errors="replace")[:500]
            logger.debug("gcxml decrypt exit %s for %s: %s", proc.returncode, path, err)
            return None

        raw_out = local_plain.read_text(encoding="utf-8", errors="replace")
        if not raw_out.strip():
            return None

        plain = normalize_gcxml_plain_for_compare(raw_out, source_label=label)
        with _CACHE_LOCK:
            _RAW_CACHE[key] = plain
        return plain
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def decrypt_gcxml_setup_to_memory(
    setup_path: Path,
    *,
    settings_dll: Path | None = None,
    timeout_sec: float = 120.0,
) -> str | None:
    """
    Decrypt ``setup.xml`` for Helper search. Returns flattened searchable text
    or None. Never writes a decrypted file beside the live setup.
    """
    path = Path(setup_path)
    content_sha = _sha1_file(path)
    if content_sha:
        key = _content_cache_key(content_sha)
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
        if cached is not None:
            return cached

    raw = decrypt_gcxml_setup_to_plain_xml(
        path,
        settings_dll=settings_dll,
        timeout_sec=timeout_sec,
    )
    if not raw:
        return None
    flat = flatten_gcxml_plain_for_search(raw)
    content_sha = _sha1_file(path)
    if content_sha:
        with _CACHE_LOCK:
            _CACHE[_content_cache_key(content_sha)] = flat
    return flat


def encrypt_gcxml_plain_file(
    plain_path: Path,
    encrypted_path: Path,
    *,
    settings_dll: Path | None = None,
    timeout_sec: float = 120.0,
) -> bool:
    """Re-encrypt Convert-GcxmlSetup plain XML to a live gcxml setup.xml path."""
    script = _find_convert_script()
    dll_src = settings_dll or _find_settings_dll(encrypted_path)
    if script is None or dll_src is None:
        return False
    local_dll = ensure_local_settings_dll(dll_src)
    if local_dll is None:
        return False

    # Encrypt writes EncryptedPath; stage to local then copy if dest is UNC.
    tmp_dir: Path | None = None
    try:
        dest = Path(encrypted_path)
        plain = Path(plain_path)
        enc_out = dest
        if _path_is_unc(dest):
            tmp_dir = Path(tempfile.mkdtemp(prefix="gcxml-encrypt-"))
            enc_out = tmp_dir / "setup.xml"

        cmd = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Encrypt",
            "-PlainPath",
            str(plain),
            "-EncryptedPath",
            str(enc_out),
            "-SettingsDll",
            str(local_dll),
        ]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout_sec,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if os.name == "nt"
            else 0,
        )
        if proc.returncode != 0 or not enc_out.is_file():
            err = (proc.stderr or b"").decode("utf-8", errors="replace")[:500]
            logger.debug("gcxml encrypt exit %s: %s", proc.returncode, err)
            return False
        if enc_out != dest:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(enc_out, dest)
        invalidate_gcxml_decrypt_cache_for(dest)
        return True
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("gcxml encrypt failed: %s", exc)
        return False
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def text_for_helper_search(path: Path, raw_bytes: bytes | None = None) -> str | None:
    """
    Return text used for Helper content search / excerpts.

    For encrypted ruleta setup.xml, returns in-memory decrypt (cached).
    Otherwise returns UTF-8 decode of ``raw_bytes`` / file, or None if binary.
    """
    p = Path(path)
    data = raw_bytes
    if data is None:
        try:
            data = p.read_bytes()[: 2_000_000]
        except OSError:
            return None

    if is_ruleta_setup_xml(p) and looks_like_gcxml_encrypted(data):
        plain = decrypt_gcxml_setup_to_memory(p)
        if plain:
            return plain
        # Never surface ciphertext to the Helper — skip the file.
        return None

    try:
        text = data.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return None
    if text.startswith("\ufeff"):
        text = text[1:]
    # Safety net: if we somehow still have ITEM____ ciphertext, hide it.
    if looks_like_encrypted_excerpt(text):
        plain = decrypt_gcxml_setup_to_memory(p) if is_ruleta_setup_xml(p) else None
        return plain
    return text


def plain_bytes_for_config_scan(path: Path) -> bytes | None:
    """
    For Config Scanner hash/archive: live encrypted ``application/ruleta/setup.xml``
    only → normalized UTF-8 plain XML bytes. Otherwise None (caller uses raw file).
    """
    p = Path(path)
    if not is_live_ruleta_settings_xml(p):
        return None
    try:
        head = p.read_bytes()[:8000]
    except OSError:
        return None
    if not looks_like_gcxml_encrypted(head):
        return None
    plain = decrypt_gcxml_setup_to_plain_xml(p, source_label=p.name)
    if not plain:
        return None
    return plain.encode("utf-8")


def needs_live_settings_decrypt(path: Path) -> bool:
    """True when Config Scanner must decrypt this path for meaningful hashes."""
    p = Path(path)
    if not is_live_ruleta_settings_xml(p):
        return False
    try:
        head = p.read_bytes()[:8000]
    except OSError:
        return False
    return looks_like_gcxml_encrypted(head)


def plain_text_for_compare(path: Path) -> str | None:
    """
    Return plain settings XML for compare.

    - Already plain Convert-GcxmlSetup → normalized text
    - Encrypted live ruleta setup.xml → decrypt (or None if decrypt fails)
    - Anything else → None (caller uses the file as-is)
    """
    p = Path(path)
    # Archives may be plain under the live relative path; also accept live encrypted.
    if not (is_live_ruleta_settings_xml(p) or is_ruleta_setup_xml(p)):
        return None
    # Schema mirrors: only treat as plain-compare when already decrypted on disk.
    if not is_live_ruleta_settings_xml(p):
        try:
            raw_head = p.read_bytes()[:4000]
        except OSError:
            return None
        if looks_like_gcxml_plain(raw_head):
            text = p.read_text(encoding="utf-8", errors="replace")
            if text.startswith("\ufeff"):
                text = text[1:]
            return normalize_gcxml_plain_for_compare(text, source_label=p.name)
        return None
    try:
        raw = p.read_bytes()
    except OSError:
        return None
    if looks_like_gcxml_plain(raw):
        text = raw.decode("utf-8", errors="replace")
        if text.startswith("\ufeff"):
            text = text[1:]
        return normalize_gcxml_plain_for_compare(text, source_label=p.name)
    if looks_like_gcxml_encrypted(raw):
        return decrypt_gcxml_setup_to_plain_xml(p, source_label=p.name)
    return None


@contextmanager
def plain_setup_path_for_diff(path: Path | None) -> Iterator[Path | None]:
    """
    Yield a filesystem path to plain setup XML for ET/diff.

    Encrypted ruleta setup is decrypted to a temp file. Caller must not keep
    the path after the context exits.
    """
    if path is None:
        yield None
        return
    p = Path(path)
    if not p.is_file():
        yield None
        return
    plain = plain_text_for_compare(p)
    if plain is None:
        yield p
        return
    # Already plain on disk and normalized identically → use original when possible.
    try:
        on_disk = p.read_text(encoding="utf-8", errors="replace")
        if on_disk.startswith("\ufeff"):
            on_disk = on_disk[1:]
        if looks_like_gcxml_plain(on_disk) and on_disk == plain:
            yield p
            return
    except OSError:
        pass

    tmp_dir = Path(tempfile.mkdtemp(prefix="gcxml-diff-"))
    tmp_path = tmp_dir / "setup.plain.xml"
    try:
        tmp_path.write_text(plain, encoding="utf-8")
        yield tmp_path
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def apply_value_to_live_ruleta_setup(
    live_path: Path,
    flat_path: str,
    value: str | None,
    *,
    apply_xml_fn,
) -> None:
    """
    Decrypt live ruleta setup.xml, apply one flat XML path, re-encrypt in place.

    ``apply_xml_fn(path, flat_path, value)`` mutates a plain XML file on disk.
    """
    dest = Path(live_path)
    if not is_live_ruleta_settings_xml(dest):
        apply_xml_fn(dest, flat_path, value)
        return

    try:
        head = dest.read_bytes()[:8000]
    except OSError as exc:
        raise OSError(f"Cannot read live setup.xml: {exc}") from exc

    # Live should be encrypted; if somehow already plain, edit + encrypt.
    with tempfile.TemporaryDirectory(prefix="gcxml-apply-") as tmp:
        tmp_dir = Path(tmp)
        plain_path = tmp_dir / "setup.plain.xml"
        if looks_like_gcxml_plain(head):
            plain_path.write_bytes(dest.read_bytes())
        elif looks_like_gcxml_encrypted(head):
            plain = decrypt_gcxml_setup_to_plain_xml(dest, source_label=dest.name)
            if not plain:
                raise OSError(
                    "Could not decrypt live ruleta setup.xml for Write "
                    "(GoldClub.Settings.dll / Convert-GcxmlSetup required)."
                )
            plain_path.write_text(plain, encoding="utf-8")
        else:
            apply_xml_fn(dest, flat_path, value)
            return

        apply_xml_fn(plain_path, flat_path, value)
        if not encrypt_gcxml_plain_file(plain_path, dest):
            raise OSError(
                "Applied setting to plain setup.xml but re-encrypt to live path failed."
            )
        invalidate_gcxml_decrypt_cache_for(dest)
