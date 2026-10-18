"""Reuse four public research roles; all account-specific advice stays local."""

import asyncio
import re
from datetime import UTC, datetime
from uuid import UUID, uuid5

from ..contracts import digest
from ..decision.next_session.labels import SHANGHAI
from ..decision.tools import SourceClient, Stopped
from . import advisor_quotes
from .advisor_contracts import AdvisorRequest
from .advisor_engine import build_answer, sell_rules
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

    def save():
        repo.save(state)

    try:
        request = AdvisorRequest.model_validate(state["request"])
        created = datetime.fromisoformat(state["created_at"]).astimezone(SHANGHAI).date()
        if request.position.buy_date and request.position.buy_date > created:
            raise ValueError("首次买入日期不能晚于本轮咨询日期。")
        if request.position.buy_date == created and request.position.sellable_shares:
            raise ValueError("今日首次买入的A股尚未交收，可卖股数须为零。")
        state.update(status="RUNNING", error=None, stage="RESEARCH")
        public = InvestmentStore(repo.base)
        if not state.get("research_run_id"):
            key = uuid5(UUID(request.request_key.hex), "holding-public-research-v1")
            definition = InvestmentRequest(
                schema_version="investment-research-v4",
                request_key=key,
                entities=[request.position.entity],
                horizon=request.horizon,
                horizon_sessions=request.horizon_sessions,
                profile=None,
            ).model_dump(mode="json")
            research, _ = public.create(definition)
            state["research_run_id"] = research["run_id"]
            save()
        research = public.get(state["research_run_id"])
        while research["status"] != "COMPLETED":
            research = await research_run(
                public, rules, research["run_id"], stop, settings, **(research_options or {})
            )
            correctable = (
                research["status"] == "INTERRUPTED"
                and research.get("stage") in {"PLANNER", "ANALYST", "VERIFIER", "WRITER"}
                and (
                    "输出未通过" in (research.get("error") or "")
                    or "解释仍未通过" in (research.get("error") or "")
                )
                and research.get("model_calls", 0) < 24
                and research.get("charged_tokens", 0) < 40000
                and research.get("elapsed_seconds", 0) < 850
            )
            if not correctable or stop.is_set() or state.get("automatic_continuations", 0) >= 2:
                break
            # Continue the same archived research; never create a new budget for a failed role.
            state["automatic_continuations"] = state.get("automatic_continuations", 0) + 1
            save()
        if research["status"] != "COMPLETED":
            raise ValueError(research.get("error") or "公开研究尚未完成，资料保留，可继续。")
        if stop.is_set():
            raise Stopped("本轮已停止，研究保留。")
        report = research["data"]
        state["stage"] = "POSITION"
        save()
        if state.get("quote_ref"):
            quoted = repo.value(state["quote_ref"])
        else:
            client = SourceClient(
                repo, state, save, stop, max_requests=4, budget_profile=dict(max_seconds=60)
            )
            work = asyncio.create_task(asyncio.to_thread(quote_collector, client, report))
            try:
                quoted = await asyncio.shield(work)
            except asyncio.CancelledError:
                stop.set()
                await work
                raise
            except (ValueError, OSError):
                if stop.is_set():
                    raise
                quoted = advisor_quotes.fallback(report)
            state["quote_ref"] = repo.blob(quoted)
            save()
        state["issued_at"] = datetime.now(UTC).isoformat()
        advisor_quotes.verify(repo, quoted, report, state["issued_at"])
        trading = sell_rules(report, repo.raw_bytes, report["candidates"][0]["security_id"])
        answer = build_answer(
            request, report, quoted, trading, pinned_route=state.get("pinned_route")
        )
        answer.update(research_run_id=research["run_id"], research_sha256=research["result_sha256"])
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
        state.update(status="INTERRUPTED", error="服务停止，本轮研究和持仓输入已保留。")
        save()
        raise
    except Exception as error:
        state.update(
            status="INTERRUPTED",
            error=str(error)[:240]
            if isinstance(error, ValueError)
            else "本轮资料读取未完成，输入已保留，可继续核查。",
        )
        save()
    return repo.get(run_id)
