"""Defect-ticket text normalize/parse helpers (no Qt)."""

from __future__ import annotations

import re


def strip_markdown_bolding(text: str) -> str:
    return (text or "").replace("**", "")


def normalize_defect_ticket_text(text: str) -> str:
    """Force section headers onto their own lines; fix mid-line glue."""
    t = (strip_markdown_bolding(text) or "").strip()
    if not t:
        return t
    # ``Forced ExitKey details:`` → newline before header
    t = re.sub(
        r"(?i)([^\n])\s*(Key details|Actual result|Expected result)\s*:",
        r"\1\n\n\2:",
        t,
    )
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def first_title_line(body: str) -> str:
    """Title is a single line — never include Key details / ROOT CAUSE blobs."""
    s = (body or "").strip()
    if not s:
        return ""
    s = re.split(r"(?i)\bKey details\s*:", s, maxsplit=1)[0]
    s = re.split(r"(?i)\bActual result\s*:", s, maxsplit=1)[0]
    s = re.split(r"(?i)\bExpected result\s*:", s, maxsplit=1)[0]
    for line in s.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def parse_defect_ticket_sections(text: str) -> dict[str, str]:
    """Split Title / Key details / Actual / Expected from model or offline text."""
    t = normalize_defect_ticket_text(text)

    if re.search(r"(?im)^\s*###\s*Title\s*$", t):
        sections: dict[str, str] = {
            "Title": "",
            "Key details": "",
            "Actual result": "",
            "Expected result": "",
        }
        key_map = {
            "Title": "Title",
            "Key details": "Key details",
            "Actual result": "Actual result",
            "Expected result": "Expected result",
        }
        md_parts = re.split(
            r"(?im)^\s*###\s*(Title|Key details|Actual result|Expected result)\s*$",
            t,
        )
        i = 1
        while i + 1 < len(md_parts):
            header = md_parts[i].strip()
            body = md_parts[i + 1].strip()
            if header in key_map:
                sections[key_map[header]] = body
            i += 2
        sections["Title"] = first_title_line(sections.get("Title", ""))
        return sections

    sections = {
        "Title": "",
        "Key details": "",
        "Actual result": "",
        "Expected result": "",
    }
    parts = re.split(
        r"(?im)^\s*(Title|Key details|Actual result|Expected result)\s*:\s*",
        t,
    )
    i = 1
    while i + 1 < len(parts):
        header = parts[i].strip()
        body = parts[i + 1].strip()
        if header in sections:
            sections[header] = body
        i += 2

    sections["Title"] = first_title_line(sections.get("Title", ""))

    if not sections["Title"] and not sections["Key details"]:
        title_match = re.search(
            r"(?im)^\s*Title\s*:\s*(.*?)(?:\n\s*\n|^\s*Key details\s*:|\bKey details\s*:)",
            t,
            re.DOTALL,
        )
        sections["Title"] = first_title_line(
            title_match.group(1) if title_match else ""
        )
        key_match = re.search(
            r"(?im)(?:^|\b)Key details\s*:\s*(.*?)(?:\n\s*\n|^\s*Actual result\s*:|\bActual result\s*:)",
            t,
            re.DOTALL,
        )
        sections["Key details"] = key_match.group(1).strip() if key_match else ""
        actual_match = re.search(
            r"(?im)(?:^|\b)Actual result\s*:\s*(.*?)(?:\n\s*\n|^\s*Expected result\s*:|\bExpected result\s*:)",
            t,
            re.DOTALL,
        )
        sections["Actual result"] = actual_match.group(1).strip() if actual_match else ""
        expected_match = re.search(
            r"(?im)(?:^|\b)Expected result\s*:\s*(.*)", t, re.DOTALL
        )
        sections["Expected result"] = (
            expected_match.group(1).strip() if expected_match else ""
        )

    return sections
