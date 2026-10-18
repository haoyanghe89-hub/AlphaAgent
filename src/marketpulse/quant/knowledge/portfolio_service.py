"""Bounded grouped public research and private account calculation, with durable recovery."""

import asyncio
import re
import time
from datetime import UTC, datetime
from uuid import UUID, uuid5

from ..contracts import digest
from ..decision.next_session.labels import SHANGHAI
from ..decision.tools import SourceClient, Stopped
from . import advisor_quotes
from .advisor_engine import sell_rules
from .portfolio_contracts import PortfolioRequest
from .portfolio_engine import build, single_report
from .portfolio_store import account_budget, groups, materials
from .research_contracts import InvestmentRequest
from .research_service import run as research_run
from .research_store import InvestmentStore


async def run(
    repo,
    rules,
    run_id,
    stop,
    settings,
    *,
    research_options=None,
    quote_collector=advisor_quotes.collect,
):
    state = repo.get(run_id, with_data=False)
    started = time.monotonic()
    previous = state.get("elapsed_seconds", 0)

    def save():
        state["elapsed_seconds"] = previous + time.monotonic() - started
        repo.save(state)

    def check():
        if stop.is_set() or previous + time.monotonic() - started >= 900:
            raise Stopped("账户研究达到时间上限或已停止，已取得的材料与预算保留。")

    async def bounded(work):
        task = asyncio.create_task(work)
        try:
            return await asyncio.wait_for(
                asyncio.shield(task), max(0.1, 900 - previous - time.monotonic() + started)
            )
        except (TimeoutError, asyncio.CancelledError):
            stop.set()
            await task
            check()
            raise

    try:
        request = PortfolioRequest.model_validate(state["request"])
        created = datetime.fromisoformat(state["created_at"]).astimezone(SHANGHAI).date()
        for a in request.assets:
            if a.buy_date and (a.buy_date > created or a.buy_date == created and a.sellable_shares):
                raise ValueError("买入日期不能晚于咨询；今日首次买入的A股可卖数量应为零。")
        state.update(status="RUNNING", error=None, stage="RESEARCH")
        state.setdefault("budget", account_budget(request))
        state.setdefault("budget_policy_version", "bounded-public-groups-v1")
        save()
        check()
        public = InvestmentStore(repo.base)
        if "research_groups" not in state:
            state["research_groups"] = []
            for i, g in enumerate(groups(request)):
                key = uuid5(UUID(request.request_key.hex), "account-public-" + str(i))
                state["research_groups"].append(dict(run_id=public.prefix + key.hex, **g))
            save()
        for binding in state["research_groups"]:
            if not public.exists(binding["run_id"]):
                definition = InvestmentRequest(
                    schema_version="investment-research-v4",
                    request_key=UUID(binding["run_id"].removeprefix(public.prefix)),
                    entities=binding["entities"],
                    horizon=binding["horizon"],
                    horizon_sessions=20 if binding["horizon"] == "SWING" else 60,
                    profile=None,
                ).model_dump(mode="json")
                public.create(definition)
        for binding in state["research_groups"]:
            check()
            study = public.get(binding["run_id"])
            if study["status"] == "COMPLETED":
                continue
            others = [
                public.get(g["run_id"], with_data=False)
                for g in state["research_groups"]
                if g != binding
            ]
            remaining = dict(
                max_model_calls=min(
                    24,
                    state["budget"]["max_model_calls"]
                    - sum(s.get("model_calls", 0) for s in others),
                ),
                max_tokens=min(
                    60000,
                    state["budget"]["max_tokens"] - sum(s.get("charged_tokens", 0) for s in others),
                ),
                max_seconds=900,
            )
            if (
                remaining["max_model_calls"] <= study["model_calls"]
                or remaining["max_tokens"] <= study["charged_tokens"]
            ):
                raise ValueError("账户公开研究共享预算不足；不可重置预算或省略持仓。")
            study["config"]["budget"] = remaining
            if not any(c.get("role") == "Analyst" for c in study["calls"]):
                # Keep original facts; route new analysis to selected published methods only.
                study["config"]["scoped_analysis"] = True
            public.save(study)
            while study["status"] != "COMPLETED":
                study = await bounded(
                    research_run(
                        public, rules, study["run_id"], stop, settings, **(research_options or {})
                    )
                )
                retries = state["automatic_continuations"].get(study["run_id"], 0)
                correctable = (
                    study["status"] == "INTERRUPTED"
                    and study.get("stage") in {"PLANNER", "ANALYST", "VERIFIER", "WRITER"}
                    and (
                        "输出未通过" in (study.get("error") or "")
                        or "解释仍未通过" in (study.get("error") or "")
                    )
                    and retries < 2
                    and study["charged_tokens"] < remaining["max_tokens"] - 15000
                )
                if not correctable:
                    break
                state["automatic_continuations"][study["run_id"]] = retries + 1
                save()
                check()
            if study["status"] != "COMPLETED":
                raise ValueError(study.get("error") or "分组公开研究需要继续核查，已完成部分保留。")
        studies, sessions = materials(repo, state)
        state["stage"] = "ACCOUNT"
        save()
        check()
        if state.get("quotes_ref"):
            quotes = repo.value(state["quotes_ref"])
        else:

            def collect():
                client = SourceClient(
                    repo, state, save, stop, max_requests=16, budget_profile=dict(max_seconds=900)
                )
                result = {}
                for study in studies:
                    for c in study["data"]["candidates"]:
                        check()
                        single = single_report(study["data"], c["security_id"])
                        try:
                            result[c["security_id"]] = quote_collector(client, single)
                        except (ValueError, OSError):
                            check()
                            result[c["security_id"]] = advisor_quotes.fallback(single)
                return result

            quotes = await bounded(asyncio.to_thread(collect))
            state["quotes_ref"] = repo.blob(quotes)
            save()
        state["issued_at"] = datetime.now(UTC).isoformat()
        trading = {}
        for study in studies:
            for c in study["data"]["candidates"]:
                sid = c["security_id"]
                advisor_quotes.verify(
                    repo, quotes[sid], single_report(study["data"], sid), state["issued_at"]
                )
                trading[sid] = sell_rules(study["data"], repo.raw_bytes, sid)
        answer = build(request, studies, quotes, trading, sessions, state["pinned_routes"])
        answer["quotes"] = quotes
        check()
        state.update(
            status="COMPLETED",
            stage="COMPLETED",
            result_ref=repo.blob(answer),
            result_sha256=digest(answer),
            source_refs=sorted(set(re.findall(r"blob://sha256/[0-9a-f]{64}", str(answer)))),
        )
        save()
    except asyncio.CancelledError:
        stop.set()
        state.update(status="INTERRUPTED", error="服务停止，账户输入、分组材料与预算已保留。")
        save()
        raise
    except Exception as error:
        state.update(
            status="INTERRUPTED",
            error=str(error)[:240]
            if isinstance(error, ValueError)
            else "本轮组合资料未完成，已取得资料和本地输入保留，可继续。",
        )
        save()
    return repo.get(run_id)
