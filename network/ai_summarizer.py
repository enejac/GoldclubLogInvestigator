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
Format the title exactly as (use SOFTWARE_VERSION verbatim — product label and core build, e.g. ``JinLong_v2.0.20.0``):
{software_version} => [Game Name] => [Short Summary]

- Game Name: Identify from logs (e.g., BigSafari_HnW).
- Summary: Concise technical failure.

### OUTPUT SECTIONS:
Title: [Follow formula above]
Key details: [Technical bullets]
Actual result: [Failure description]
Expected result: [Correct behavior]

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

### Actual result
[A paragraph describing the current unstable or broken state of the build based on the session logs. e.g., "Throughout the test session, the build exhibited systemic resource disposal failures..."]

### Expected result
[A paragraph describing the healthy, expected behavior for the system as a whole. e.g., "The system should maintain stability across all game transitions, cleanly deallocate resources, and operate without recurring fatal exceptions."]

Write objectively throughout. Do not use first-person phrasing or disclaimers such as
"Based on my AI analysis", "As an AI", or similar — especially in Actual result and Expected result.
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


def _dual_failure_message(
    e1: BaseException | None,
    e2: BaseException | None,
    *,
    audit: bool,
) -> str:
    # Keep UI concise when both providers fail (no raw backend stack/details).
    return "Limit reached. Please try again later."


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

    user_content = f"""Analyze the following incident payload. Base **LOG SEQUENCE** strictly on preceding_logs when present.

--- preceding_logs ({len(prev_lines)} chronological line(s), oldest → newest) ---
{preceding_block}

--- error_details ---
{error_block}
"""
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
        audit=True,
    )

