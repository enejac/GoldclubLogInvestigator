"""
Privacy-safe incident summarizer (Gemini primary, Groq optional).

Guardrail: ``generate_incident_summary`` must NOT send raw proprietary logs (aggregates only).
``enhance_incident_summary`` / ``generate_full_audit`` send bounded log excerpts per UI design.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
import json
import logging
import re
import time

from product_version import PRODUCT_VERSION_PLACEHOLDER
import warnings
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

# Developer API / free tier. ``gemini-1.5-flash`` is often 404 on legacy ``v1beta`` paths;
# ``gemini-2.0-flash`` is the current stable Flash id for new keys.
GEMINI_MODEL = "gemini-2.0-flash"

# Groq Cloud (OpenAI-compatible, free tier for dev keys).
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_CHAT_COMPLETIONS_URL = "https://api.groq.com/openai/v1/chat/completions"
# OpenRouter (OpenAI-compatible).
# Free endpoints can be rotated/retired; keep a fallback list to avoid hard failures.
OPENROUTER_MODEL = "openrouter/free"
OPENROUTER_MODEL_FALLBACKS: tuple[str, ...] = (
    "openrouter/free",
    "google/gemini-2.0-flash-exp:free",
)
OPENROUTER_CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"

# Venice AI (OpenAI-compatible, privacy-focused). Model id from Settings (default qwen-3-6-plus).
from config_manager import VENICE_MODEL_DEFAULT

VENICE_CHAT_COMPLETIONS_URL = "https://api.venice.ai/api/v1/chat/completions"

AI_PROVIDER_GEMINI = "gemini"
AI_PROVIDER_GROQ = "groq"
AI_PROVIDER_OPENROUTER = "openrouter"
AI_PROVIDER_VENICE = "venice"

# Legacy ``google.generativeai`` client timeout (seconds). Full audit uses tiny payloads;
# incident enhance can be large — keep this generous to avoid aborting before the API responds.
GEMINI_LEGACY_TIMEOUT_SECS = 120
# ``google.genai`` Client uses ``HttpOptions.timeout`` in **milliseconds**.
GEMINI_HTTP_TIMEOUT_MS = 120_000

# Top-tier slot/EGM domain expertise prepended to EVERY provider's system instruction
# (Gemini, Groq, OpenRouter, Venice) for all AI flows: incident summary, enhance, and
# full session audit. Keep this free of curly braces — it is concatenated with prompt
# bodies that are processed via str.format().
SLOT_EGM_EXPERTISE = """
You are a world-class expert in electronic gaming machines (EGMs / slot machines), the SAS
gaming protocol, and the GoldClub / Aurum slot platform. Apply this domain expertise to every
analysis so your output is accurate and authoritative about slot games and general EGM behaviour.

PLATFORM & COMPONENTS
- GoldClub cabinets run a OneHand slot client alongside GoldClub.Aurum.Services. Logs are split
  per subsystem: SlotLog (game/client), GoldClub.Aurum.Services and SASControler/sasmsgr
  (host/SAS bridge), OneHand event splits (GAME EVENTS, TRANSACTION EVENTS), Bootstrap, CommCtrl,
  HardwareSetup, AurumConfigurer, and GoldClub.Logging.LogDaemon (environment fingerprint, lines
  like "Spawning vX.Y.Z").
- CommCtrl.exe is the SAS communications bridge between the EGM and the host/messenger. Transfer
  and meter state persists under var/state/goldclub.aurum.services/GCMessenger (DeviceManagerData
  and AFT transaction history XML).
- Games are themes loaded by the client (Themes\\<ThemeName>\\config_*.xml). Multigamer/game-selector
  behaviour is driven by slot/themes/mgconfig.xml.

LOG FORMAT
- Timestamps are ISO-8601 with offset (e.g. 2026-03-15T00:00:00.551+00:00), occasionally
  "YYYY-MM-DD HH:MM:SS". Levels are INFO, WARN, ERROR. Context is bracketed, e.g. [SlotMachine] or
  [Loading Game], then Namespace.Class - message.
- The cabinet/machine id usually appears as GST##### inside URIs (e.g.
  msgrUri:http://GST20664:50011/SASControler1), sometimes thousands of lines into a file, not only
  at the top.

SAS PROTOCOL & ACCOUNTING (be precise)
- SAS exposes meters via long polls; the 6F poll returns multiple meters as meter code + BCD value.
  On the wire the 2-byte meter code is little-endian (e.g. bytes 17 00 mean meter 0017).
- Money meters are stored in cents and may be displayed in credits/dollars after denomination
  scaling. Pure counters (games played, etc.) are never scaled.
- AFT/WAT transfers: TotalTransferToEGM (0017) and TotalTransferToHost (0018) are AGGREGATE meters
  = cashable + non-cashable (restricted) + promotional. When reconciling against EGM WAT buckets,
  sum cashableInAmt + nonCashInAmt + promoInAmt for IN (and the Out equivalents for 0018); comparing
  against the cashable-only bucket produces false mismatches.
- Core meters to recognize: TotalCreditsFromBills (000B), TotalTicketIn (0015), TotalTicketOut
  (0016), TotalHandPaid (0023), TotalBillsDispensed (006E), TotalCancelledCredits (0004),
  TotalJackpot (0002). TITO = ticket in/out; handpay/cancelled credits are attendant-paid
  jackpots or large wins.

FAILURE-MODE REASONING
- Always separate the true root cause from downstream side effects. A game-load crash typically has
  a first-cause exception (often a NullReferenceException during theme/resource init, an
  InvalidComObjectException on a disposed COM object, or an IOException on a locked/missing theme
  file) followed by cascading cleanup errors. Report the first cause and the exact step where the
  sequence got stuck.
- Resource-disposal and shared-screen lifecycle problems most often surface at game transitions.
  Correlate by timestamp to pinpoint where the flow stalls.
- Watch for hardware faults (Dallas key/security, bill validator, printer, hopper), SAS link drops
  and comms timeouts, RAM clears, and accounting mismatches.
- Roulette (Ruleta / Alegro) classic ERROR N screens: the Godot UI closes and a dedicated error
  window opens. Logs show INFO TRIAL error="N" type="DISPLAYED" (and sometimes ERRO Trial expired
  for ERROR 30). These are game-interrupting cabinet faults — treat ERROR N catalog title and
  possible faults as the ROOT CAUSE (GCI-ROULETTE-007). Do not blame a later Godot kill alone when
  TRIAL DISPLAYED is present. Slot / OneHand logs do not use this ERROR N screen.
  ERROR 30 that returns after RAM clear / clock rollback is leftover
  C:\\goldclub\\ruleta\\persistent\\RouletteActivate.dat (RAM clear does not wipe
  persistent). Empty Activate plus "Dongle mismatch" is ERROR 99 (fresh bind),
  not a missing Aurum XML. Do not restore an expired Activate.dat from a snapshot.
- Rank severity ERROR > WARN > INFO, but weigh real impact: a single fatal crash that ends the
  session outranks many benign repeated warnings.

