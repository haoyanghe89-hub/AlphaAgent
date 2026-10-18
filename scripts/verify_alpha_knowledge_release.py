"""Offline public knowledge release verification; no network or extraction."""

import argparse
import json

from marketpulse.quant.knowledge.release import verify_archive

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("archive")
    print(json.dumps(verify_archive(parser.parse_args().archive), ensure_ascii=False))
