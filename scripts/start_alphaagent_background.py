"""Windowless Windows task supervisor; reuse the existing checked startup script."""

import os
import subprocess
from pathlib import Path


def launch(root: Path) -> int:
    shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    logs = root / "logs"
    logs.mkdir(exist_ok=True)
    # A GUI-subsystem entrypoint plus CREATE_NO_WINDOW avoids console-host delegation.
    with (logs / "alphaagent-startup.log").open("ab", buffering=0) as stream:
        try:
            return subprocess.run(
                [
                    str(shell),
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-WindowStyle",
                    "Hidden",
                    "-ExecutionPolicy",
                    "RemoteSigned",
                    "-File",
                    str(root / "start-alphaagent.ps1"),
                    "-NoBrowser",
                    "-TaskMode",
                ],
                cwd=root,
                stdin=subprocess.DEVNULL,
                stdout=stream,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
                check=False,
            ).returncode
        except OSError:
            stream.write(b"Could not launch AlphaAgent startup process.\n")
            return 1


if __name__ == "__main__":
    raise SystemExit(launch(Path(__file__).resolve().parents[1]))
