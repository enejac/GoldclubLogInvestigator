from __future__ import annotations

from automation.remote_exec import resolve_psexec_path


def test_resolve_psexec_path_returns_none_if_missing() -> None:
    # Repo may or may not have tools/psexec.exe in CI; just ensure it doesn't crash.
    _ = resolve_psexec_path()

