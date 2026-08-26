"""
Read-only Jira Cloud helpers for Log Investigator.

Credentials (never baked into the lite exe):

* ``JIRA_BASE_URL`` — e.g. ``https://winsytemsintl.atlassian.net``
* ``JIRA_EMAIL`` — Atlassian account email
* ``JIRA_API_TOKEN`` — API token from id.atlassian.com

If any are missing, callers receive ``{"ok": False, "error": "..."}`` and should
fall back to local recipes.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from base64 import b64encode
from typing import Any

from config import JIRA_BASE_URL as CONFIG_JIRA_BASE


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def jira_configured() -> bool:
    base = _env("JIRA_BASE_URL") or (CONFIG_JIRA_BASE or "").strip()
    email = _env("JIRA_EMAIL")
    token = _env("JIRA_API_TOKEN")
    return bool(base and email and token)


def _auth_header(email: str, token: str) -> str:
    raw = f"{email}:{token}".encode("utf-8")
    return "Basic " + b64encode(raw).decode("ascii")


def fetch_issue_metadata(issue_key: str, *, timeout: float = 25.0) -> dict[str, Any]:
    """
    Fetch summary / type / status / links for an issue.

    Returns a plain dict always; ``ok`` is False when credentials or HTTP fail.
    """
    key = (issue_key or "").strip().upper()
    if not key:
        return {"ok": False, "error": "empty issue key"}

    base = (_env("JIRA_BASE_URL") or (CONFIG_JIRA_BASE or "").strip()).rstrip("/")
    email = _env("JIRA_EMAIL")
    token = _env("JIRA_API_TOKEN")
    if not base or not email or not token:
        return {
            "ok": False,
            "error": (
                "Jira credentials not configured "
                "(set JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN)"
            ),
            "key": key,
        }

    # Prefer site hostname form used by Atlassian cloud REST.
    if "atlassian.net" in base and "/ex/jira/" not in base:
        api = f"{base}/rest/api/3/issue/{key}"
    else:
        api = f"{base}/rest/api/3/issue/{key}"

    fields = "summary,status,issuetype,issuelinks,description,environment,resolution"
    url = f"{api}?fields={fields}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": _auth_header(email, token),
            "Accept": "application/json",
            "User-Agent": "GoldclubLogInvestigator/jira-repro",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            data = json.loads(body)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")[:400]
        return {"ok": False, "error": f"HTTP {e.code}: {err_body}", "key": key}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        return {"ok": False, "error": str(e), "key": key}

    fields_d = data.get("fields") or {}
    status = fields_d.get("status") or {}
    itype = fields_d.get("issuetype") or {}
    resolution = fields_d.get("resolution") or {}
    links: list[dict[str, str]] = []
    for link in fields_d.get("issuelinks") or []:
        if not isinstance(link, dict):
            continue
        for side in ("outwardIssue", "inwardIssue"):
            other = link.get(side)
            if isinstance(other, dict) and other.get("key"):
                of = other.get("fields") or {}
                links.append(
                    {
                        "key": str(other["key"]),
                        "summary": str((of.get("summary") or "")),
                        "type": str(((of.get("issuetype") or {}).get("name") or "")),
                        "relation": side,
                    }
                )
    return {
        "ok": True,
        "key": data.get("key") or key,
        "summary": fields_d.get("summary"),
        "status": (status.get("name") if isinstance(status, dict) else None),
        "issuetype": (itype.get("name") if isinstance(itype, dict) else None),
        "resolution": (resolution.get("name") if isinstance(resolution, dict) else None),
        "environment": fields_d.get("environment"),
        "description": fields_d.get("description"),
        "issuelinks": links,
        "browse": f"{base}/browse/{data.get('key') or key}",
    }
