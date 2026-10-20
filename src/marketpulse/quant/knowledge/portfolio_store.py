"""Immutable account turns on the existing SQLite/CAS, with verified public research links."""

import copy
import re
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError

from ..contracts import digest
from ..decision.store import DecisionStore
from ..live.store import ResearchRunRow
from .advisor_engine import sell_rules
from .advisor_quotes import verify as verify_quote
from .portfolio_contracts import PortfolioRequest
from .portfolio_engine import build, single_report
from .research_store import InvestmentStore


def groups(request):
    result = []
    for horizon in ("SWING", "MEDIUM"):
        entities = sorted(a.entity for a in request.assets if a.horizon == horizon)
        result.extend(
            dict(horizon=horizon, entities=entities[i : i + 4]) for i in range(0, len(entities), 4)
        )
    return result


def account_budget(request):
    count = len(groups(request))
    return dict(max_model_calls=24 * count, max_tokens=60000 * count, max_seconds=900)


def materials(repo, state, cache=None):
    cache = {} if cache is None else cache
    request = PortfolioRequest.model_validate(state["request"])
    definitions = groups(request)
    bindings = state["research_groups"]
    if len(bindings) != len(definitions):
        raise ValueError("账户研究分组数量不一致。")
    studies = []
    calendars = []
    for binding, definition in zip(bindings, definitions, strict=True):
        rid = binding["run_id"]
        if rid not in cache:
            cache[rid] = InvestmentStore(repo.base).get(rid)
        study = cache[rid]
        public = study["request"]
        if (
            study["status"] != "COMPLETED"
            or public["profile"] is not None
            or public["entities"] != definition["entities"]
            or public["horizon"] != definition["horizon"]
            or public["horizon_sessions"] != (20 if definition["horizon"] == "SWING" else 60)
            or bool(public.get("enhanced_research")) != request.enhanced_research
        ):
            raise ValueError("账户研究主体、周期或公开载荷边界不一致。")
        studies.append(study)
        packet = repo.value(study["inputs_ref"])
        for table in packet["calendars"].values():
            calendars.append(
                sorted(
                    r["cal_date"]
                    for r in table
                    if str(r["is_open"]) == "1" and r["cal_date"] <= packet["market_date"]
                )[-61:]
            )
    if any(c != calendars[0] for c in calendars):
        raise ValueError("不同交易所或分组的六十日历窗口不一致，不能缩短为交集。")
    budget = account_budget(request)
    if (
        state.get("budget") != budget
        or state.get("budget_policy_version") != "bounded-public-groups-v1"
    ):
        raise ValueError("账户预算未绑定固定的分组限额。")
    if (
        sum(s.get("model_calls", 0) for s in studies) > budget["max_model_calls"]
        or sum(s.get("charged_tokens", 0) for s in studies) > budget["max_tokens"]
    ):
        raise ValueError("账户公开研究累计超出共享预算。")
    return studies, calendars[0]


