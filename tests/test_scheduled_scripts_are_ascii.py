"""Scripts launched by Task Scheduler must be plain ASCII.

Windows PowerShell 5.1 reads a script with no byte-order mark using the system
ANSI code page, not UTF-8. A single non-ASCII character in a UTF-8 file is
therefore mis-decoded, and when it lands inside a string literal the parser
fails outright:

    Unexpected token 'the' in expression or statement.
    The string is missing the terminator: ".

That happened on 2026-08-18 to an em dash in a Write-Host string. The file
parsed cleanly under pwsh 7, so a syntax check on the development shell said
nothing — only running it under 5.1 revealed it.

These scripts run under 5.1 deliberately: its path
(%WINDIR%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe) never moves,
while the pwsh 7 install on this machine sits under a versioned Store path that
its next update would invalidate. Choosing the stable interpreter means
accepting its encoding rule, so the rule is enforced here rather than
rediscovered on a morning when the session does not start.
"""

from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: Only the scripts a scheduled task loads. Scripts a human runs from pwsh 7
#: are free to use whatever characters they like.
#:
#: "Loads", not "invokes": a dot-sourced file is parsed by the same 5.1 host
#: under the same code page, so it inherits the rule from the script that
#: sources it rather than from how it is launched.
#:
#: The list was two entries while five scheduled tasks ran run_ers_stage.ps1
#: and a sixth ran run_scalping_session_validation.ps1. Both carried em dashes,
#: unnoticed because they sat in comment blocks, which the parser tolerates --
#: the failure only appears when a mis-decoded character lands inside a string.
#: They are covered now so that stays luck-free.
SCHEDULED_SCRIPTS = (
    "scripts/run_daily_orb_automation.ps1",
    "scripts/enable_orb_clock_selfheal.ps1",
    "scripts/run_ers_stage.ps1",
    "scripts/run_scalping_session_validation.ps1",
    "scripts/lib/utf8_log.ps1",
)


@pytest.mark.parametrize("relative", SCHEDULED_SCRIPTS)
def test_scheduled_script_is_pure_ascii(relative):
    path = PROJECT_ROOT / relative
    assert path.is_file(), f"{relative} is missing"

    text = path.read_text(encoding="utf-8")
    offenders = [
        (number, line.strip(), sorted({hex(ord(c)) for c in line if ord(c) > 127}))
        for number, line in enumerate(text.splitlines(), 1)
        if any(ord(c) > 127 for c in line)
    ]

    assert not offenders, (
        f"{relative} contains non-ASCII characters. Windows PowerShell 5.1 "
        f"mis-decodes them and can fail to parse the file:\n"
        + "\n".join(f"  line {n}: {chars} in {line[:70]}" for n, line, chars in offenders)
    )


@pytest.mark.parametrize("relative", SCHEDULED_SCRIPTS)
def test_scheduled_script_has_no_bom(relative):
    # The other half of the same trap: a BOM would make 5.1 read it correctly,
    # but the repository convention is ASCII without one, and a BOM added to
    # "fix" an em dash would hide the real constraint from the next reader.
    assert not (PROJECT_ROOT / relative).read_bytes().startswith(b"\xef\xbb\xbf")
