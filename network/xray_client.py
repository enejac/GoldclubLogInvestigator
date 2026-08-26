"""
Xray Cloud API helpers (phase 2) for reading Manual Test Steps.

Credentials (env only — never ship in lite exe):

* ``XRAY_CLIENT_ID`` / ``XRAY_CLIENT_SECRET`` — Xray Cloud API key pair
* Optional ``XRAY_BASE_URL`` (default ``https://xray.cloud.getxray.app``)

Until credentials exist, ``fetch_test_steps`` returns a soft error and the local
recipe under ``automation/xray_recipes/`` remains authoritative for execution.

``submit_test_execution`` is a stub that refuses to write until recipes + oracle
asserts are stable (see plan).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

_DEFAULT_XRAY = "https://xray.cloud.getxray.app"


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def xray_configured() -> bool:
    return bool(_env("XRAY_CLIENT_ID") and _env("XRAY_CLIENT_SECRET"))


def _authenticate(*, timeout: float = 20.0) -> tuple[bool, str]:
    client_id = _env("XRAY_CLIENT_ID")
    client_secret = _env("XRAY_CLIENT_SECRET")
    if not client_id or not client_secret:
        return False, "XRAY_CLIENT_ID / XRAY_CLIENT_SECRET not set"
    base = (_env("XRAY_BASE_URL") or _DEFAULT_XRAY).rstrip("/")
    url = f"{base}/api/v2/authenticate"
    payload = json.dumps(
        {"client_id": client_id, "client_secret": client_secret}
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "GoldclubLogInvestigator/xray-repro",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            token = resp.read().decode("utf-8", errors="replace").strip().strip('"')
    except urllib.error.HTTPError as e:
        return False, f"auth HTTP {e.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return False, str(e)
    if not token:
        return False, "empty Xray token"
    return True, token


def fetch_test_steps(issue_key: str, *, timeout: float = 30.0) -> dict[str, Any]:
    """
    Fetch Manual Test Steps for an Xray Test issue (informational).

    Does **not** drive the bot — local YAML recipes remain the executable plan.
    """
    key = (issue_key or "").strip().upper()
    if not key:
        return {"ok": False, "error": "empty issue key"}
    if not xray_configured():
        return {
            "ok": False,
            "error": "Xray credentials not configured (XRAY_CLIENT_ID/SECRET)",
            "key": key,
        }

    ok, token_or_err = _authenticate(timeout=min(timeout, 20.0))
    if not ok:
        return {"ok": False, "error": token_or_err, "key": key}
    token = token_or_err

    base = (_env("XRAY_BASE_URL") or _DEFAULT_XRAY).rstrip("/")
    # Xray Cloud GraphQL — getTest steps
    gql = {
        "query": (
            "query($jql: String!) {"
            " getTests(jql: $jql, limit: 1) {"
            "  results { issueId jira(fields: [\"key\",\"summary\"]) "
            "   steps { id action data result } } } }"
        ),
        "variables": {"jql": f"key = {key}"},
    }
    url = f"{base}/api/v2/graphql"
    req = urllib.request.Request(
        url,
        data=json.dumps(gql).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "GoldclubLogInvestigator/xray-repro",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")[:400]
        return {"ok": False, "error": f"HTTP {e.code}: {err}", "key": key}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        return {"ok": False, "error": str(e), "key": key}

    if data.get("errors"):
        return {"ok": False, "error": str(data["errors"])[:500], "key": key}

    results = (
        ((data.get("data") or {}).get("getTests") or {}).get("results") or []
    )
    if not results:
        return {"ok": True, "key": key, "steps": [], "note": "no Xray steps returned"}

    steps_raw = results[0].get("steps") or []
    steps: list[dict[str, str]] = []
    for s in steps_raw:
        if not isinstance(s, dict):
            continue
        steps.append(
            {
                "id": str(s.get("id") or ""),
                "action": str(s.get("action") or ""),
                "data": str(s.get("data") or ""),
                "result": str(s.get("result") or ""),
            }
        )
    return {"ok": True, "key": key, "steps": steps}


def submit_test_execution(
    issue_key: str,
    *,
    status: str,
    comment: str = "",
) -> dict[str, Any]:
    """
    Intentionally disabled until recipe + :8090 asserts are stable.

    Callers should attach evidence packs manually / via a later toggle.
    """
    _ = (issue_key, status, comment)
    return {
        "ok": False,
        "error": (
            "Xray Test Execution upload is disabled until automated verdicts "
            "are proven stable (local BUG-TEST/DEV pack only)"
        ),
    }
