"""Local operator intake/review tool. No URL fetching and no model or publish call."""

import argparse
import json
from pathlib import Path

from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.investigation.persistence.base import (
    create_investigation_engine,
    create_session_factory,
)
from marketpulse.investigation.server import _upgrade_database
from marketpulse.quant.knowledge.rule_store import RuleStore
from marketpulse.quant.knowledge.store import KnowledgeStore
from marketpulse.quant.knowledge.supplements import SupplementStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["submit", "assess", "link", "get", "list"])
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--id")
    parser.add_argument("--rule")
    parser.add_argument("--version")
    args = parser.parse_args()
    if not args.data.resolve().is_relative_to(Path.cwd().resolve() / ".phase3-quant-o"):
        raise ValueError("private workspace data path required")
    url = "sqlite:///" + (args.data / "metadata.sqlite").resolve().as_posix()
    _upgrade_database(url)
    engine = create_investigation_engine(url)
    try:
        store = SupplementStore(
            RuleStore(
                KnowledgeStore(
                    create_session_factory(engine),
                    LocalContentAddressedBlobStorage(args.data / "blobs"),
                )
            )
        )
        data = json.loads(args.input.read_text(encoding="utf-8-sig")) if args.input else None
        if args.command == "submit":
            result = store.submit(data)
        elif args.command == "assess":
            result = store.assess(args.id, data)
        elif args.command == "link":
            result = store.link_published_rule(args.id, args.rule, args.version)
        elif args.command == "get":
            result = store.get(args.id)
        else:
            result = store.list()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
