"""Fixed investment research and model checkpoints on the shared archive."""

from ..contracts import digest
from . import research_agents as agents
from .planning_store import PlanningStore
from .research_context import analysis_basis, analysis_plan, direction_check_context
from .research_context import analyst_context as expected_analysis_context
from .research_context import review_context as expected_review_context
from .research_contracts import InvestmentRequest

BUDGET = dict(max_model_calls=24, max_tokens=60000, max_seconds=900)


class InvestmentStore(PlanningStore):
    prefix = "RUN-IR-"
    request_type = InvestmentRequest
    schema_version = "investment-research-v1"
    scenario = "investment_research"

    def register_in_session(self, session, state):
        if state["request"]["schema_version"] == "investment-research-v4":
            from .evaluation import protocol
            from .models import InvestmentStudyRow

            session.add(
                InvestmentStudyRow(
                    run_id=state["run_id"],
                    created_at=state["created_at"],
                    protocol_ref=self.blob(protocol(state)),
                )
            )

    def create(self, request):
        state, fresh = super().create(request)
        if fresh:
            state.update(
                config=dict(
                    prompt_version=agents.prompt_version(request["schema_version"]),
                    budget=BUDGET,
                ),
                model_calls=0,
                charged_tokens=0,
                tokens=0,
                model_steps={},
            )
            self.save(state)
        if request["schema_version"] == "investment-research-v4":
            from .observations import ObservationStore

            ObservationStore(self).register(state)
        return state, fresh

    def get(self, run_id, *, with_data=True):
        state = super().get(run_id, with_data=with_data)
        if with_data and state.get("result_ref"):
            self.verify(state)
        return state

    def verify(self, state):
        data = state["data"]
        if state["request"]["schema_version"] in {
            "investment-research-v2",
            "investment-research-v3",
            "investment-research-v4",
        }:
            from .context import verify

            verify(self, state)
        if state["request"]["schema_version"] == "investment-research-v4":
            from .observations import verify_protocol

            verify_protocol(self, state)
        context = self.value(state["public_context_ref"])
        plan = self.output(state, "Planner", agents.Plan)
        agents.guard_plan(plan, context)
        planner_context = self.value(state["model_steps"]["Planner"]["context_ref"])
        if planner_context != {
            k: context[k]
            for k in ("horizon", "horizon_sessions", "stocks", "methods", "limitations")
        }:
            raise ValueError("规划上下文与封存输入不一致")
        analysis = self.output(state, state["approved_analysis_step"], agents.Analysis)
        agents.guard_analysis(
            analysis,
            analysis_basis(
                context,
                plan.model_dump(mode="json"),
                scoped=state["config"].get("scoped_analysis", False),
            ),
        )
        review = self.output(state, state["approved_review_step"], agents.Review)
        agents.guard_review(review, analysis)
        analysis_context = self.value(
            state["model_steps"][state["approved_analysis_step"]]["context_ref"]
        )
        review_context = self.value(
            state["model_steps"][state["approved_review_step"]]["context_ref"]
        )
        if any(
            analysis_context.get(k) != v
            for k, v in expected_analysis_context(
                context,
                state["config"]["prompt_version"],
                plan.model_dump(mode="json"),
                scoped=state["config"].get("scoped_analysis", False),
                compact=state["config"].get("portfolio_compact_v1", False),
            ).items()
        ):
            raise ValueError("分析与复核未使用固定公开上下文")
        review_checkpoint = state["model_steps"][state["approved_review_step"]]
        expected_review = expected_review_context(
            context,
            plan.model_dump(mode="json"),
            analysis.model_dump(mode="json"),
            review_context["review_round"],
            compact=agents.compact_review(
                state["model_steps"][state["approved_review_step"]].get(
                    "prompt_version", state["config"]["prompt_version"]
                )
            ),
            portfolio_compact=state["config"].get("portfolio_compact_v1", False),
        )
        if review_checkpoint.get("output_kind") == "direction_check":
            previous_key = review_checkpoint["previous_review_step"]
            expected_review = direction_check_context(
                self.value(state["model_steps"][previous_key]["context_ref"]),
                self.output(state, previous_key, agents.Review),
            )
        if review_context != expected_review:
            raise ValueError("复核的引用与批准内容不一致")
        if (
            state["config"]["prompt_version"]
            in {agents.DIRECTION_PROMPT_VERSION, agents.CHECKS_PROMPT_VERSION}
            and analysis_context["review_round"]
        ):
            previous = self.output(
                state, "Analyst-" + str(analysis_context["review_round"] - 1), agents.Analysis
            ).model_dump(mode="json")
            if analysis_context.get("previous_analysis") != previous:
                raise ValueError("修正分析未绑定被复核的上一版判断")
            feedback = self.output(
                state, "Verifier-" + str(analysis_context["review_round"] - 1), agents.Review
            ).model_dump(mode="json")["reviews"]
            if analysis_context.get("review_feedback") != feedback:
                raise ValueError("修正分析未使用对应的固定复核反馈")
        if (
            state.get("direction_review_recovery")
            and state["approved_review_step"] == state["direction_review_recovery"]["review_step"]
        ):
            recovery = state["direction_review_recovery"]
            original = self.output(state, recovery["previous_review_step"], agents.Review)
            if recovery["analysis_step"] != state[
                "approved_analysis_step"
            ] or not agents.direction_only_rejection(original):
                raise ValueError("方向复核恢复不能替代未获准事实或交易条件")
            if (
                recovery["prompt_version"] != agents.DIRECTION_CHECK_PROMPT_VERSION
                or state["charged_tokens"] < recovery["previous_charged_tokens"]
                or state["model_calls"] < recovery["previous_model_calls"]
            ):
                raise ValueError("方向复核恢复的版本或累计预算不一致")
        if review_checkpoint.get("output_kind") != "direction_check" and review_context.get(
            "proposal"
        ) != analysis.model_dump(mode="json"):
            raise ValueError("批准对象不是最终分析")
        if analysis_context.get("plan") != analysis_plan(
            plan.model_dump(mode="json"), state["config"].get("portfolio_compact_v1", False)
        ) or review_context.get("plan") != analysis_plan(
            plan.model_dump(mode="json"), state["config"].get("portfolio_compact_v1", False)
        ):
            raise ValueError("分析未遵守封存的路线规划")
        if any(r.verdict != "APPROVE" for r in review.reviews):
            raise ValueError("未通过的内容不能发布")
        approved = analysis.model_dump(mode="json")
        writing_context = self.value(state["model_steps"]["Writer"]["context_ref"])
        expected_writing = dict(
            approved_hash=digest(approved),
            items=[
                dict(security_id=o["security_id"], direction=o["direction"], summary=o["summary"])
                for o in approved["opinions"]
            ],
        )
        if writing_context != expected_writing:
            raise ValueError("发布输入不是最终批准内容")
        agents.guard_writing(self.output(state, "Writer", agents.Writing), approved)
        if data["research_opinions"] != approved["opinions"] or data["approved_hash"] != digest(
            approved
        ):
            raise ValueError("报告与批准内容不一致")
        if self.value(state["knowledge_ref"]) != data["knowledge_usage"]:
            raise ValueError("固定知识版本不一致")
        if len([c for c in state["calls"] if c.get("kind") == "model"]) != state["model_calls"]:
            raise ValueError("模型调用预算记录不一致")
        if (
            state["model_calls"] > 24
            or state["charged_tokens"] > 60000
            or state["elapsed_seconds"] > 900
        ):
            raise ValueError("研究超出预算")

    def output(self, state, step, schema):
        checkpoint = state["model_steps"][step]
        call = state["calls"][checkpoint["call_index"]]
        request = self.value(call["request_ref"])
        role = checkpoint["role"]
        version = checkpoint.get("prompt_version", state["config"]["prompt_version"])
        if version != state["config"]["prompt_version"] and (
            role != "Verifier"
            or version
            not in {agents.DIRECTION_PROMPT_VERSION, agents.DIRECTION_CHECK_PROMPT_VERSION}
        ):
            raise ValueError("仅允许对旧方向分歧使用固定新版本复核")
        output_kind = checkpoint.get("output_kind")
        recorded_schema = (
            agents.ReviewChecks
            if output_kind == "review_checks"
            else agents.DirectionCheck
            if output_kind == "direction_check"
            else schema
        )
        if output_kind and (
            role != "Verifier"
            or recorded_schema not in (schema, agents.ReviewChecks, agents.DirectionCheck)
        ):
            raise ValueError("复核输出类型不一致")
        if (
            call["status"] != "COMPLETED"
            or call["response_ref"] != checkpoint["response_ref"]
            or request["context"] != self.value(checkpoint["context_ref"])
            or request["instructions"]
            != agents.role_prompts(dict(state["config"], prompt_version=version))[role]
            or request["schema"] != recorded_schema.model_json_schema()
            or request["prompt_version"] != version
        ):
            raise ValueError("模型检查点与固定调用不一致")
        result = recorded_schema.model_validate(self.value(checkpoint["response_ref"]))
        if schema is agents.Review and output_kind == "review_checks":
            return agents.finalize_review(result)
        if schema is agents.Review and output_kind == "direction_check":
            previous = self.output(state, checkpoint["previous_review_step"], agents.Review)
            return agents.combine_direction_check(result, previous)
        return result