class PortfolioStore(DecisionStore):
    prefix = "RUN-PA-"

    def create(self, definition):
        request = PortfolioRequest.model_validate(definition)
        rid = self.prefix + request.request_key.hex
        if self.exists(rid):
            old = self.get(rid)
            if PortfolioRequest.model_validate(old["request"]) != request:
                raise ValueError("请求标识已绑定其他账户输入。")
            return old, False
        parent = None
        if request.previous_run_id:
            parent = self.get(request.previous_run_id)
            if parent["status"] != "COMPLETED" or parent["turn_count"] >= 100:
                raise ValueError("上一轮完成后才能追问；一百轮后请另开咨询，原记录保留。")
            before = PortfolioRequest.model_validate(parent["request"])
            if (
                not request.refresh_research
                and request.enhanced_research != before.enhanced_research
            ):
                raise ValueError("更换研究流程时请勾选更新公开资料，原研究会保留。")
            old_assets = {a.entity: a for a in before.assets}
            for a in request.assets:
                if a.entity in old_assets:
                    if a.horizon != old_assets[a.entity].horizon or a.route_id not in (
                        "AUTO",
                        parent["data"]["pinned_routes"][a.entity],
                    ):
                        raise ValueError("已有股票的原周期和路线不能在账户追问中切换。")
            if groups(before) != groups(request) and not request.refresh_research:
                raise ValueError("持仓股票或候选改变时，请勾选更新公开资料。")
        state = dict(
            schema_version="portfolio-advisor-v1",
            run_id=rid,
            request=request.model_dump(mode="json"),
            status="RUNNING",
            scenario="portfolio_advisor",
            page="portfolio-advisor",
            title="、".join(a.entity for a in request.assets if a.shares) + " · 账户调仓",
            created_at=datetime.now(UTC).isoformat(),
            calls=[],
            process=[],
            tool_results={},
            turn_count=parent["turn_count"] + 1 if parent else 1,
            conversation_id=parent["conversation_id"] if parent else rid,
            pinned_routes={
                a.entity: parent["data"]["pinned_routes"][a.entity]
                for a in request.assets
                if a.entity in parent["data"]["pinned_routes"]
            }
            if parent
            else {},
            elapsed_seconds=0,
            automatic_continuations={},
        )
        if parent and not request.refresh_research:
            state["research_groups"] = copy.deepcopy(parent["research_groups"])
        if request.semantic_understanding:
            state["dialogue_version"] = "question-dialogue-v2"
        try:
            with self.base.sessions.begin() as session:
                session.add(
                    ResearchRunRow(
                        run_id=rid, status="RUNNING", created_at=state["created_at"], payload=state
                    )
                )
            return state, True
        except IntegrityError:
            old = self.get(rid)
            if PortfolioRequest.model_validate(old["request"]) != request:
                raise ValueError("请求标识已绑定其他账户输入。") from None
            return old, False

    def get(self, run_id, *, with_data=True, cache=None):
        if not re.fullmatch(r"RUN-PA-[0-9a-f]{32}", run_id):
            raise KeyError(run_id)
        state = self.base.get(run_id, with_data=with_data)
        if state["schema_version"] != "portfolio-advisor-v1":
            raise ValueError("账户咨询记录类型不一致。")
        if with_data and state.get("result_ref"):
            if state["result_ref"] != "blob://sha256/" + state["result_sha256"]:
                raise ValueError("账户结果哈希不一致。")
            studies, sessions = materials(self, state, cache)
            data = state["data"]
            trading = {}
            for study in studies:
                for c in study["data"]["candidates"]:
                    sid = c["security_id"]
                    verify_quote(
                        self,
                        data["quotes"][sid],
                        single_report(study["data"], sid),
                        state["issued_at"],
                    )
                    trading[sid] = sell_rules(study["data"], self.raw_bytes, sid)
            expected = build(
                state["request"], studies, data["quotes"], trading, sessions, state["pinned_routes"]
            )
            expected["quotes"] = data["quotes"]
            if PortfolioRequest.model_validate(state["request"]).semantic_understanding:
                from .question_understanding import bound_understanding, dialogue

                understanding = bound_understanding(self, state, studies)
                expected["dialogue"] = dialogue(
                    expected,
                    understanding,
                    portfolio=True,
                    version=state.get("dialogue_version", "question-dialogue-v1"),
                )
                expected["privacy"]["anonymous_semantics_sent_to_model"] = (
                    state["understanding"]["model_calls"] > 0
                )
            if (
                expected != data
                or digest(data) != state["result_sha256"]
                or state["elapsed_seconds"] > 900
            ):
                raise ValueError("账户结果与封存研究、知识或本地计算不一致。")
            if state["request"]["previous_run_id"]:
                old = self.base.get(state["request"]["previous_run_id"], with_data=False)
                if (
                    old["status"] != "COMPLETED"
                    or old["conversation_id"] != state["conversation_id"]
                ):
                    raise ValueError("账户对话前序绑定不一致。")
                old_data = self.value(old["result_ref"])
                expected_pins = {
                    a.entity: old_data["pinned_routes"][a.entity]
                    for a in PortfolioRequest.model_validate(state["request"]).assets
                    if a.entity in old_data["pinned_routes"]
                }
                if (
                    state["pinned_routes"] != expected_pins
                    or state["turn_count"] != old["turn_count"] + 1
                ):
                    raise ValueError("账户原路线或前序轮次绑定不一致。")
                if (
                    not state["request"]["refresh_research"]
                    and old["research_groups"] != state["research_groups"]
                ):
                    raise ValueError("追问的固定公开研究不一致。")
        return copy.deepcopy(state)

    def conversation(self, run_id):
        turns = []
        seen = set()
        cache = {}
        while run_id:
            if run_id in seen or len(seen) >= 100:
                raise ValueError("账户对话引用重复或超出上限。")
            seen.add(run_id)
            state = self.get(run_id, cache=cache)
            turns.append(state)
            run_id = state["request"].get("previous_run_id")
        return list(reversed(turns))
