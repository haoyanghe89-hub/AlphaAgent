"""Back up the existing local AlphaAgent instance without starting research."""

from pathlib import Path

from marketpulse.investigation.operations.local_backup import main

if __name__ == "__main__":
    main(Path(__file__).resolve().parents[1])
