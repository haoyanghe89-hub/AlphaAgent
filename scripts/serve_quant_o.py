"""Local Task O acceptance and configured-model research server over an isolated evidence DB."""

import argparse
import os
from pathlib import Path

import uvicorn
from pydantic import SecretStr

from marketpulse.config import Settings
from marketpulse.investigation.server import create_app

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8830)
    args = parser.parse_args()
    data = args.data.resolve()
    if (
        not data.is_relative_to(Path.cwd().resolve() / ".phase3-quant-o")
        or not (data / "metadata.sqlite").is_file()
    ):
        raise ValueError("existing private Task O acceptance DB required")
    os.environ["INVESTIGATION_BLOB_ROOT"] = str(data / "blobs")
    os.environ["QUANT_CALL_JOURNAL_ROOT"] = str(data / "calls")
    os.environ["INVESTIGATION_SERVE_FRONTEND"] = "true"
    settings = Settings.from_env(require_api_key=False).model_copy(
        update={
            "database_url": SecretStr("sqlite:///" + (data / "metadata.sqlite").as_posix()),
            "review_allow_insecure_loopback": True,
        }
    )
    uvicorn.run(
        create_app(settings=settings), host="127.0.0.1", port=args.port, log_level="warning"
    )