GROUNDING
- Use the exact identifiers, meter codes, timestamps, theme names, and component names found in the
  logs. Be concrete and technical. Never invent meters, codes, games, or events that are not
  supported by the provided data; if something is unknown, say so briefly.
""".strip()

SYSTEM_PROMPT = """
Act as a Senior Casino Systems & QA Engineer.

### DATA PROVIDED:
- SOFTWARE_VERSION: {software_version}
- INCIDENT_DATA: [User provided logs]

### DEFECT TICKET TITLE RULES:
Format the title EXACTLY as (SOFTWARE_VERSION must be the first segment — never omit it):
{software_version} => [Game Name] => [Short Summary]

Example: ``Ruleta Module_v10.2.0.684 => Roulette => Godot Process Crash / Forced Exit``

- SOFTWARE_VERSION is provided above — copy it verbatim as the title prefix.
- Game Name: Identify from logs (e.g., Roulette, BigSafari_HnW). Do NOT put the product build alone as Game Name.
- Summary: Concise technical failure.

### OUTPUT SECTIONS (all four are mandatory — never return empty or meta-only text):
Title: [Follow formula above — MUST start with SOFTWARE_VERSION]
Key details:
- ROOT CAUSE: [First failure in the timeline — what broke first, in which component, and why]
- LOG SEQUENCE: [3–8 chronological bullets from preceding_logs near the CRITICAL fault timestamp, oldest → newest]
- [Additional technical bullets: exceptions, missing nodes, process exits, SAS/state if relevant]
Actual result: [What the player/operator observed — concrete failure at the fault line]
Expected result: [Correct stable behavior for this game flow]

LOG SEQUENCE RULES:
- Use only lines whose timestamps are at or near the incident fault time in error_details.
- NEVER use LogDaemon boot/fingerprint lines (``Spawning v…``, ``Spawning... done``, early-day
  INFO Connecting/Connected) unless the CRITICAL fault itself is at boot.
- Prefer WARN/ERROR/CRITICAL, Godot kill/exit, Missing node, and exception lines over INFO chatter.
- For Roulette: include ``TRIAL error=… type=DISPLAYED``, ``Trial expired``, and ``Roulette ERROR N``
  lines when present — they mark the dedicated error window that closed Godot.

You MUST ground ROOT CAUSE and LOG SEQUENCE in preceding_logs and error_details. Never invent
events. If a ``known Roulette ERROR (catalog)`` block is provided, use it for ROOT CAUSE and link
tracking id GCI-ROULETTE-007. If evidence is thin, state what is missing and infer cautiously from
the lines provided. Never respond with safety ratings, refusals, or placeholder text only.

Strictly avoid markdown bolding.

Write every section as an objective technical report. Do not use first-person phrasing or
disclaimers such as "Based on my AI analysis", "As an AI", or similar.
""".strip()

SYSTEM_PROMPT_AUDIT = """
You are analyzing a full session of logs and generating a defect ticket that summarizes the overall health and the most critical systemic failures of the session.

DATA PROVIDED:
- SOFTWARE_VERSION: {software_version}   (cabinet build: product name + ``_`` + core version)
- INCIDENT_DATA: batch summary lines (timestamp, game, severity, error type per incident)

You MUST format your response EXACTLY with these four markdown headers and nothing else:

### Title
{software_version} => Session Audit => [1-sentence summary of the most critical systemic issue or overall health]

### Key details
* [Bullet points of the major recurring errors, critical crashes, and patterns found across the session]
* [Group similar errors together and mention specific components that repeatedly fail]
* [Keep it highly technical and objective]
* [If Roulette ERROR N / TRIAL DISPLAYED appears, name the ERROR code and catalog root cause]

### Actual result
[A paragraph describing the current unstable or broken state of the build based on the session logs. e.g., "Throughout the test session, the build exhibited systemic resource disposal failures..."]

### Expected result
[A paragraph describing the healthy, expected behavior for the system as a whole. e.g., "The system should maintain stability across all game transitions, cleanly deallocate resources, and operate without recurring fatal exceptions."]

Write objectively throughout. Do not use first-person phrasing or disclaimers such as
"Based on my AI analysis", "As an AI", or similar — especially in Actual result and Expected result.

