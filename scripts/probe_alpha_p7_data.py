"""Private read-only P6/P7 capability probes; credentials never leave transport."""

import hashlib
import json
import sqlite3
from pathlib import Path

import requests

from marketpulse.quant.decision.tushare import load_token, records


def main():
    stage = Path(".phase3-quant-o/decision-p6-p7-real-01")
    stage.mkdir(parents=True, exist_ok=True)
    root = Path(".phase3-quant-o/alpha-history-real-02")
    with sqlite3.connect(root / "metadata.sqlite") as db:
        rows = db.execute("select run_id,status,payload from inv_quant_research_run").fetchall()
        assert not any(status == "RUNNING" for _, status, _ in rows), "active task exists"
        baseline = {
            rid: hashlib.sha256(p.encode()).hexdigest() for rid, s, p in rows if s == "COMPLETED"
        }
        if not (stage / "baseline.json").exists():
            (stage / "baseline.json").write_text(json.dumps(baseline), encoding="utf8")
            with sqlite3.connect(stage / "before.sqlite") as target:
                db.backup(target)
    token = load_token()
    assert token
    probes = [
        ("index_member_all", dict(l3_code="850812.SI"), ""),
        ("sw_daily", dict(ts_code="850812.SI", start_date="20260901", end_date="20261014"), ""),
        (
            "margin_detail",
            dict(ts_code="688012.SH", start_date="20260901", end_date="20261014"),
            "",
        ),
        ("adj_factor", dict(ts_code="688012.SH", start_date="20260901", end_date="20261014"), ""),
        ("dividend", dict(ts_code="688012.SH"), ""),
    ]
    results = []
    catalog = requests.post(
        "https://api.tushare.pro",
        json=dict(
            api_name="index_classify", token=token, params=dict(level="L3", src="SW2021"), fields=""
        ),
        timeout=25,
    ).json()
    for row in records(catalog):
        if row["industry_name"] in ("半导体设备", "集成电路制造"):
            for flag in ("Y", "N"):
                probes.append(
                    ("index_member_all", dict(l3_code=row["index_code"], is_new=flag), "")
                )
    for api, params, fields in probes:
        try:
            response = requests.post(
                "https://api.tushare.pro",
                json=dict(api_name=api, token=token, params=params, fields=fields),
                timeout=(8, 25),
            )
            response.raise_for_status()
            raw = response.content
            assert token.encode() not in raw
            packet = response.json()
            if packet.get("code") == 0:
                values = records(packet)
                path = stage / (
                    "probe-"
                    + api
                    + "-"
                    + hashlib.sha256(json.dumps(params).encode()).hexdigest()[:8]
                    + ".json"
                )
                path.write_bytes(raw)
                results.append(
                    dict(
                        api=api,
                        params=params,
                        code=0,
                        count=len(values),
                        fields=packet["data"]["fields"],
                        sample=values[:2],
                    )
                )
            else:
                results.append(
                    dict(api=api, code=packet.get("code"), message=str(packet.get("msg"))[:160])
                )
        except Exception as error:
            results.append(dict(api=api, error=type(error).__name__))
    (stage / "data-probes.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf8"
    )
    print(json.dumps(dict(baseline=len(baseline), probes=results), ensure_ascii=False))


if __name__ == "__main__":
    main()
