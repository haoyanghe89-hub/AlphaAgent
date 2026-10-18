import importlib.util
import os
import subprocess
from pathlib import Path

import pytest


def startup_module():
    script = Path(__file__).resolve().parents[3] / "scripts/start_alphaagent_background.py"
    spec = importlib.util.spec_from_file_location("background_startup", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_windowless_supervisor_keeps_child_exit_status_and_existing_startup(tmp_path, monkeypatch):
    module = startup_module()
    monkeypatch.setenv("SystemRoot", "C:/Windows")
    recorded = {}

    def child(command, **options):
        recorded.update(command=command, **options)
        options["stdout"].write(b"startup failure retained in log\n")
        return subprocess.CompletedProcess(command, 17)

    monkeypatch.setattr(module.subprocess, "run", child)
    assert module.launch(tmp_path) == 17
    assert recorded["command"][-3:] == [
        str(tmp_path / "start-alphaagent.ps1"),
        "-NoBrowser",
        "-TaskMode",
    ]
    assert recorded["creationflags"] == subprocess.CREATE_NO_WINDOW
    assert recorded["stdin"] == subprocess.DEVNULL
    assert recorded["stderr"] == subprocess.STDOUT
    assert recorded["cwd"] == tmp_path
    assert b"startup failure" in (tmp_path / "logs/alphaagent-startup.log").read_bytes()


@pytest.mark.skipif(os.name != "nt", reason="Windows-only startup launcher")
def test_real_powershell_has_no_console_and_failure_reaches_scheduler(tmp_path):
    (tmp_path / "start-alphaagent.ps1").write_text(
        "Add-Type 'using System; using System.Runtime.InteropServices; "
        'public class ConsoleProbe { [DllImport("kernel32.dll")] '
        "public static extern IntPtr GetConsoleWindow(); }'\n"
        "if ([ConsoleProbe]::GetConsoleWindow() -ne [IntPtr]::Zero) { exit 23 }\n"
        'Write-Output "NO_CONSOLE"\nexit 17\n',
        encoding="utf-8",
    )
    assert startup_module().launch(tmp_path) == 17
    assert b"NO_CONSOLE" in (tmp_path / "logs/alphaagent-startup.log").read_bytes()
