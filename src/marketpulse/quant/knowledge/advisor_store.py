"""Each holding dialogue turn is immutable, with explicit parent and research links."""

import copy
import re
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError

from ..contracts import digest
from ..decision.store import DecisionStore
from ..live.store import ResearchRunRow
from .advisor_contracts import AdvisorRequest
from .advisor_engine import build_answer, sell_rules
from .advisor_quotes import verify as verify_quote
from .research_store import InvestmentStore


class AdvisorStore(DecisionStore):
    prefix = "RUN-HA-"

    def __init__(self, research_store):
        super().__init__(research_store)
        self._research = {}
        self._trading = {}

    def parent(self, request):
        if not request.previous_run_id:
            return None
        old = self.get(request.previous_run_id)
        if old["status"] != "COMPLETED":
            raise ValueError("上一轮完成后才能继续追问。")
        before = AdvisorRequest.model_validate(old["request"])
        if not request.refresh_research and request.enhanced_research != before.enhanced_research:
            raise ValueError("更换研究流程时请勾选更新公开资料，原研究会保留。")
        if (
            request.position.entity != before.position.entity
            or request.horizon != before.horizon
            or request.horizon_sessions != before.horizon_sessions
        ):
            raise ValueError("更换股票或持有周期请开始新咨询，原对话会保留。")
        if request.route_id not in ("AUTO", old["data"]["route_id"]):
            raise ValueError("原持仓路线不能在追问中切换；请单独研究新路线。")
        if old.get("turn_count", 1) >= 100:
            raise ValueError("本对话已保存一百轮，请开始新咨询，原记录仍保留。")
        return old

    def create(self, definition):
        request = AdvisorRequest.model_validate(definition)
        run_id = self.prefix + request.request_key.hex
        if self.exists(run_id):
            old = self.get(run_id)
            if AdvisorRequest.model_validate(old["request"]) != request:
                raise ValueError("请求标识已绑定其他输入。")
            return old, False
        parent = self.parent(request)
        state = dict(
            schema_version="holding-advisor-v1",
            run_id=run_id,
            request=request.model_dump(mode="json"),
            status="RUNNING",
            scenario="holding_advisor",
            page="holding-advisor",
            title=request.position.entity + " · 持仓顾问",
            created_at=datetime.now(UTC).isoformat(),
            calls=[],
            process=[],
            tool_results={},
            turn_count=parent.get("turn_count", 1) + 1 if parent else 1,
            conversation_id=parent["conversation_id"] if parent else run_id,
            pinned_route=parent["data"]["route_id"] if parent else None,
        )
        if parent and not request.refresh_research:
            state["research_run_id"] = parent["research_run_id"]
        if request.semantic_understanding:
            state["dialogue_version"] = "question-dialogue-v2"
        try:
            with self.base.sessions.begin() as session:
                session.add(
                    ResearchRunRow(
                        run_id=run_id,
                        status=state["status"],
                        created_at=state["created_at"],
                        payload=state,
                    )
                )
            return state, True
        except IntegrityError:
            old = self.get(run_id)
            if AdvisorRequest.model_validate(old["request"]) != request:
                raise ValueError("请求标识已绑定其他输入。") from None
            return old, False

    def get(self, run_id, *, with_data=True):
        if not re.fullmatch(r"RUN-HA-[0-9a-f]{32}", run_id):
            raise KeyError(run_id)
        state = self.base.get(run_id, with_data=with_data)
        if state["schema_version"] != "holding-advisor-v1":
            raise ValueError("持仓咨询记录类型不一致。")
        if with_data and state.get("result_ref"):
            if state["result_ref"] != "blob://sha256/" + state["result_sha256"]:
                raise ValueError("持仓回答哈希不一致。")
            research_id = state["research_run_id"]
            if research_id not in self._research:
                self._research[research_id] = InvestmentStore(self.base).get(research_id)
            research = self._research[research_id]
            if research["status"] != "COMPLETED" or research["request"]["profile"] is not None:
                raise ValueError("持仓回答必须绑定已复核的公开研究。")
            definition = AdvisorRequest.model_validate(state["request"])
            if (
                research["request"]["entities"] != [definition.position.entity]
                or research["request"]["horizon"] != definition.horizon
                or research["request"]["horizon_sessions"] != definition.horizon_sessions
                or bool(research["request"].get("enhanced_research"))
                != definition.enhanced_research
            ):
                raise ValueError("公开研究与持仓股票或周期不一致。")
            previous = definition.previous_run_id
            if previous:
                parent = self.base.get(previous, with_data=False)
                prior = AdvisorRequest.model_validate(parent["request"])
                if (
                    parent["status"] != "COMPLETED"
                    or state["conversation_id"] != parent["conversation_id"]
                    or state["turn_count"] != parent["turn_count"] + 1
                    or definition.position.entity != prior.position.entity
                    or state["pinned_route"] != self.value(parent["result_ref"])["route_id"]
                ):
                    raise ValueError("持仓对话的原路线或前序版本绑定不一致。")
                if (
                    not definition.refresh_research
                    and state["research_run_id"] != parent["research_run_id"]
                ):
                    raise ValueError("追问未复用已封存研究。")
            data = state["data"]
            verify_quote(self, data["quote"], research["data"], state["issued_at"])
            if research_id not in self._trading:
                self._trading[research_id] = sell_rules(
                    research["data"], self.raw_bytes, data["security_id"]
                )
            trading = self._trading[research_id]
            understanding = None
            if definition.semantic_understanding:
                from .question_understanding import bound_understanding

                understanding = bound_understanding(self, state, [research])
            expected = build_answer(
                state["request"],
                research["data"],
                data["quote"],
                trading,
                pinned_route=state.get("pinned_route"),
                understanding=understanding,
            )
            expected.update(
                research_run_id=research["run_id"], research_sha256=research["result_sha256"]
            )
            if definition.semantic_understanding:
                from .question_understanding import dialogue

                expected["dialogue"] = dialogue(
                    expected,
                    understanding,
                    version=state.get("dialogue_version", "question-dialogue-v1"),
                )
                expected["privacy"]["anonymous_semantics_sent_to_model"] = (
                    state["understanding"]["model_calls"] > 0
                )
            if expected != data or digest(data) != state["result_sha256"]:
                raise ValueError("持仓回答与固定输入、知识或本地计算不一致。")
        return copy.deepcopy(state)

    def conversation(self, run_id):
        # Request-scoped reuse avoids reparsing the same rules for every turn.
        # The next HTTP request starts with an empty cache and verifies originals again.
        self._research.clear()
        self._trading.clear()
        turns, seen = [], set()
        while run_id:
            if run_id in seen or len(seen) >= 100:
                raise ValueError("对话引用重复或超出上限。")
            seen.add(run_id)
            state = self.get(run_id)
            turns.append(state)
            run_id = state["request"].get("previous_run_id")
        return list(reversed(turns))
