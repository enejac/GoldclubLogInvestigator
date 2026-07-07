#!/usr/bin/env python3
"""
Log Investigator — main entry point.

Recursively scans Goldclub log roots, classifies errors with severity and game
context, estimates first-cause lines, and writes Markdown or CSV reports.

Suitable for later wrapping in Streamlit/PyQt: keep CLI thin and reuse
``scanner``, ``parser``, and ``reporter`` modules.
"""

from __future__ import annotations

import argparse
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Sequence

# Local imports: ensure project directory is importable
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from config import DEFAULT_SCAN_ROOTS  # noqa: E402
from parser import Incident, parse_log_file_safe  # noqa: E402
from reporter import write_report  # noqa: E402
from scanner import ScanRootError, collect_log_files  # noqa: E402


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Log Investigator — scan logs, classify incidents, export report.",
    )
    p.add_argument(
        "paths",
        nargs="*",
        help=(
            "Optional log root directories. Defaults to entries in "
            "config.DEFAULT_SCAN_ROOTS when omitted."
        ),
    )
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("goldclub_log_report.md"),
        help="Output file path (.md or .csv).",
    )
    p.add_argument(
        "--format",
        choices=("markdown", "csv"),
        default=None,
        help="Report format (default: infer from output extension).",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Thread pool size for parsing files (I/O bound).",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    return p.parse_args(argv)


def _infer_format(output: Path, fmt: str | None) -> str:
    if fmt:
        return fmt
    suf = output.suffix.lower()
    if suf == ".csv":
        return "csv"
    return "markdown"


def run_analysis(
    roots: Sequence[str | Path],
    output: Path,
    output_format: str,
    workers: int,
) -> list[Incident]:
    """
    Discover log files, parse them concurrently, return merged incidents.

    This function is the core orchestration API for a future GUI.
    """
    try:
        files = collect_log_files(roots)
    except ScanRootError as e:
        logging.error("%s", e)
        write_report(
            [],
            output,
            fmt=output_format,
            title="Log Investigator",
        )
        logging.info("Wrote empty report after scan root error to %s", output)
        return []

    logging.info("Found %d log/text files under %d root(s).", len(files), len(roots))

    incidents: list[Incident] = []

    if not files:
        write_report(
            incidents,
            output,
            fmt=output_format,
            title="Log Investigator",
        )
        logging.info("No log files found; wrote empty report to %s", output)
        return incidents
    workers = max(1, workers)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(parse_log_file_safe, fp): fp for fp in files}
        for fut in as_completed(futures):
            path = futures[fut]
            try:
                incidents.extend(fut.result().incidents)
            except Exception:
                logging.exception("Worker failed for %s", path)

    incidents.sort(key=lambda i: (i.log_file_path, i.line_number))
    write_report(
        incidents,
        output,
        fmt=output_format,
        title="Log Investigator",
    )
    logging.info("Wrote %d incidents to %s", len(incidents), output)
    return incidents


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    roots = list(args.paths) if args.paths else list(DEFAULT_SCAN_ROOTS)
    output_fmt = _infer_format(args.output, args.format)

    try:
        run_analysis(roots, args.output, output_fmt, args.workers)
    except KeyboardInterrupt:
        logging.error("Interrupted.")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
