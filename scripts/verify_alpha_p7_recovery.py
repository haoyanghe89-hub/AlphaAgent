"""Real source/model workflow with a labelled local storage fault, then HTTP resume.

No model result is mocked. The injected fault occurs after the real Planner
checkpoint; the normal service must reuse it and complete the remaining roles.
"""

import asyncio
import json
import threading
from pathlib import Path
from uuid import uuid4

from evaluate_alpha_p7 import repository

from marketpulse.config import Settings
from marketpulse.quant.decision import screening
from marketpulse.quant.decision.contracts import ResearchRequest
from marketpulse.quant.decision.store import fingerprint


def main():
    stage = Path(".phase3-quant-o/decision-p6-p7-real-01")
    repo = repository(Path(".phase3-quant-o/alpha-history-real-02").resolve())
    assert not any(s["status"] == "RUNNING" for s in repo.list())
    request = ResearchRequest(
        request_key=uuid4(),
        scope="industry",
        research_mode="candidates",
        entity="集成电路制造",
        question="核查制造企业经营、现金回收与反证，保留原文。",
    )
    state, _ = repo.create(request.model_dump(mode="json"), fingerprint())
    original_save = repo.save
    injected = False

    def save(value):
        nonlocal injected
        if (
            not injected
            and any(
                c.get("role") == "Planner" and c["status"] == "COMPLETED" for c in value["calls"]
            )
            and value["process"][-1]["key"] == "analyst"
        ):
            injected = True
            raise OSError(
                "P7 labelled local storage fault after real Planner; model output not mocked"
            )
        original_save(value)

    repo.save = save
    asyncio.run(
        screening.run(
            repo, state["run_id"], threading.Event(), Settings.from_env(require_api_key=False)
        )
    )
    before = repo.get(state["run_id"], with_data=False)
    assert injected and before["status"] == "FAILED"
    assert (
        len(
            [
                c
                for c in before["calls"]
                if c.get("role") == "Planner" and c["status"] == "COMPLETED"
            ]
        )
        == 1
    )
    result = dict(
        run_id=state["run_id"],
        fault_kind="INJECTED_LOCAL_STORAGE_FAILURE_AFTER_REAL_PLANNER",
        before=before,
    )
    (stage / "recovery-before.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8"
    )
    print(
        json.dumps(
            dict(
                run_id=state["run_id"],
                status=before["status"],
                completed_planner=1,
                model_calls=before["model_calls"],
                source_calls=sum(not c.get("role") for c in before["calls"]),
            ),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
