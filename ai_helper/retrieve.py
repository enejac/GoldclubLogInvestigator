"""Local file search + excerpt extraction for the offline AI Helper."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
import fnmatch
import os
import re

# Max files walked / returned and excerpt size caps (keep prompts small).
MAX_WALK_FILES = 8_000
MAX_HITS = 8
MAX_EXCERPT_CHARS = 2_400
MAX_EXCERPT_LINES = 80
MAX_CONTENT_SCAN_FILES = 600
MAX_CONTENT_READ_BYTES = 200_000

_CONTENT_EXTS = frozenset(
    {".xml", ".json", ".config", ".ini", ".txt", ".cfg", ".ps1", ".bat", ".cmd"}
)

# Skip heavy / irrelevant trees under a GoldClub root.
_SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        "__pycache__",
        "node_modules",
        "snapshots",
        "reports",
        ".pytest_cache",
        "build",
        "dist",
        "models",
        "bin",
        "obj",
        "packages",
        "nuget",
        "node",
        "cache",
        "temp",
        "tmp",
    }
)

# When the root looks like a full GoldClub install, only walk these subtrees.
_NARROW_SUBTREES = (
    "config",
    "slot/themes",
    "slot/Themes",
    "data",
    "aurum",
)

# Question keyword → preferred relative glob patterns (GoldClub layout).
PATH_ALIASES: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (
        # SAS serial / COM port lives in CommCtrlSAS CommControler.ini (not Aurum SASControler).
        re.compile(
            r"\b(sas\s*com|com\s*port|serial\s*port|commcontroler|commcontroller|"
            r"commctrlsas|comm\s*ctrl\s*sas|mux|"
            r"which\s+com|on\s+which\s+com)\b"
            r"|\bsas\b.*\bcom\b|\bcom\b.*\bsas\b|\bcom\s*\d+\b",
            re.I,
        ),
        (
            "**/CommCtrlSAS/CommControler.ini",
            "**/CommCtrlSas/CommControler.ini",
            "**/CommCtrlSAS/**/CommControler.ini",
            "**/CommControler.ini",
            "**/CommCtrlSAS/**",
        ),
    ),
    (
        re.compile(
            r"\b(sas\s*controller|sascontroler|sas\s*control)\b",
            re.I,
        ),
        (
            "**/config/etc/application/aurum/SASControler*/**",
            "**/config/etc/application/aurum/SASControler*",
            "**/aurum/**/SASControler*",
            "**/SASControler*",
        ),
    ),
    (
        re.compile(r"\baurum\s*setup\b|\bAurumSetup\.xml\b", re.I),
        (
            "**/AurumSetup.xml",
            "**/aurum/**/AurumSetup.xml",
            "**/config/**/AurumSetup.xml",
        ),
    ),
    (
        re.compile(
            r"\bmgconfig\b|\binactivity\b|\bInactivitySecondsToGameSelector\b|\bgame\s*selector\b"
            r"|\bcashoutbuttonmode\b|\bcashout\s*button\b",
            re.I,
        ),
        (
            "**/slot/themes/mgconfig.xml",
            "**/themes/mgconfig.xml",
            "**/mgconfig.xml",
        ),
    ),
    (
        re.compile(r"\bcommctrl\b|\bsas\s*bridge\b", re.I),
        (
            "**/CommCtrlSAS/CommControler.ini",
            "**/CommControler.ini",
            "**/CommCtrlSAS/**",
            "**/CommCtrl*",
            "**/var/state/**/GCMessenger/**",
        ),
    ),
    (
        re.compile(
            r"\b("
            r"roulette\s+error|error\s+list|error\s+screen|error\s+window|"
            r"trial\s+expired|trial\s+error|TRIAL\s+DISPLAYED|"
            r"alegro\s+error"
            r")\b"
            r"|\berror\s*[#:]?\s*\d{1,3}\b.*\b(roulette|ruleta|trial)\b"
            r"|\b(roulette|ruleta|trial)\b.*\berror\s*[#:]?\s*\d{1,3}\b",
            re.I,
        ),
        (
            "**/roulette_error_catalog.json",
            "**/data/roulette_error_catalog.json",
            "**/known_issues.json",
        ),
    ),
    (
        # Roulette gameplay / credit-on-board / wager options live in ruleta setup.xml
        # (gcxml), not Aurum options.xml.
        re.compile(
            r"\b("
            r"no\s*credit|no\s*game|nogame|nocredit|"
            r"credit\s*on\s*(board|position)|credits?\s+on\s+(board|position)|"
            r"without\s*credit|residual\s*credit|minimal\s*wager|"
            r"wager\s*amount|admin\s*menu.*credit|lock\s*in\s*admin|"
            r"setup\.decrypted|ruleta\s*setup|roulette\s*setup|"
            r"disable\s+.*\b(game|credit)|enable\s+.*\b(game|credit)"
            r")\b"
            r"|\b(option|setting)\b.*\b(credit|wager|board|game)\b"
            r"|\b(credit|wager|board)\b.*\b(option|setting|disable|enable|lock)\b",
            re.I,
        ),
        (
            "**/config/etc/application/ruleta/setup.xml",
            "**/ruleta/setup.xml",
            "**/setup.decrypted.xml",
            "**/config/etc/application/ruleta/godot.xml",
            "**/ruleta/godot.xml",
            "**/mgconfig.xml",
        ),
    ),
    (
        # Roulette main payout / ticket-printer payout method (NOT serialport layout).
        re.compile(
            r"\b("
            r"outputtype|userpayout|pay\s*system|payout\s*method|payout\s*type|"
            r"main\s*payout|ticket\s*printer\s*payout|ticket\s*payout|"
            r"tito\s*payout|cashout\s*method|payoutautoconfirm|"
            r"payout\s*auto\s*confirm"
            r")\b"
            r"|\b(ticket|tito|printer)\b.*\b(payout|cashout|pay\s*out)\b"
            r"|\b(payout|cashout|pay\s*out)\b.*\b(ticket|tito|printer|method|type)\b",
            re.I,
        ),
        (
            "**/config/etc/application/ruleta/setup.xml",
            "**/ruleta/setup.xml",
            "**/HW/driverssetup/configuration.xml",
            "**/driverssetup/configuration.xml",
            "**/mgconfig.xml",
        ),
    ),
    (
        re.compile(
            r"\b("
            r"tito\s*driver|driverssetup|ticket\s*printer\s*driver|"
            r"futurelogic|psa66|endpointaddress|tito0"
            r")\b"
            r"|\btito\b.*\b(driver|endpoint|30400)\b",
            re.I,
        ),
        (
            "**/HW/driverssetup/configuration.xml",
            "**/driverssetup/configuration.xml",
            "**/config/etc/application/HW/**",
        ),
    ),
)

# Paths that are pointers/wrappers — demote vs real config.
_DEMOTE_PATH_FRAGMENTS = (
    "gcbackup",
    "service.d",
    "maintenance",
    "xyntservice",
    "xyntservice2",
    "\\backup\\",
    "/backup/",
    "logviewer",
    "financialreport",
    # Prefer live application\\ruleta\\setup.xml over the xml-configs mirror.
    "\\xml-configs\\",
    "/xml-configs/",
)

# Payout / cashout questions must not surface the COM name map as "the answer".
_PAYOUT_METHOD_QUESTION = re.compile(
    r"\b("
    r"payout\s*method|payout\s*type|main\s*payout|outputtype|userpayout|"
    r"pay\s*system|ticket\s*printer\s*payout|ticket\s*payout|tito\s*payout|"
    r"cashout\s*method|cashoutbuttonmode|payoutautoconfirm"
    r")\b"
    r"|\b(ticket|tito|printer)\b.*\b(payout|cashout)\b"
    r"|\b(payout|cashout)\b.*\b(ticket|tito|printer|method|type|configure)\b",
    re.I,
)

_SERIALPORT_COM_QUESTION = re.compile(
    r"\b("
    r"serialport|layout\.json|locations\.json|which\s+com|com\s*port|"
    r"ticket\s*printer\s*com|uart|leds\s*com"
    r")\b",
    re.I,
)

_STOP_TOKENS = frozenset(
    {
        "where",
        "what",
        "which",
        "find",
        "show",
        "locate",
        "located",
        "config",
        "configuration",
        "setting",
        "settings",
        "file",
        "files",
        "path",
        "xml",
        "the",
        "and",
        "for",
        "from",
        "with",
        "please",
        "helper",
        "check",
        "option",  # alone matches options.xml noise; keep with credit/game via aliases
        "options",
    }
)

_GAMEPLAY_OPTION_QUESTION = re.compile(
    r"\b("
    r"no\s*credit|no\s*game|credit|wager|board|disable|enable|lock|"
    r"option|setting|residual|admin\s*menu"
    r")\b",
    re.I,
)

_AURUM_OPTIONS_NOISE_QUESTION = re.compile(
    r"\b(seedprovider|aurum\s*options|checkendpoint|workerpool)\b",
    re.I,
)


@dataclass(frozen=True)
class RetrievalHit:
    path: str
    excerpt: str
    score: float
    reason: str = ""


@dataclass
class RootsDiagnosis:
    """Resolved search roots plus human notes about skipped / missing paths."""

    roots: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    attempted: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.roots)


def diagnose_roots(candidates: list[str] | tuple[str, ...] | None) -> RootsDiagnosis:
    """
    Resolve which candidate paths exist and explain failures.

    Used by the GUI when opening AI Helper so empty roots are not silent.
    """
    diag = RootsDiagnosis()
    seen: set[str] = set()
    for raw in candidates or ():
        text = (raw or "").strip()
        if not text:
            continue
        diag.attempted.append(text)
        try:
            p = Path(text).expanduser()
        except (OSError, ValueError) as exc:
            diag.notes.append(f"Invalid path {text!r}: {exc}")
            continue
        try:
            exists = p.exists()
        except OSError as exc:
            diag.notes.append(f"Cannot access {text}: {exc}")
            continue
        if not exists:
            hint = ""
            if text.startswith("\\\\"):
                hint = " (UNC — check network/cmdkey GOLD-CLUB\\test)"
            elif re.match(r"^[A-Za-z]:\\?$", text.rstrip("\\")):
                hint = " (drive letter not mounted or empty)"
            diag.notes.append(f"Not found: {text}{hint}")
            continue
        if not p.is_dir():
            # Allow a single file as a trivial root (its parent).
            if p.is_file():
                p = p.parent
            else:
                diag.notes.append(f"Not a directory: {text}")
                continue
        try:
            key = str(p.resolve()).lower()
            resolved = str(p.resolve())
        except OSError:
            key = str(p).lower()
            resolved = str(p)
        if key in seen:
            continue
        seen.add(key)
        diag.roots.append(resolved)
    if not diag.roots and not diag.notes and not diag.attempted:
        diag.notes.append(
            "No log path or Config Scanner target set. "
            "Enter a GoldClub folder in the main window path field, "
            "or set Config Scanner game drive (e.g. D:\\ or \\\\10.0.0.90\\c$\\Goldclub)."
        )
    elif not diag.roots and diag.attempted:
        diag.notes.append(
            "None of the configured paths are reachable. "
            "Fix the main-window log path or Config Scanner target, then reopen AI Helper."
        )
    return diag


def _looks_like_goldclub_install(root: Path) -> bool:
    try:
        return (root / "config").is_dir() or (root / "slot").is_dir() or (
            root / "Goldclub"
        ).is_dir() or (root / "goldclub").is_dir()
    except OSError:
        return False


def _walk_targets(root: Path) -> list[Path]:
    """Prefer narrow config/theme subtrees under a full GoldClub install."""
    try:
        if not _looks_like_goldclub_install(root):
            return [root]
    except OSError:
        return [root]

    targets: list[Path] = []
    for rel in _NARROW_SUBTREES:
        cand = root.joinpath(*rel.split("/"))
        try:
            if cand.is_dir():
                targets.append(cand)
        except OSError:
            continue
    # Nested Goldclub\ on a drive letter
    for name in ("Goldclub", "goldclub"):
        nested = root / name
        try:
            if nested.is_dir():
                for rel in _NARROW_SUBTREES:
                    cand = nested.joinpath(*rel.split("/"))
                    if cand.is_dir():
                        targets.append(cand)
        except OSError:
            continue
    return targets or [root]


def _basename_alias_match(filename: str, pattern: str) -> bool:
    """
    Match file basename against the last segment of an alias pattern.

    Rejects wildcard-only basenames like ``*.xml`` (would match every XML file).
    """
    base = Path(pattern.replace("\\", "/")).name
    if not base or base in ("*", "*.*", "*.xml", "*.json"):
        return False
    # Require at least one literal alphanumeric char in the pattern stem
    stem = base.split("*", 1)[0].split("?", 1)[0]
    if not any(c.isalnum() for c in stem):
        return False
    return fnmatch.fnmatch(filename.lower(), base.lower())


def _glob_match_path(path: str, pattern: str) -> bool:
    """Case-insensitive path glob; ``**`` = any dirs, ``*`` = within one segment."""
    path = path.replace("\\", "/").strip("/")
    pat = pattern.replace("\\", "/").strip("/")
    while pat.startswith("**/"):
        pat = pat[3:]
    dir_prefix = False
    if pat.endswith("/**"):
        pat = pat[:-3]
        dir_prefix = True

    segs = path.split("/") if path else []
    psegs = [p for p in pat.split("/") if p]

    def match_from(si: int, pi: int) -> bool:
        while pi < len(psegs):
            if psegs[pi] == "**":
                if pi == len(psegs) - 1:
                    return True
                for k in range(si, len(segs) + 1):
                    if match_from(k, pi + 1):
                        return True
                return False
            if si >= len(segs):
                return False
            if not fnmatch.fnmatch(segs[si].lower(), psegs[pi].lower()):
                return False
            si += 1
            pi += 1
        if dir_prefix:
            # Pattern matched a directory prefix; file may be deeper.
            return si <= len(segs)
        # Matched a directory-like last segment (e.g. SASControler*) — allow files beneath.
        if si < len(segs) and psegs and ("*" in psegs[-1] or "?" in psegs[-1]):
            return True
        return si == len(segs)

    # Leading ** is implied so pattern can match any depth.
    for start in range(len(segs) + 1):
        if match_from(start, 0):
            return True
    return False


def _path_matches_glob(rel_posix: str, pattern: str) -> bool:
    """Match a relative path against a ``**``-style glob (case-insensitive on Windows)."""
    return _glob_match_path(rel_posix, pattern)


def _iter_files_under(
    root: Path,
    *,
    cancel_check: Callable[[], bool] | None = None,
    limit: int = MAX_WALK_FILES,
) -> list[Path]:
    files: list[Path] = []
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            if cancel_check and cancel_check():
                return files
            dirnames[:] = [
                d
                for d in dirnames
                if d.lower() not in {x.lower() for x in _SKIP_DIR_NAMES}
            ]
            for name in filenames:
                files.append(Path(dirpath) / name)
                if len(files) >= limit:
                    return files
    except OSError:
        return files
    return files


def collect_files(
    roots: list[Path],
    *,
    cancel_check: Callable[[], bool] | None = None,
) -> list[tuple[Path, Path]]:
    """
    Walk roots once (narrowing GoldClub installs).

    Returns list of ``(search_root, file_path)`` for relative-path matching.
    """
    out: list[tuple[Path, Path]] = []
    seen: set[str] = set()
    for root in roots:
        if cancel_check and cancel_check():
            break
        for target in _walk_targets(root):
            if cancel_check and cancel_check():
                break
            remaining = MAX_WALK_FILES - len(out)
            if remaining <= 0:
                return out
            for path in _iter_files_under(
                target, cancel_check=cancel_check, limit=remaining
            ):
                try:
                    key = str(path.resolve()).lower()
                except OSError:
                    key = str(path).lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append((root, path))
                if len(out) >= MAX_WALK_FILES:
                    return out
    return out


def read_excerpt(
    path: Path,
    *,
    max_chars: int = MAX_EXCERPT_CHARS,
    max_lines: int = MAX_EXCERPT_LINES,
    needle: str | None = None,
) -> str:
    """Read a bounded text excerpt; optionally center on first needle match."""
    from ai_helper.gcxml_decrypt import text_for_helper_search

    try:
        raw_bytes = path.read_bytes()[: max(max_chars * 4, MAX_CONTENT_READ_BYTES)]
    except OSError:
        return ""
    if _looks_binary(raw_bytes):
        # Encrypted gcxml often looks binary-ish; still try in-memory decrypt.
        from ai_helper.gcxml_decrypt import is_ruleta_setup_xml, looks_like_gcxml_encrypted

        if not (is_ruleta_setup_xml(path) and looks_like_gcxml_encrypted(raw_bytes)):
            return ""

    raw = text_for_helper_search(path, raw_bytes)
    if not raw:
        return ""

    lines = raw.splitlines()
    if needle:
        low = needle.lower()
        idx = next((i for i, ln in enumerate(lines) if low in ln.lower()), None)
        if idx is not None:
            start = max(0, idx - 8)
            chunk = lines[start : start + max_lines]
            text = "\n".join(chunk)
            if len(text) > max_chars:
                return text[:max_chars].rstrip() + "\n…"
            return text

    chunk = lines[:max_lines]
    text = "\n".join(chunk)
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "\n…"
    return text


def _looks_binary(data: bytes) -> bool:
    if not data:
        return True
    sample = data[:4096]
    if b"\x00" in sample:
        return True
    # High ratio of non-text control bytes (excluding tab/lf/cr)
    weird = sum(1 for b in sample if b < 9 or (13 < b < 32) or b == 127)
    return (weird / max(len(sample), 1)) > 0.30


def _path_demote(path: Path, question: str = "") -> float:
    low = str(path).lower().replace("/", "\\")
    name = path.name.lower()
    demote = 0.0
    for frag in _DEMOTE_PATH_FRAGMENTS:
        if frag.lower().replace("/", "\\") in low:
            demote -= 12.0
    q = (question or "").strip()
    gameplay = bool(q and _GAMEPLAY_OPTION_QUESTION.search(q))
    aurum_noise_ok = bool(q and _AURUM_OPTIONS_NOISE_QUESTION.search(q))
    if name == "options.xml" and gameplay and not aurum_noise_ok:
        if "\\aurum\\" in low or "/aurum/" in low:
            demote -= 28.0
        else:
            demote -= 10.0
    # Payout-method questions: bury COM layout / UI chrome that mention "Ticket".
    payoutish = bool(q and _PAYOUT_METHOD_QUESTION.search(q))
    serial_ok = bool(q and _SERIALPORT_COM_QUESTION.search(q))
    if payoutish and not serial_ok:
        if "\\serialport\\" in low or "/serialport/" in low:
            demote -= 35.0
        if name in {"layout.json", "locations.json"}:
            demote -= 40.0
        if "\\xml-configs\\" in low or "/xml-configs/" in low:
            demote -= 40.0
        if "\\setup.2\\" in low or "/setup.2/" in low:
            demote -= 18.0
        if "\\externalapps\\" in low or "/externalapps/" in low:
            demote -= 25.0
        if name in {"main_layout.xml", "mainmenu.xml", "oticket.xml"}:
            demote -= 15.0
        if "ticket0.dat" in low or "\\tickets\\" in low or "/tickets/" in low:
            demote -= 20.0
    return demote


def _is_setup_option_question(question: str) -> bool:
    q = (question or "").strip()
    if not q:
        return False
    if _PAYOUT_METHOD_QUESTION.search(q):
        return True
    return bool(
        re.search(
            r"\b("
            r"no\s*credit|no\s*game|nogame|nocredit|credit|wager|board|"
            r"option|setting|lock|residual|admin\s*menu|"
            r"setup\.decrypted|ruleta\s*setup|roulette\s*setup|minimal\s*wager|"
            r"payout|cashout|handpay|tito"
            r")\b",
            q,
            re.I,
        )
    )


def _path_boost(path: Path, question: str) -> float:
    """Boost real COM/serial and roulette setup config files for matching questions."""
    q = question.lower()
    name = path.name.lower()
    low = str(path).lower().replace("/", "\\")
    boost = 0.0
    comish = bool(
        re.search(
            r"\b(com\s*port|sas\s*com|serial\s*port|commcontroler|commctrlsas|mux|"
            r"which\s+com|on\s+which\s+com)\b"
            r"|\bsas\b.*\bcom\b|\bcom\b.*\bsas\b|\bcom\s*\d+\b",
            q,
            re.I,
        )
        or ("com" in q and "port" in q)
    )
    if name == "commcontroler.ini" or name == "commcontroller.ini":
        boost += 25.0 if comish else 12.0
    if "commctrlsas" in low and name.endswith(".ini"):
        boost += 18.0 if comish else 3.0
    elif comish and ("\\commctrl\\" in low or "/commctrl/" in low) and name.endswith(".ini"):
        # Hardware CommCtrl — useful secondary, keep below SAS
        boost += 6.0
    if comish and name.endswith(".ini"):
        boost += 4.0
    if comish and ("logviewer" in low or "financialreport" in low or name == "commands.list"):
        boost -= 20.0
    if name.endswith(".ini"):
        boost += 1.0

    setupish = _is_setup_option_question(question)
    if setupish:
        if name == "setup.decrypted.xml":
            boost += 26.0
        elif name == "setup.xml" and ("\\ruleta\\" in low or "/ruleta/" in low):
            boost += 24.0
        elif name == "setup.xml":
            boost += 14.0
        elif name == "godot.xml" and ("\\ruleta\\" in low or "/ruleta/" in low):
            boost += 8.0
        elif name == "mgconfig.xml":
            boost += 6.0
    payoutish = bool(_PAYOUT_METHOD_QUESTION.search(question))
    if payoutish:
        if name == "setup.xml" and ("\\ruleta\\" in low or "/ruleta/" in low):
            boost += 30.0
        if name == "configuration.xml" and (
            "\\driverssetup\\" in low or "/driverssetup/" in low
        ):
            boost += 22.0
        if name == "mgconfig.xml" and re.search(
            r"cashoutbuttonmode|cashout\s*button|handpay", question, re.I
        ):
            boost += 20.0
    return boost


def _score_filename(name: str, question: str) -> float:
    q = question.lower()
    n = name.lower()
    score = 0.0
    tokens = [
        t
        for t in re.split(r"[^a-z0-9]+", q)
        if len(t) >= 3 and t not in _STOP_TOKENS
    ]
    for t in tokens:
        if t in n:
            score += 2.0
    if "sas" in q and "sas" in n:
        score += 3.0
    if "control" in q and ("control" in n or "controler" in n):
        score += 2.5
    if "com" in q and ("com" in n or "comm" in n):
        score += 3.0
    if "port" in q and ("port" in n or n.endswith(".ini")):
        score += 2.0
    if n.endswith(".ini"):
        score += 1.5
    if n.endswith(".xml"):
        score += 0.5
    return score


def _alias_patterns(question: str) -> list[str]:
    out: list[str] = []
    for rx, patterns in PATH_ALIASES:
        if rx.search(question):
            out.extend(patterns)
    return out


def _content_needles(question: str) -> list[str]:
    """Distinctive tokens / phrases suitable for content grep."""
    found: list[str] = []
    seen: set[str] = set()

    def _add(s: str) -> None:
        t = (s or "").strip()
        if len(t) < 4:
            return
        key = t.lower()
        if key in seen or key in _STOP_TOKENS:
            return
        seen.add(key)
        found.append(t)

    q = (question or "").strip()
    ql = q.lower()

    # Multi-word phrases first (better for XML tag matching).
    for phrase in (
        "no credit",
        "no game",
        "credits on position",
        "credit on board",
        "credits on board",
        "no credits on",
        "when no credits",
        "minimal wager",
        "residual credits",
        "ignore min wager",
        "lock in admin menu",
        "admin menu only when no credits",
        "outputtype",
        "userpayout",
        "pay system",
        "payoutautoconfirm",
        "cashoutbuttonmode",
        "aliasname",
    ):
        if phrase in ql:
            _add(phrase)

    if _PAYOUT_METHOD_QUESTION.search(q):
        for tok in (
            "outputtype",
            "userpayout",
            "pay system",
            "payoutAutoConfirm",
            "CashoutButtonMode",
            "tito",
            "endpointaddress",
        ):
            _add(tok)

    # Bigrams from question tokens (skip stop words).
    tokens = [
        t
        for t in re.split(r"[^a-z0-9]+", ql)
        if len(t) >= 3 and t not in _STOP_TOKENS
    ]
    for a, b in zip(tokens, tokens[1:]):
        _add(f"{a} {b}")

    for m in re.findall(r"['\"]([^'\"]{4,80})['\"]", question):
        _add(m)
    for m in re.findall(r"\b[A-Z][a-zA-Z0-9_]{5,80}\b", question):
        _add(m)
    for m in re.findall(r"\b[A-Za-z][A-Za-z0-9_]{9,80}\b", question):
        if m.lower() not in _STOP_TOKENS:
            _add(m)
    # CamelCase glued words without spaces
    for m in re.findall(r"\b\w*[a-z][A-Z]\w+\b", question):
        _add(m)

    # Short domain tokens when the question is clearly about options / credit / lock.
    if _is_setup_option_question(q):
        for tok in ("credit", "wager", "credits", "nogame", "nocredit"):
            if tok in ql or tok.rstrip("s") in tokens:
                _add(tok if tok != "credits" else "credit")

    return found


def _primary_needle(question: str, content_needles: list[str] | None = None) -> str | None:
    if content_needles:
        # Prefer multi-word phrases for excerpt centering.
        multi = [n for n in content_needles if " " in n]
        if multi:
            return multi[0]
        return content_needles[0]
    q = question.lower()
    if "com" in q or "serial" in q or "baud" in q:
        return "Port"
    if "no credit" in q or "nocredit" in q:
        return "credit"
    if "no game" in q or "nogame" in q:
        return "game"
    if "wager" in q:
        return "wager"
    if "sas" in q and "control" in q:
        return "SAS"
    if "sas" in q:
        return "SAS"
    if "inactivity" in q:
        return "Inactivity"
    if "aurum" in q:
        return "Aurum"
    if "credit" in q:
        return "credit"
    if _PAYOUT_METHOD_QUESTION.search(question or ""):
        return "outputtype"
    return None


def _file_contains(path: Path, needle: str) -> bool:
    if path.suffix.lower() not in _CONTENT_EXTS and not path.name.lower().endswith(
        (".xml", ".json")
    ):
        return False
    from ai_helper.gcxml_decrypt import text_for_helper_search

    try:
        data = path.read_bytes()[:MAX_CONTENT_READ_BYTES]
    except OSError:
        return False
    text = text_for_helper_search(path, data)
    if not text:
        return False
    return needle.lower() in text.lower()


def retrieve(
    question: str,
    roots: list[str] | tuple[str, ...] | None,
    *,
    max_hits: int = MAX_HITS,
    cancel_check: Callable[[], bool] | None = None,
) -> list[RetrievalHit]:
    """
    Search ``roots`` for files matching the question.

    Single file walk per call, then alias / filename / content passes.
    ``cancel_check`` may abort mid-walk (returns hits collected so far).
    """
    q = (question or "").strip()
    root_paths = diagnose_roots(list(roots or ())).roots
    root_paths_p = [Path(r) for r in root_paths]
    if not q or not root_paths_p:
        return []

    hits: list[RetrievalHit] = []
    seen: set[str] = set()
    alias_pats = _alias_patterns(q)
    needles = _content_needles(q)
    primary = _primary_needle(q, needles)

    def _cancelled() -> bool:
        return bool(cancel_check and cancel_check())

    def _add(path: Path, score: float, reason: str, needle: str | None = None) -> None:
        try:
            key = str(path.resolve()).lower()
        except OSError:
            return
        if key in seen:
            return
        if not path.is_file():
            return
        use_needle = needle or primary
        if path.name.lower() in ("commcontroler.ini", "commcontroller.ini"):
            if not use_needle or use_needle.lower() in {"port", "com"}:
                use_needle = "<"
        excerpt = read_excerpt(path, needle=use_needle)
        # Skip binary / empty noise (e.g. service.d JSON blobs).
        if not excerpt.strip():
            return
        # Never show gcxml ciphertext (ITEM____ / undecrypted setup).
        from ai_helper.gcxml_decrypt import looks_like_encrypted_excerpt

        if looks_like_encrypted_excerpt(excerpt):
            return
        seen.add(key)
        adj = score + _path_boost(path, q) + _path_demote(path, q)
        reason_out = reason
        # Mark when excerpt came from in-memory gcxml decrypt
        if path.name.lower() == "setup.xml":
            from ai_helper.gcxml_decrypt import has_memory_decrypt

            if has_memory_decrypt(path) and "gcxml-plain-memory" in excerpt:
                reason_out = f"{reason}+gcxml-memory"
                adj += 6.0
        # Content that mentions COM/Port gets a small bump for COM questions
        if re.search(r"\b(com\s*port|sas\s*com|serial)\b", q, re.I) or (
            "com" in q.lower() and "port" in q.lower()
        ):
            low_ex = excerpt.lower()
            if "port" in low_ex or "com" in low_ex or "baud" in low_ex:
                adj += 5.0
        # Prefer excerpts that actually mention credit/wager phrases
        if _is_setup_option_question(q):
            low_ex = excerpt.lower()
            if "credit" in low_ex or "wager" in low_ex or "lock in admin" in low_ex:
                adj += 8.0
        hits.append(
            RetrievalHit(
                path=str(path),
                excerpt=excerpt,
                score=adj,
                reason=reason_out,
            )
        )

    inventory = collect_files(root_paths_p, cancel_check=cancel_check)

    # Pass 1: alias globs
    if alias_pats and not _cancelled():
        for root, path in inventory:
            if _cancelled():
                break
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                rel = path.name
            for pat in alias_pats:
                if _path_matches_glob(rel, pat) or _basename_alias_match(path.name, pat):
                    _add(path, 20.0, f"alias:{pat}", needle=primary)
                    break
            if len(hits) >= max_hits * 3:
                break

    # Pass 2: filename score (reuse inventory — no second walk)
    if len(hits) < max_hits and not _cancelled():
        scored: list[tuple[float, Path]] = []
        for _root, path in inventory:
            s = _score_filename(path.name, q)
            if s >= 2.0:
                scored.append((s, path))
        scored.sort(key=lambda t: t[0], reverse=True)
        for s, path in scored[: max_hits * 2]:
            if _cancelled():
                break
            _add(path, s, "filename", needle=primary)

    # Pass 3: exact basenames mentioned in the question
    if len(hits) < max_hits and not _cancelled():
        basenames = re.findall(r"[\w.-]+\.(?:xml|json|config|ini)", q, flags=re.I)
        if basenames:
            want = {b.lower() for b in basenames}
            for _root, path in inventory:
                if _cancelled():
                    break
                if path.name.lower() in want:
                    _add(path, 15.0, "exact-name", needle=primary)

    # Pass 4: bounded content grep (always — improves excerpts / finds decrypted setup)
    if needles and not _cancelled():
        scanned = 0
        for _root, path in inventory:
            if _cancelled() or scanned >= MAX_CONTENT_SCAN_FILES:
                break
            if path.suffix.lower() not in _CONTENT_EXTS:
                continue
            scanned += 1
            for needle in needles:
                if _file_contains(path, needle):
                    _add(path, 18.0, f"content:{needle}", needle=needle)
                    break
            if len(hits) >= max_hits * 3:
                break

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:max_hits]
