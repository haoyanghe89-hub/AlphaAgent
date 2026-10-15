"""Read-only comparison of independently sealed versions; no generated change stories."""

from ..contracts import digest


def facts(state):
    data = state["data"]
    if data["report"]["stage"] == "P6_MARKET":
        return dict(
            metrics=data["metrics"],
            membership=data["membership"],
            **({"market_index": data["market_index"]} if "market_index" in data else {}),
        )
    if data["report"]["stage"] == "P5":
        return dict(members=data["members"], selected=data["selected"])
    return [
        {
            k: e.get(k)
            for k in (
                "subject_id",
                "kind",
                "text",
                "value",
                "unit",
                "period_start",
                "period_end",
                "published_at",
            )
        }
        for e in data.get("evidence", [])
    ]


def normalized(value):
    if isinstance(value, dict):
        return {
            k: normalized(v)
            for k, v in value.items()
            if not k.endswith(("_ref", "_refs", "_ids"))
            and k not in {"evidence_id", "gaps", "universe_ref", "cutoff_at", "fetched_at"}
        }
    if isinstance(value, list):
        return [normalized(v) for v in value]
    return value


def compare(parent, current):
    if current.get("parent_run_id") != parent["run_id"]:
        raise ValueError("comparison parent is not linked")
    before, after = parent["data"]["report"], current["data"]["report"]
    if before["stage"] != after["stage"] and not (
        before["stage"] in {"P2", "P3", "P4", "P4K"} and after["stage"] in {"P2", "P3", "P4", "P4K"}
    ):
        raise ValueError("comparison modes differ")
    evidence_changed = digest(normalized(facts(parent))) != digest(normalized(facts(current)))
    fields = ("summary", "conclusion", "opinion", "candidates")
    changes = [
        dict(field=k, before=before.get(k), after=after.get(k))
        for k in fields
        if before.get(k) != after.get(k)
    ]
    return dict(
        parent=parent["run_id"],
        current=current["run_id"],
        before_cutoff=before.get("cutoff_at"),
        after_cutoff=after.get("cutoff_at"),
        evidence_changed=evidence_changed,
        conclusion_changed=bool(changes),
        changes=changes,
        explanation="本次取得的事实或观察窗口有变化，请对照下方原文与新旧结论。"
        if evidence_changed
        else "本次核查未发现所纳入事实变化。"
        + ("模型表述或取舍有变化，不能归因为新事实。" if changes else "结论维持，已保留本次检查。"),
        original_preserved=True,
    )