Roulette note: classic ERROR N screens close Godot and open a dedicated error window
(TRIAL DISPLAYED / GCI-ROULETTE-007). Prefer catalog root cause over treating a later process kill
as the primary defect when ERROR N is present.
""".strip()


def _compose_system_instruction(base_prompt: str, software_version: str) -> str:
    """Prepend the slot/EGM domain expertise to a base prompt for ALL providers.

    ``str.format`` is applied only to ``base_prompt`` so the expertise text is never
    subject to brace substitution (and may safely contain characters like ``{}``).
    """
    sv = (software_version or "").strip() or PRODUCT_VERSION_PLACEHOLDER
    body = base_prompt.format(software_version=sv)
    return f"{SLOT_EGM_EXPERTISE}\n\n{body}"


def _new_sdk_content_config(system_instruction: str) -> Any:
    """Config for ``google.genai`` ``generate_content`` with system instruction."""
    try:
        from google.genai import types as genai_types  # type: ignore

        return genai_types.GenerateContentConfig(system_instruction=system_instruction)
    except Exception:
        return {"system_instruction": system_instruction}


def _genai_developer_client(genai_mod: object, api_key: str) -> Any:
    """
    ``google.genai`` Client with an explicit HTTP timeout.

    Without this, large "Enhance Summary" payloads can sit behind the default deadline and
    surface as ``504 The request timed out`` from the API gateway.
    """
    try:
        from google.genai import types as genai_types  # type: ignore

        return genai_mod.Client(
            api_key=api_key,
            http_options=genai_types.HttpOptions(timeout=GEMINI_HTTP_TIMEOUT_MS),
        )
    except Exception:
        return genai_mod.Client(api_key=api_key)


def _gemini_transient_http_error(exc: BaseException) -> bool:
    """Retry once on gateway / overload / deadline errors that often succeed on repeat."""
    s = str(exc).lower()
    return (
        "504" in s
        or "503" in s
        or "502" in s
        or "429" in s
        or "timeout" in s
        or "timed out" in s
    )


def _pick_legacy_generate_model(genai_mod: object) -> str | None:
    """
    Pick a legacy ``google.generativeai`` model name that supports generateContent.

    Returns a model resource name (e.g. ``models/gemini-2.0-flash``) or ``None``.
    """
    try:
        models = genai_mod.list_models()  # type: ignore[attr-defined]
    except Exception:
        return None
    best: str | None = None
    for m in models or []:
        name = str(getattr(m, "name", "") or "").strip()
        methods = getattr(m, "supported_generation_methods", None) or []
        methods_s = {str(x) for x in methods}
        if not name:
            continue
        # Must support text generation
        if "generateContent" not in methods_s:
            continue
        lname = name.lower()
        if "vision" in lname:
            continue
        if "gemini" not in lname:
            continue
        # Prefer flash-tier models for quota/cost; avoid auto-picking "pro".
        if best is None:
            best = name
        # Prefer stable Flash IDs on the Developer API (legacy SDK uses ``v1beta`` discovery).
        if "gemini-2.0-flash" in lname and "lite" not in lname:
            return name
        if "gemini-2.5-flash-lite" not in lname and "gemini-2.5-flash" in lname:
            return name
        if "gemini-1.5-flash" in lname:
            return name
        if "flash" in lname and "pro" not in lname:
            best = name
    return best


def _configure_legacy_genai(genai_mod: object, api_key: str) -> None:
    """
    Configure the deprecated ``google.generativeai`` client.

    ``google-generativeai`` is hard-wired to ``v1beta`` discovery; using a current model id
    (``gemini-2.0-flash``) avoids 404s more reliably than retired 1.5 Flash names.
    """
    try:
        from google.api_core import client_options as co  # type: ignore

        # api_endpoint must be host[:port] (no URL scheme) for grpc transport.
        opts = co.ClientOptions(api_endpoint="generativelanguage.googleapis.com")
        genai_mod.configure(api_key=api_key, client_options=opts)  # type: ignore[attr-defined]
    except Exception:
        genai_mod.configure(api_key=api_key)  # type: ignore[attr-defined]


def _groq_chat_completion(system_instruction: str, user_content: str, api_key: str) -> str:
    """Call Groq OpenAI-compatible chat completions (stdlib HTTP; no extra deps)."""
    key = (api_key or "").strip()
    if not key:
        raise ValueError("Missing Groq API key.")
    body = json.dumps(
        {
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.25,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        GROQ_CHAT_COMPLETIONS_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise RuntimeError(f"Groq HTTP {e.code}: {err_body or e.reason}") from e
    data = json.loads(raw)
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"Groq: empty choices in response: {data!r}")
    msg = choices[0].get("message") or {}
    content = (msg.get("content") or "").strip()
    if not content:
        raise RuntimeError(f"Groq: no message content: {data!r}")
    return content


def _openrouter_chat_completion(system_instruction: str, user_content: str, api_key: str) -> str:
    """Call OpenRouter chat completions (OpenAI-compatible, stdlib HTTP)."""
    key = (api_key or "").strip()
    if not key:
        raise ValueError("Missing OpenRouter API key.")
    last_exc: BaseException | None = None
    for model_name in OPENROUTER_MODEL_FALLBACKS:
        body = json.dumps(
            {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": user_content},
                ],
                "temperature": 0.25,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            OPENROUTER_CHAT_COMPLETIONS_URL,
            data=body,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            last_exc = RuntimeError(
                f"OpenRouter HTTP {e.code}: {err_body or e.reason}"
            )
            # If this specific free model disappeared, try the next one.
            if e.code == 404:
                continue
            raise last_exc from e
        data = json.loads(raw)
        choices = data.get("choices") or []
        if not choices:
            last_exc = RuntimeError(f"OpenRouter: empty choices in response: {data!r}")
            continue
        msg = choices[0].get("message") or {}
        content = (msg.get("content") or "").strip()
        if content:
            return content
        last_exc = RuntimeError(f"OpenRouter: no message content: {data!r}")
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("OpenRouter request failed.")


def _venice_chat_completion(
    system_instruction: str,
    user_content: str,
    api_key: str,
    *,
    model: str | None = None,
) -> str:
    """Call Venice AI OpenAI-compatible chat completions (stdlib HTTP; no extra deps)."""
    from config_manager import SettingsManager

    key = (api_key or "").strip()
    if not key:
        raise ValueError("Missing Venice API key.")
    model_id = (model or SettingsManager.get_venice_model() or VENICE_MODEL_DEFAULT).strip()
    body = json.dumps(
        {
            "model": model_id,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.25,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        VENICE_CHAT_COMPLETIONS_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise RuntimeError(f"Venice HTTP {e.code}: {err_body or e.reason}") from e
    data = json.loads(raw)
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"Venice: empty choices in response: {data!r}")
    msg = choices[0].get("message") or {}
    content = (msg.get("content") or "").strip()
    if not content:
        raise RuntimeError(f"Venice: no message content: {data!r}")
    return content


def _normalize_ai_provider(provider: str | None) -> str:
    p = (provider or AI_PROVIDER_GEMINI).strip().lower()
    if p == AI_PROVIDER_GROQ:
        return AI_PROVIDER_GROQ
    if p == AI_PROVIDER_OPENROUTER:
        return AI_PROVIDER_OPENROUTER
    if p == AI_PROVIDER_VENICE:
        return AI_PROVIDER_VENICE
    return AI_PROVIDER_GEMINI


def _failover_eligible(exc: BaseException) -> bool:
    """
    True when trying the other provider may help (quota, rate limit, auth, credits, missing model).
    """
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in (401, 403, 404, 429, 502, 503, 504)
    try:
        from google.api_core import exceptions as gexc  # type: ignore

        if isinstance(
            exc,
            (
                gexc.ResourceExhausted,
                gexc.PermissionDenied,
                gexc.Unauthenticated,
                gexc.NotFound,
            ),
        ):
            return True
    except Exception:
        pass
    s = str(exc).lower()
    if "429" in s or "401" in s or "403" in s or "404" in s:
        return True
    if "502" in s or "503" in s or "504" in s:
        return True
    if "quota" in s and ("exceed" in s or "limit: 0" in s):
        return True
    if "rate limit" in s or "too many requests" in s or "resource exhausted" in s:
        return True
    if "high demand" in s or "temporarily unavailable" in s:
        return True
    if "timeout" in s or "timed out" in s:
        return True
    if "please retry in" in s:
        return True
    if "invalid api key" in s or "api key not valid" in s:
        return True
    if "permission denied" in s or "unauthenticated" in s:
        return True
    return False


def _provider_ready(
    provider: str,
    *,
    gemini_key: str,
    groq_key: str,
    openrouter_key: str,
    venice_key: str,
    gemini_sdk_available: bool,
) -> bool:
    mk = (gemini_key or "").strip()
    gk = (groq_key or "").strip()
    ok = (openrouter_key or "").strip()
    vk = (venice_key or "").strip()
    if provider == AI_PROVIDER_GEMINI:
        return bool(mk) and gemini_sdk_available
    if provider == AI_PROVIDER_GROQ:
        return bool(gk)
    if provider == AI_PROVIDER_OPENROUTER:
        return bool(ok)
    if provider == AI_PROVIDER_VENICE:
        return bool(vk)
    return False


def _short_api_error(exc: BaseException) -> str:
    s = str(exc).strip()
    if not s:
        return type(exc).__name__
    line = s.splitlines()[0].strip()
    if len(line) > 140:
        line = line[:137] + "..."
    return line


def _dual_failure_message(
    e1: BaseException | None,
    e2: BaseException | None,
    *,
    audit: bool,
) -> str:
    pfx = "Session audit" if audit else "Technical summary"
    err = e2 or e1
    if err is not None:
        hint = _quota_hint_from_exception(err, audit=audit)
        if hint:
            return hint
        return f"{pfx} unavailable ({_short_api_error(err)})."
    return (
        f"{pfx} unavailable (all configured AI providers failed — "
        "check API keys and quota in Settings)."
    )


_AI_FAILURE_MARKERS = (
    "limit reached",
    "please try again later",
    "technical summary unavailable",
    "technical summary skipped",
    "session audit unavailable",
    "session audit skipped",
    "no api key configured",
    "no configured ai provider",
    "rate limited",
    "quota is 0",
    "gemini sdk not installed",
    "⚠️",
)


def _is_ai_unavailable_response(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return True
    return any(m in t for m in _AI_FAILURE_MARKERS)


def _is_structured_defect_ticket(text: str) -> bool:
    """True when AI output has parseable defect-ticket sections with real content."""
    import re

    t = (text or "").strip()
    if len(t) < 80:
        return False
    lower = t.lower()
    if re.fullmatch(r"user safety:\s*\w+", lower):
        return False
    if "user safety:" in lower and not re.search(r"(?im)^\s*Title\s*:", t):
        return False

    has_md = re.search(r"(?im)^\s*###\s*Title\s*$", t)
    has_plain = re.search(r"(?im)^\s*Title\s*:", t)
    if not has_md and not has_plain:
        return False

    key_match = re.search(
        r"(?im)^\s*(?:###\s*)?Key details\s*:?\s*(.*?)(?:\n\s*\n|^\s*(?:###\s*)?Actual result\s*:?)",
        t,
        re.DOTALL,
    )
    if not key_match:
        return False
    key_body = key_match.group(1).strip()
    if len(key_body) < 24:
        return False
    if key_body.lower().startswith("user safety"):
        return False
    return True


def _is_low_quality_ai_response(text: str) -> bool:
    """Catch blocked, safety-meta, or empty AI replies that should use offline analysis."""
    t = (text or "").strip()
    if not t:
        return True
    if _is_ai_unavailable_response(t):
        return True
    if _is_structured_defect_ticket(t):
        return False
    lower = t.lower()
    if "user safety:" in lower and len(t) < 400:
        return True
    if len(t) < 60:
        return True
    return True


_LOG_SEQUENCE_KEYWORDS = (
    "error",
    "warn",
    "critical",
    "exception",
    "missing node",
    "godot",
    "ruleta",
    "unhandled",
    "exited",
    "unexpected",
    "killed",
    "failed",
    "nullreference",
    "ioexception",
    "queuedata",
    "payoutpressed",
    "process:",
    "ended",
    "stack trace",
    "critical log",
    "did not exit",
    "forced",
)

_ISO_TS_RE = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:\d{2}|Z)?)"
)


def _parse_line_timestamp(line: str) -> float | None:
    """Return epoch seconds for an ISO timestamp at the start of a log line, if any."""
    from datetime import datetime

    m = _ISO_TS_RE.search(line or "")
    if not m:
        return None
    raw = m.group("ts").replace(" ", "T")
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw).timestamp()
    except Exception:
        return None


def _is_boot_fingerprint_line(line: str) -> bool:
    """LogDaemon spawn / early connect noise — not useful as incident LOG SEQUENCE."""
    low = (line or "").lower()
    if "spawning" in low:
        return True
    if "logging::log()" in low and "spawn" in low:
        return True
    if "connecting to: 127.0.0.1" in low or "connected to: 127.0.0.1" in low:
        return True
    return False


def _incident_anchor_epoch(error_details: str, preceding_logs: list[str]) -> float | None:
    """Prefer the latest CRITICAL/fault timestamp; fall back to last timestamp in context."""
    candidates: list[float] = []
    for blob in (error_details or "", "\n".join(preceding_logs or [])):
        for line in blob.splitlines():
            low = line.lower()
            if any(
                k in low
                for k in (
                    "critical",
                    "godot did not exit",
                    "killing process",
                    "unhandled exception",
                    "exited unexpectedly",
                )
            ):
                ts = _parse_line_timestamp(line)
                if ts is not None:
                    candidates.append(ts)
    if candidates:
        return max(candidates)
    for line in reversed(list(preceding_logs or []) + (error_details or "").splitlines()):
        ts = _parse_line_timestamp(line)
        if ts is not None and not _is_boot_fingerprint_line(line):
            return ts
    return None


def _extract_log_sequence_bullets(
    lines: list[str],
    *,
    max_bullets: int = 10,
    anchor_epoch: float | None = None,
    window_seconds: float = 600.0,
) -> list[str]:
    """Chronological evidence lines near the fault — skip boot Spawning noise."""
    filtered: list[str] = []
    for line in lines:
        s = (line or "").strip()
        if not s or s.startswith("... [gap] ..."):
            continue
        if _is_boot_fingerprint_line(s):
            continue
        filtered.append(s)

    if anchor_epoch is None:
        anchor_epoch = _incident_anchor_epoch("\n".join(filtered), filtered)

    near: list[str] = []
    if anchor_epoch is not None:
        for s in filtered:
            ts = _parse_line_timestamp(s)
            if ts is None:
                continue
            if abs(ts - anchor_epoch) <= window_seconds:
                near.append(s)
        if not near:
            # Widen once if nothing in the tight window.
            for s in filtered:
                ts = _parse_line_timestamp(s)
                if ts is None:
                    continue
                if abs(ts - anchor_epoch) <= window_seconds * 6:
                    near.append(s)
    pool = near if near else filtered

    picked: list[str] = []
    seen: set[str] = set()
    for s in pool:
        low = s.lower()
        if not any(k in low for k in _LOG_SEQUENCE_KEYWORDS):
            continue
        if s in seen:
            continue
        seen.add(s)
        picked.append(s[:240])
    if len(picked) < 3:
        # Prefer the end of the pool (closest to the fault) over file-start INFO.
        for s in reversed(pool):
            if s in seen:
                continue
            seen.add(s)
            picked.insert(0, s[:240])
            if len(picked) >= max_bullets:
                break
    return picked[-max_bullets:]


def ensure_defect_title_has_version(title: str, software_version: str) -> str:
    """Force ``{software_version} => …`` on defect ticket titles (single line only)."""
    from network.defect_ticket import first_title_line

    sv = (software_version or "").strip() or PRODUCT_VERSION_PLACEHOLDER
    t = first_title_line(title or "")
    if not t:
        return f"{sv} => Cabinet => Incident"
    if t.startswith(sv + " =>") or t.startswith(sv + "=>"):
        return t
    if re.match(r"(?i)^(ruleta|roulette|slot|cabinet|game)\s*=>", t):
        return f"{sv} => {t}"
    if "=>" in t:
        return f"{sv} => {t}"
    return f"{sv} => Roulette => {t}"


def rewrite_defect_ticket_software_version(text: str, software_version: str) -> str:
    """Rewrite Title so SOFTWARE_VERSION is first; keep Key details as its own section."""
    from network.defect_ticket import parse_defect_ticket_sections

    sv = (software_version or "").strip() or PRODUCT_VERSION_PLACEHOLDER
    t = text or ""
    if not t.strip():
        return t

    parts = parse_defect_ticket_sections(t)
    title = ensure_defect_title_has_version(parts.get("Title", ""), sv)
    key = (parts.get("Key details") or "").strip()
    actual = (parts.get("Actual result") or "").strip()
    expected = (parts.get("Expected result") or "").strip()

    out = f"Title: {title}\n\n"
    if key:
        out += f"Key details:\n{key}\n\n"
    if actual:
        out += f"Actual result: {actual}\n\n"
    if expected:
        out += f"Expected result: {expected}"
    return out.strip()


def _infer_root_cause_line(
    error_details: str,
    preceding_logs: list[str],
    known: object | None,
    failure: str,
) -> str:
    """Single-sentence root cause from log evidence and known-issue catalog."""
    import re

    from roulette_errors import find_roulette_error_in_text

    roulette = find_roulette_error_in_text(error_details, "\n".join(preceding_logs))
    if roulette is not None:
        return (
            f"Roulette ERROR {roulette.code} screen displayed (Godot UI closed): "
            f"{roulette.title}"
        )

    combined = list(preceding_logs) + error_details.splitlines()
    for line in reversed(combined):
        s = line.strip()
        if not s:
            continue
        if "Unhandled exception" in s:
            ex = re.search(r"Unhandled exception:\s*(\S+)", s)
            meth = re.search(r"at\s+(\S+\.\S+)\(", s)
            if ex and meth:
                return (
                    f"Unhandled {ex.group(1)} in {meth.group(1).split('.')[-1]} "
                    "— first fatal fault in the Godot/ruleta GUI stack."
                )
            if ex:
                return f"Unhandled {ex.group(1)} — first fatal fault in the game client."
        if "Godot did not exit" in s:
            return (
                "Godot renderer hung on shutdown and was force-killed — "
                "usually follows an earlier GUI exception or ruleta service restart."
            )
        if re.search(r"exited unexpectedly|Ruleta process:\s*\d+\s+ended", s, re.I):
            return (
                "Ruleta/Godot child process terminated unexpectedly — "
                "correlate godot\\ and ruleta\\ logs around this timestamp."
            )
        if "Missing node" in s:
            return (
                f"Scene graph fault before crash: {s[:180]} "
                "(missing node often precedes NullReference on payout/touch)."
            )
        if "Exception while reading QueueData" in s:
            return (
                "Godot QueueData deserialization failed — backend sent null/invalid "
                "lock or bet state to the UI queue."
            )
    if known is not None:
        pc = getattr(known, "probable_cause", "") or ""
        if ":" in pc:
            tail = pc.split(":", 1)[1].strip()
            if tail:
                return tail
    return f"CRITICAL fault: {failure} — inspect stack/context lines for the first ERROR/WARN before the fault."


def _extract_game_name(error_details: str, preceding: list[str]) -> str:
    import re

    blob = f"{error_details}\n" + "\n".join(preceding)
    theme = re.search(r"Themes[\\/\\]([^\\/\\]+)", blob, re.I)
    if theme:
        return theme.group(1)
    if re.search(
        r"ruleta|roulette|godot|TRIAL\s+error=|Roulette\s+ERROR\s+\d+",
        blob,
        re.I,
    ):
        return "Roulette"
    if re.search(r"onehand|slot", blob, re.I):
        return "Slot"
    return "Cabinet"


def _extract_failure_summary(error_details: str) -> str:
    import re

    from roulette_errors import find_roulette_error_in_text

    roulette = find_roulette_error_in_text(error_details)
    if roulette is not None:
        short = roulette.title.rstrip(".")
        if len(short) > 80:
            short = short[:77] + "…"
        return f"Roulette ERROR {roulette.code} — {short}"

    m = re.search(r"Unhandled exception:\s*(\S+)", error_details)
    if m:
        ex = m.group(1)
        meth = re.search(r"at\s+(\S+\.\S+)\(", error_details)
        if meth:
            short_m = meth.group(1).split(".")[-1]
            return f"{ex} ({short_m})"
        return ex
    for pat in (
        r"(NullReferenceException)",
        r"(InvalidOperationException)",
        r"(IOException)",
        r"Critical Log Exception",
    ):
        m = re.search(pat, error_details, re.I)
        if m:
            return m.group(1)
    for line in error_details.splitlines():
        s = line.strip()
        if len(s) > 24:
            return s[:120]
    return "Critical log exception"


def _roulette_catalog_prompt_block(
    error_details: str,
    preceding_logs: list[str],
) -> str | None:
    """Matched Roulette ERROR N catalog text for cloud / offline ticket AI."""
    from roulette_errors import (
        find_roulette_error_in_text,
        format_catalog_block_for_ai,
    )

    info = find_roulette_error_in_text(
        error_details,
        "\n".join(preceding_logs or []),
    )
    if info is None:
        return None
    return format_catalog_block_for_ai(info)


def _offline_incident_summary(
    error_details: str,
    preceding_logs: list[str],
    software_version: str,
    *,
    ai_fallback: bool = False,
) -> str:
    """Structured defect ticket from log evidence when AI providers fail or return garbage."""
    from parser_rules import match_known_issue
    from roulette_errors import find_roulette_error_in_text

    sv = (software_version or "").strip() or PRODUCT_VERSION_PLACEHOLDER
    game = _extract_game_name(error_details, preceding_logs)
    failure = _extract_failure_summary(error_details)

    roulette = find_roulette_error_in_text(
        error_details,
        "\n".join(preceding_logs or []),
    )

    known = match_known_issue(error_details, "CRITICAL")
    if known is None:
        for line in preceding_logs:
            known = match_known_issue(line, "CRITICAL")
            if known:
                break

    if roulette is not None:
        title_label = f"Roulette ERROR {roulette.code} — {roulette.title.rstrip('.')}"
        if len(title_label) > 100:
            title_label = title_label[:97] + "…"
    else:
        title_label = known.name if known else failure
    title = ensure_defect_title_has_version(f"{game} => {title_label}", sv)

    root_cause = _infer_root_cause_line(error_details, preceding_logs, known, failure)
    anchor = _incident_anchor_epoch(error_details, list(preceding_logs))
    seq_lines = _extract_log_sequence_bullets(
        list(preceding_logs) + error_details.splitlines(),
        anchor_epoch=anchor,
    )

    bullets: list[str] = [
        f"ROOT CAUSE: {root_cause}",
    ]
    if roulette is not None:
        bullets.append(roulette.probable_cause)
        bullets.append("Tracking: GCI-ROULETTE-007 — Roulette ERROR N screen (Godot UI closed).")
    if seq_lines:
        bullets.append("LOG SEQUENCE:")
        bullets.extend(f"  * {ln}" for ln in seq_lines)
    elif known and roulette is None:
        bullets.append(known.probable_cause)

    combined = list(preceding_logs) + error_details.splitlines()
    seq_set = set(seq_lines)
    for line in combined:
        s = line.strip()
        if not s or s in seq_set:
            continue
        for needle in (
            "Missing node",
            "Unhandled exception",
            "Godot did not exit",
            "exited unexpectedly",
            "Ruleta process:",
            "Exception while reading QueueData",
            "Critical Log Exception",
            "TRIAL error=",
            "Trial expired",
            "Roulette ERROR",
        ):
            if needle.lower() in s.lower() and s not in bullets and len(bullets) < 14:
                bullets.append(s[:220])
                break

    if len(bullets) <= 2:
        bullets.append(failure)

    if ai_fallback:
        bullets.append(
            "Evidence-based summary — AI returned insufficient detail "
            "(blocked, safety filter, or empty response)."
        )
    else:
        bullets.append(
            "Offline draft — AI summary unavailable (rate limit, quota, or missing API key)."
        )

    actual = (
        f"The session logged a CRITICAL fault in {game}: {failure}. "
        f"{root_cause}"
    )
    if roulette is not None:
        expected = (
            f"{game} should play without opening the dedicated ERROR {roulette.code} "
            "window; Godot UI should stay up and the wheel/sensors/COM/power path for "
            "this catalog fault should remain healthy."
        )
    else:
        expected = (
            f"{game} should run without unhandled GUI exceptions or hung Godot shutdowns; "
            "touch/payout flows should stay synchronized with the Aurum backend."
        )
    if known and getattr(known, "issue_id", "").startswith("GCI-GODOT-002"):
        expected = (
            f"{game} Godot renderer should exit cleanly on game switch; "
            "ruleta service should not kill a hung process without a preceding logged fault."
        )

    formatted: list[str] = []
    for b in bullets:
        if b == "LOG SEQUENCE:":
            formatted.append("- LOG SEQUENCE:")
        elif b.startswith("  * "):
            formatted.append(b)
        else:
            formatted.append(f"- {b}")
    key_block = "\n".join(formatted)
    return (
        f"Title: {title}\n\n"
        f"Key details:\n{key_block}\n\n"
        f"Actual result: {actual}\n\n"
        f"Expected result: {expected}"
    )


def _call_with_failover(
    *,
    primary_provider: str,
    gemini_key: str,
    groq_key: str,
    openrouter_key: str,
    venice_key: str,
    gemini_sdk_available: bool,
    run_gemini: Callable[[], str],
    run_groq: Callable[[], str],
    run_openrouter: Callable[[], str],
    run_venice: Callable[[], str],
    audit: bool = False,
) -> str:
    """
    Run configured provider with cascade fallback across available providers.
    Tries primary first, then the other providers in deterministic order.
    """
    mk = (gemini_key or "").strip()
    gk = (groq_key or "").strip()
    ok = (openrouter_key or "").strip()
    vk = (venice_key or "").strip()
    providers = [
        AI_PROVIDER_GEMINI,
        AI_PROVIDER_GROQ,
        AI_PROVIDER_OPENROUTER,
        AI_PROVIDER_VENICE,
    ]
    ordered = [primary_provider] + [p for p in providers if p != primary_provider]
    run_map: dict[str, tuple[Callable[[], str], str]] = {
        AI_PROVIDER_GEMINI: (run_gemini, "Gemini"),
        AI_PROVIDER_GROQ: (run_groq, "Groq"),
        AI_PROVIDER_OPENROUTER: (run_openrouter, "OpenRouter"),
        AI_PROVIDER_VENICE: (run_venice, "Venice"),
    }

    first_error: BaseException | None = None
    last_error: BaseException | None = None
    attempted = 0
    for idx, provider in enumerate(ordered):
        if not _provider_ready(
            provider,
            gemini_key=mk,
            groq_key=gk,
            openrouter_key=ok,
            venice_key=vk,
            gemini_sdk_available=gemini_sdk_available,
        ):
            continue
        fn, label = run_map[provider]
        attempted += 1
        if idx > 0 and last_error is not None:
            logger.warning(
                "Falling back from previous provider to %s (%s)",
                label,
                last_error,
            )
        try:
            out = fn().strip()
            if out:
                return out
            err = RuntimeError(f"Empty response from {label}")
            if first_error is None:
                first_error = err
            last_error = err
        except Exception as e:  # noqa: BLE001
            if first_error is None:
                first_error = e
            last_error = e
            # Only fail over on transient/eligibile classes.
            if not _failover_eligible(e):
                hint = _quota_hint_from_exception(e, audit=audit)
                if hint:
                    return hint
                pfx = "Session audit" if audit else "Technical summary"
                return f"{pfx} unavailable ({label} API call failed: {e})."

    if attempted == 0:
        pfx = "Session audit" if audit else "Technical summary"
        return f"{pfx} unavailable (No configured AI provider key available)."

    hint = _quota_hint_from_exception(last_error or RuntimeError("Unknown AI error"), audit=audit)
    if hint:
        return hint
    return _dual_failure_message(first_error, last_error, audit=audit)


def _quota_hint_from_exception(exc: BaseException, *, audit: bool = False) -> str | None:
    s = str(exc)
    pfx = "Session audit" if audit else "Technical summary"
    # Common Gemini free-tier failure mode: quota exists but is effectively disabled (limit 0).
    if "Quota exceeded" in s and "limit: 0" in s:
        return (
            f"{pfx} unavailable (Gemini quota is 0 for this API key/project). "
            "Enable billing / free-tier quota for the Gemini API in Google Cloud/AI Studio, "
            "or use an API key from a project with active quota."
        )
    if "Please retry in" in s:
        # Keep message short; the raw exception contains verbose proto payload.
        return f"{pfx} unavailable (Rate limited). Please retry in a few seconds."
    return None


def generate_incident_summary(
    stats: dict[str, Any],
    sas_audit_result: str,
    api_key: str,
    *,
    software_version: str | None = None,
    ai_provider: str | None = None,
    groq_api_key: str = "",
    openrouter_api_key: str = "",
    venice_api_key: str = "",
) -> str:
    """
    Return a short executive summary using Gemini (or Groq if configured), or a safe fallback.

    Privacy guardrail: Only ``stats`` and ``sas_audit_result`` are used (no raw log text).
    """
    prov = _normalize_ai_provider(ai_provider)
    if prov == AI_PROVIDER_GROQ:
        gk = (groq_api_key or "").strip()
        if not gk:
            return "Technical summary skipped (No Groq API key configured in Settings)."
    if prov == AI_PROVIDER_OPENROUTER:
        ok = (openrouter_api_key or "").strip()
        if not ok:
            return "Technical summary skipped (No OpenRouter API key configured in Settings)."
    if prov == AI_PROVIDER_VENICE:
        vk = (venice_api_key or "").strip()
        if not vk:
            return "Technical summary skipped (No Venice API key configured in Settings)."

    key = (api_key or "").strip()
    if prov == AI_PROVIDER_GEMINI and not key:
        return "Technical summary skipped (No API key configured in Settings)."

    # Prefer the newer SDK if present; fall back to legacy package.
    try:
        from google import genai as genai_new  # type: ignore
    except Exception:
        genai_new = None
    if genai_new is None:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", FutureWarning)
                import google.generativeai as genai  # type: ignore
        except Exception:
            genai = None
    else:
        genai = None
    if prov == AI_PROVIDER_GEMINI and genai_new is None and genai is None:
        return "Technical summary skipped (Gemini SDK not installed). Install `google-generativeai` or `google-genai`."

    prompt = (
        "Summarize this slot machine test run using only the high-level aggregated data below. "
        "Do NOT request or assume raw logs. Follow the OUTPUT FORMAT from your instructions.\n\n"
        f"Stats (aggregated): {stats}\n\n"
        f"Accounting / SAS Audit (high-level): {sas_audit_result}\n\n"
        "For **LOG SEQUENCE**, derive observations from these aggregates and audit strings "
        "(e.g., phases, gaps, anomalies). Use **N/A** briefly only when a section does not apply."
    )

    sv = (software_version or "").strip() or PRODUCT_VERSION_PLACEHOLDER
    system_instruction = _compose_system_instruction(SYSTEM_PROMPT, sv)

    gemini_sdk_available = genai_new is not None or genai is not None

    def run_gemini() -> str:
        if genai_new is not None:
            client = _genai_developer_client(genai_new, key)
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=_new_sdk_content_config(system_instruction),
            )
            txt = getattr(resp, "text", None) or ""
        else:
            assert genai is not None
            _configure_legacy_genai(genai, key)
            preferred = GEMINI_MODEL
            from google.generativeai.types import RequestOptions  # type: ignore

            try:
                ro = RequestOptions(timeout=GEMINI_LEGACY_TIMEOUT_SECS)
                model = genai.GenerativeModel(
                    model_name=preferred,
                    system_instruction=system_instruction,
                )
                resp = model.generate_content(prompt, request_options=ro)
                txt = getattr(resp, "text", None) or ""
            except Exception as e:
                _ = e
                picked = _pick_legacy_generate_model(genai)
                if picked and picked != preferred and "flash" in picked.lower():
                    model = genai.GenerativeModel(
                        model_name=picked,
                        system_instruction=system_instruction,
                    )
                    resp = model.generate_content(
                        prompt, request_options=RequestOptions(timeout=GEMINI_LEGACY_TIMEOUT_SECS)
                    )
                    txt = getattr(resp, "text", None) or ""
                else:
                    raise
        return str(txt).strip()

    def run_groq() -> str:
        return _groq_chat_completion(
            system_instruction, prompt, (groq_api_key or "").strip()
        ).strip()

    def run_openrouter() -> str:
        return _openrouter_chat_completion(
            system_instruction,
            prompt,
            (openrouter_api_key or "").strip(),
        ).strip()

    def run_venice() -> str:
        return _venice_chat_completion(
            system_instruction,
            prompt,
            (venice_api_key or "").strip(),
        ).strip()

    return _call_with_failover(
        primary_provider=prov,
        gemini_key=key,
        groq_key=groq_api_key or "",
        openrouter_key=openrouter_api_key or "",
        venice_key=venice_api_key or "",
        gemini_sdk_available=gemini_sdk_available,
        run_gemini=run_gemini,
        run_groq=run_groq,
        run_openrouter=run_openrouter,
        run_venice=run_venice,
        audit=False,
    )


def enhance_incident_summary(
    error_details: str,
    preceding_logs: list[str],
    software_version: str,
    api_key: str,
    *,
    ai_provider: str | None = None,
    groq_api_key: str = "",
    openrouter_api_key: str = "",
    venice_api_key: str = "",
) -> str:
    """
    Root-cause analysis for one incident + preceding timeline events.

    NOTE: This sends the payload to Gemini or Groq. Callers should keep the payload
    small and avoid including large raw log dumps.
    """
    prov = _normalize_ai_provider(ai_provider)
    key = (api_key or "").strip()
    gk = (groq_api_key or "").strip()
    ok = (openrouter_api_key or "").strip()
    vk = (venice_api_key or "").strip()
    if prov == AI_PROVIDER_GROQ:
        if not gk:
            return "⚠️ Groq API key not configured in Settings."
    elif prov == AI_PROVIDER_OPENROUTER:
        if not ok:
            return "⚠️ OpenRouter API key not configured in Settings."
    elif prov == AI_PROVIDER_VENICE:
        if not vk:
            return "⚠️ Venice API key not configured in Settings."
    elif not key:
        return "⚠️ Analysis API key not configured in Settings."

    # Prefer newer SDK if present; fall back to legacy package.
    try:
        from google import genai as genai_new  # type: ignore
    except Exception:
        genai_new = None
    if genai_new is None:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", FutureWarning)
                import google.generativeai as genai  # type: ignore
        except Exception:
            genai = None
    else:
        genai = None
    gemini_sdk_available = genai_new is not None or genai is not None
    if prov == AI_PROVIDER_GEMINI and not gemini_sdk_available:
        return "Technical summary unavailable (Gemini SDK not installed)."

    sv = (software_version or "").strip() or PRODUCT_VERSION_PLACEHOLDER
    system_instruction = _compose_system_instruction(SYSTEM_PROMPT, sv)

    # Normalize inputs. Full Session Audit sends one short line per incident; this path can
    # include dozens of wide log lines and trips Gemini gateway timeouts (HTTP 504) if unbounded.
    _MAX_PRECEDING_LINE = 450
    _MAX_PRECEDING_LINES = 36
    _MAX_ERROR_CHARS = 6_500
    _MAX_USER_CHARS = 28_000

    err = (error_details or "").strip()
    if len(err) > _MAX_ERROR_CHARS:
        err = err[:_MAX_ERROR_CHARS] + "\n…(truncated)…"

    prev_lines: list[str] = []
    for x in (preceding_logs or []):
        s = str(x).rstrip()
        if not s.strip():
            continue
        if len(s) > _MAX_PRECEDING_LINE:
            s = s[:_MAX_PRECEDING_LINE] + "…"
        prev_lines.append(s)
    prev_lines = prev_lines[-_MAX_PRECEDING_LINES:]

    preceding_block = (
        "\n".join(prev_lines)
        if prev_lines
        else "(No preceding log lines were provided — analyze from error_details only.)"
    )
    error_block = err if err else "(No error_details or stack trace was provided.)"

    catalog_block = _roulette_catalog_prompt_block(err, prev_lines)
    catalog_section = ""
    if catalog_block:
        catalog_section = (
            "\n--- known Roulette ERROR (catalog) ---\n"
            f"{catalog_block}\n"
        )

    user_content = f"""Analyze the following incident payload.

