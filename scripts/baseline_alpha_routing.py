"""Preserve the user-authorized routing-stage baseline without changing live data."""

import hashlib
import json
import sqlite3
from pathlib import Path

stage = Path(".phase3-quant-o/decision-routing-20261013-01")
stage.mkdir(exist_ok=True)
dbpath = Path(".phase3-quant-o/alpha-history-real-02/metadata.sqlite").resolve()
if not (stage / "baseline.json").exists():
    with sqlite3.connect("file:" + dbpath.as_posix() + "?mode=ro", uri=True) as db:
        baseline = {
            r[0]: hashlib.sha256(r[1].encode()).hexdigest()
            for r in db.execute(
                "select run_id,payload from inv_quant_research_run where status='COMPLETED'"
            )
        }
        with sqlite3.connect(stage / "before.sqlite") as target:
            db.backup(target)
    (stage / "baseline.json").write_text(json.dumps(baseline, indent=2), encoding="utf8")
else:
    baseline = json.loads((stage / "baseline.json").read_text(encoding="utf8"))
print(json.dumps({"completed_baseline": len(baseline), "backup": str(stage / "before.sqlite")}))
