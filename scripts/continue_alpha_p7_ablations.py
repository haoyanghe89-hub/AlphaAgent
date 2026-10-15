"""Second bounded eval batch, reusing exact completed first-batch checkpoints.

The first batch's exhausted budget is not increased. Both costs remain visible.
"""

import asyncio
import json
from pathlib import Path

from evaluate_alpha_p7 import Evaluation, ablations, repository


def main():
    stage = Path(".phase3-quant-o/decision-p6-p7-real-01")
    previous = json.loads((stage / "ablations-v2/ablations-state.json").read_text(encoding="utf8"))
    target = stage / "ablations-batch-02"
    target.mkdir(exist_ok=True)
    job = Evaluation(
        repository(Path(".phase3-quant-o/alpha-history-real-02").resolve()), target, "ablations"
    )
    if not job.state["checkpoints"]:
        job.state["checkpoints"] = previous["checkpoints"]
        job.state["reused_from"] = "ablations-v2/ablations-state.json"
        job.state["previous_batch_cost"] = dict(
            calls=previous["model_calls"],
            tokens=previous["tokens"],
            charged_tokens=previous["charged_tokens"],
        )
        job.save()
    asyncio.run(ablations(job, target))
    result = json.loads((target / "ablations-report.json").read_text(encoding="utf8"))
    result["total_calls_including_first_batch"] = previous["model_calls"] + job.state["model_calls"]
    result["total_tokens_including_first_batch"] = previous["tokens"] + job.state["tokens"]
    result["budget_note"] = (
        "Two separately bounded evaluation batches; "
        "original first-batch failure and costs retained. "
        "Exact same input checkpoints only."
    )
    (target / "ablations-report-total.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8"
    )
    print(
        json.dumps(
            dict(
                total_calls=result["total_calls_including_first_batch"],
                total_tokens=result["total_tokens_including_first_batch"],
            ),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
