"""Stable local/server entrypoint. Existing SQLite/CAS history is never re-created."""

import argparse
import json
import os
from pathlib import Path

import uvicorn
from pydantic import SecretStr

from marketpulse.config import Settings
from marketpulse.investigation.server import create_app


def configuration(root):
    local = root / "alphaagent.local.json"
    if local.is_file():
        return json.loads(local.read_text(encoding="utf-8-sig"))
    return dict(data=".phase3-quant-o/alpha-history-real-02", port=8839)


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    config = configuration(root)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path(config["data"]))
    parser.add_argument("--port", type=int, default=config.get("port", 8839))
    args = parser.parse_args()
    os.chdir(root)
    data = args.data.resolve()
    if not (data / "metadata.sqlite").is_file() or not (data / "blobs").is_dir():
        raise ValueError("需要已有数据库及配对原文目录，不能创建空库替代历史。")
    if (data / "restore-report.json").exists():
        raise ValueError("恢复演练目录不能作为运行数据目录。")
    os.environ.update(
        INVESTIGATION_BLOB_ROOT=str(data / "blobs"),
        QUANT_CALL_JOURNAL_ROOT=str(data / "calls"),
        INVESTIGATION_SERVE_FRONTEND="true",
        ALPHAAGENT_DAILY_ENABLED="true",
    )
    os.environ.setdefault("MARKETPULSE_ENV_FILE", str(root / ".env"))
    settings = Settings.from_env(require_api_key=False).model_copy(
        update={
            "database_url": SecretStr("sqlite:///" + (data / "metadata.sqlite").as_posix()),
            "review_allow_insecure_loopback": True,
        }
    )
    uvicorn.run(
        create_app(settings=settings), host="127.0.0.1", port=args.port, log_level="warning"
    )
