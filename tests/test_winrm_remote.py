from automation.remote_input_agent import _unc_to_remote_local


def test_unc_to_remote_local() -> None:
    assert _unc_to_remote_local(r"\\10.0.0.90\c$\Windows\Temp\foo") == r"C:\Windows\Temp\foo"