CRITICAL RULES:
1. Title MUST start with SOFTWARE_VERSION exactly: {sv} => [Game] => [Summary]
2. LOG SEQUENCE must use timestamps near the CRITICAL fault in error_details.
   Do NOT quote LogDaemon ``Spawning v…`` / early-day Connecting lines unless the fault is at boot.
3. If a Roulette ERROR catalog block is present, use it for ROOT CAUSE and cite GCI-ROULETTE-007.

--- preceding_logs ({len(prev_lines)} chronological line(s), oldest → newest) ---
{preceding_block}

--- error_details ---
{error_block}
{catalog_section}"""
    if len(user_content) > _MAX_USER_CHARS:
        user_content = (
            user_content[:_MAX_USER_CHARS] + "\n…(truncated for API size limits)…"
        )

    def run_gemini() -> str:
        if genai_new is not None:
            client = _genai_developer_client(genai_new, key)
            txt = ""
            for attempt in range(2):
                try:
                    resp = client.models.generate_content(
                        model=GEMINI_MODEL,
                        contents=user_content,
                        config=_new_sdk_content_config(system_instruction),
                    )
                    txt = getattr(resp, "text", None) or ""
                    break
                except Exception as e_call:
                    if attempt == 0 and _gemini_transient_http_error(e_call):
                        time.sleep(2.0)
                        continue
                    raise
            return str(txt).strip()
        assert genai is not None
        _configure_legacy_genai(genai, key)
        preferred = GEMINI_MODEL
        from google.generativeai.types import RequestOptions  # type: ignore

        try:
            model = genai.GenerativeModel(
                model_name=preferred,
                system_instruction=system_instruction,
            )
            resp = model.generate_content(
                user_content,
                request_options=RequestOptions(timeout=GEMINI_LEGACY_TIMEOUT_SECS),
            )
            txt = getattr(resp, "text", None) or ""
        except Exception:
            picked = _pick_legacy_generate_model(genai)
            if picked and picked != preferred and "flash" in picked.lower():
                model = genai.GenerativeModel(
                    model_name=picked,
                    system_instruction=system_instruction,
                )
                resp = model.generate_content(
                    user_content,
                    request_options=RequestOptions(timeout=GEMINI_LEGACY_TIMEOUT_SECS),
                )
                txt = getattr(resp, "text", None) or ""
            else:
                raise
        return str(txt).strip()

    def run_groq() -> str:
        return _groq_chat_completion(system_instruction, user_content, gk).strip()

    def run_openrouter() -> str:
        return _openrouter_chat_completion(system_instruction, user_content, ok).strip()

    def run_venice() -> str:
        return _venice_chat_completion(system_instruction, user_content, vk).strip()

    result = _call_with_failover(
        primary_provider=prov,
        gemini_key=key,
        groq_key=groq_api_key or "",
        openrouter_key=openrouter_api_key or "",
        venice_key=venice_api_key or "",
        gemini_sdk_available=gemini_sdk_available,
        run_gemini=run_gemini,
        run_groq=run_groq,
        run_openrouter=run_openrouter,
        run_venice=run_venice,
        audit=False,
    )
    if _is_ai_unavailable_response(result) or _is_low_quality_ai_response(result):
        return _offline_incident_summary(
            error_details,
            preceding_logs,
            software_version,
            ai_fallback=bool(result and not _is_ai_unavailable_response(result)),
        )
    return rewrite_defect_ticket_software_version(result, sv)


def generate_full_audit(
    incidents_data: str,
    software_version: str,
    api_key: str,
    *,
    ai_provider: str | None = None,
    groq_api_key: str = "",
    openrouter_api_key: str = "",
    venice_api_key: str = "",
) -> str:
    prov = _normalize_ai_provider(ai_provider)
    key = (api_key or "").strip()
    gk = (groq_api_key or "").strip()
    ok = (openrouter_api_key or "").strip()
    vk = (venice_api_key or "").strip()
    if prov == AI_PROVIDER_GROQ:
        if not gk:
            return "⚠️ Groq API key not configured."
    elif prov == AI_PROVIDER_OPENROUTER:
        if not ok:
            return "⚠️ OpenRouter API key not configured."
    elif prov == AI_PROVIDER_VENICE:
        if not vk:
            return "⚠️ Venice API key not configured."
    elif not key:
        return "⚠️ Analysis API key not configured."

    try:
        from google import genai as genai_new  # type: ignore
    except Exception:
        genai_new = None
    if genai_new is None:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", FutureWarning)
                import google.generativeai as genai  # type: ignore
        except Exception:
            genai = None
    else:
        genai = None

    gemini_sdk_available = genai_new is not None or genai is not None
    if prov == AI_PROVIDER_GEMINI and not gemini_sdk_available:
        return "Session audit unavailable (Gemini SDK not installed)."

    payload = (incidents_data or "").strip()
    if not payload:
        payload = "(No incidents provided.)"

    sv = (software_version or "").strip() or PRODUCT_VERSION_PLACEHOLDER
    system_instruction = _compose_system_instruction(SYSTEM_PROMPT_AUDIT, sv)
    user_payload = f"ALL SCANNED ERRORS:\n{payload}"

    def run_gemini() -> str:
        if genai_new is not None:
            client = _genai_developer_client(genai_new, key)
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=user_payload,
                config=_new_sdk_content_config(system_instruction),
            )
            txt = getattr(resp, "text", None) or ""
        else:
            assert genai is not None
            _configure_legacy_genai(genai, key)
            preferred = GEMINI_MODEL
            from google.generativeai.types import RequestOptions  # type: ignore

            try:
                model = genai.GenerativeModel(
                    model_name=preferred,
                    system_instruction=system_instruction,
                )
                resp = model.generate_content(
                    user_payload,
                    request_options=RequestOptions(timeout=GEMINI_LEGACY_TIMEOUT_SECS),
                )
                txt = getattr(resp, "text", None) or ""
            except Exception:
                picked = _pick_legacy_generate_model(genai)
                if picked and picked != preferred and "flash" in picked.lower():
                    model = genai.GenerativeModel(
                        model_name=picked,
                        system_instruction=system_instruction,
                    )
                    resp = model.generate_content(
                        user_payload,
                        request_options=RequestOptions(timeout=GEMINI_LEGACY_TIMEOUT_SECS),
                    )
                    txt = getattr(resp, "text", None) or ""
                else:
                    raise
        return str(txt).strip()

    def run_groq() -> str:
        return _groq_chat_completion(system_instruction, user_payload, gk).strip()

    def run_openrouter() -> str:
        return _openrouter_chat_completion(system_instruction, user_payload, ok).strip()

    def run_venice() -> str:
        return _venice_chat_completion(system_instruction, user_payload, vk).strip()

    result = _call_with_failover(
        primary_provider=prov,
        gemini_key=key,
        groq_key=groq_api_key or "",
        openrouter_key=openrouter_api_key or "",
        venice_key=venice_api_key or "",
        gemini_sdk_available=gemini_sdk_available,
        run_gemini=run_gemini,
        run_groq=run_groq,
        run_openrouter=run_openrouter,
        run_venice=run_venice,
        audit=True,
    )
    if _is_ai_unavailable_response(result):
        return result
    return rewrite_defect_ticket_software_version(result, sv)

