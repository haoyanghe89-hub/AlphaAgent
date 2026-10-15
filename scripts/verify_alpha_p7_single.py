"""Fresh full industry/funding demonstration under the unchanged shared budget."""

import asyncio
import json
import threading
from pathlib import Path
from uuid import uuid4

from evaluate_alpha_p7 import repository

from marketpulse.config import Settings
from marketpulse.quant.decision import medium
from marketpulse.quant.decision.contracts import ResearchRequest
from marketpulse.quant.decision.store import fingerprint


def main():
    stage = Path(".phase3-quant-o/decision-p6-p7-real-01")
    repo = repository(Path(".phase3-quant-o/alpha-history-real-02").resolve())
    assert not any(s["status"] == "RUNNING" for s in repo.list())
    request = ResearchRequest(
        request_key=uuid4(),
        scope="single",
        entity="中微公司",
        research_mode="medium_term",
        include_flows=True,
        include_industry=True,
        question="研究未来三个月的经营变化与现金回收，结合制造需求和分渠道资金，比较支持与反证，并给出明确的有条件方向。",
    )
    state, _ = repo.create(request.model_dump(mode="json"), fingerprint())
    (stage / "single-bounded-created.json").write_text(
        json.dumps(dict(run_id=state["run_id"])), encoding="utf8"
    )
    asyncio.run(
        medium.run(
            repo, state["run_id"], threading.Event(), Settings.from_env(require_api_key=False)
        )
    )
    result = repo.get(state["run_id"])
    (stage / "single-bounded-final.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8"
    )
    print(
        json.dumps(
            dict(
                run_id=result["run_id"],
                status=result["status"],
                error=result.get("error"),
                model_calls=result.get("model_calls"),
                tokens=result.get("tokens"),
                elapsed=result.get("elapsed_seconds"),
            ),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
