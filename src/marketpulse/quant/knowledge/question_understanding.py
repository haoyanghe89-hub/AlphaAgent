"""Bounded semantic routing over an allowlisted projection, never private prose."""

import asyncio
import copy
import json
import re
import time
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid5

from pydantic import Field
from sqlalchemy.exc import IntegrityError

from ..contracts import canonical, digest
from ..decision.next_session.agents import DailyRoles
from ..decision.store import DecisionStore
from ..decision.tools import SourceClient
from ..decision.tushare import fetch, records
from ..domain import FrozenModel
from ..live.store import ResearchRunRow

VERSION = "question-understanding-v1"
BUDGET = dict(max_model_calls=3, max_tokens=24000, max_seconds=120)
Mode = Literal[
    "next",
    "investment",
    "medium",
    "market",
    "candidates",
    "holding",
    "portfolio",
    "compare",
    "strategy",
    "clarify",
]
Focus = Literal[
    "DIRECTION",
    "BASIS",
    "INVALIDATION",
    "CASHFLOW",
    "VALUATION",
    "TECHNICAL",
    "POLICY",
    "FLOWS",
    "ENTRY",
    "SELL",
    "ADD",
    "DEFENSE",
    "HOLD",
    "CONCENTRATION",
    "CORRELATION",
    "GENERAL",
]
FOCUS_LABELS = dict(
    DIRECTION="方向判断",
    BASIS="判断依据",
    INVALIDATION="推翻判断的条件",
    CASHFLOW="现金回收",
    VALUATION="估值",
    TECHNICAL="技术位置",
    POLICY="政策",
    FLOWS="资金流向",
    ENTRY="入场条件",
    SELL="减仓与卖出",
    ADD="加仓条件",
    DEFENSE="下行防守",
    HOLD="持有条件",
    CONCENTRATION="行业集中风险",
    CORRELATION="持仓相关性",
    GENERAL="整体建议",
)

# Unknown text, identifiers and every amount stay local. Preserve compositional
# language (negation, conditionals, order, references), rather than keyword tags.
WORDS = """
下一交易日 明日 明天 明早 未来三个月 三个月 中期 波段 短期 长期 今天 现在 当前 后续
投资计划 投资研究 研究 预测 方向 看多 看空 看好 不看好 涨跌 上涨 下跌 回撤 跌破 突破
账户调仓 组合持仓 多只持仓 持仓顾问 持仓咨询 持仓 仓位 账户 组合 行业集中度 集中度
集中风险 行业集中 行业 相关性 相关 同一行业 同时 一起 总仓位 总风险 风险预算 风险
市场观察 市场 大盘 板块 行业候选 候选 龙头 制造业 制造 半导体设备 设备 推荐 筛选
成本价 成本 股数 股 资金 现金 价格 点位 盈亏 浮亏 亏损 收益 入场 买入 加仓 补仓
摊薄 减仓 降仓 降风险 调仓 卖出 卖 止盈 止损 防守 持有 等待 持币 观望 退出 落袋
现金回收 现金流 经营现金流 财报 基本面 营收 收入 利润 增长 质量 估值 政策 主力资金
资金流向 量能 成交量 趋势 技术 指标 MACD RSI KDJ 均线 财务 盈利能力 反证 失效条件
失效 推翻 判断 结论 依据 理由 方法 逻辑 知识体系 知识库 规则 条件 触发 信号 变化
第一只 第二只 第三只 第四只 第五只 第六只 第七只 第八只 前一只 后一只 另一只 两只
前者 后者 这只 那只 它 它们 这个 那个 该股 这家 上一轮 上一个 上次 刚才 之前 前面
先别 不要 不是 不再 不能 不用 并非 不 因为 所以 而是 但是 但 而且 同时 并且 以及
如果 假如 假设 要是 那如果 即使 就算 只是 仍然 还是 那么 然后 最后 首先 优先 先 再
只要 一旦 除非 否则 才 就 会 是否 什么 怎么 怎样 为什么 哪些 哪个 多少 什么时候
没变 不变 未变 没有 满足 不满足 发生 出现 保持 有效 无效 改变 继续 沿用 解释 说明
分析 比较 对比 核对 判断 关注 重点 看 看看 只看 不看 单独 分别 逐股 泛讲 整体 综合
我 我们 你 用户 帮我 请 能 能否 可以 需要 希望 想 要 了 的 地 得 吗 呢 吧 在 对 与 和
为 根据 按 按照 到 到达 跌到 涨到 元 块 万 亿 成 ％ % 超过 低于 高于 上限 下限
全市场选股 年度调仓 回测 路线 策略 趋势波段 质量成长
""".split()
TOKEN = re.compile(
    "|".join(re.escape(w) for w in sorted(set(WORDS), key=lambda w: (-len(w), w))), re.I
)


class QuestionRequest(FrozenModel):
    request_key: UUID
    question: str = Field(min_length=1, max_length=2000)
    channel: Literal["workbench", "holding", "portfolio"] = "workbench"
    selected_mode: str | None = Field(default=None, max_length=30)
    previous_run_id: str | None = Field(default=None, pattern=r"^RUN-QU-[0-9a-f]{32}$")


class Task(FrozenModel):
    mode: Mode
    targets: tuple[str, ...] = Field(max_length=8)
    horizon: Literal["SWING", "MEDIUM"]
    focuses: tuple[Focus, ...] = Field(min_length=1, max_length=5)
    industry: Literal["manufacturing", "equipment"] | None = None


class Understanding(FrozenModel):
    tasks: tuple[Task, ...] = Field(min_length=1, max_length=4)
    inherited: bool


PROMPT = (
    "你负责金融问题的语义理解，不提供投资事实或交易指令。输入是本地白名单投影："
    "[私密]是未发送片段，[数值]是未发送数字；S标记仅代表匿名股票，编号按本地列表顺序。"
    "不得猜测被隐藏的内容。结合previous理解否定、先后、多任务、条件与前文指代。"
    "私人咨询第一只/第二只按slots顺序；工作台优先按上轮tasks讨论的targets顺序。"
    "前者/后者及它引用上一轮targets。未点名的账户问题可选全部。"
    "否定的任务或关注点不要选，例如先别说补仓应优先解释实际追问而非ADD。"
    "什么变化会推翻判断选INVALIDATION，现金回收不变而回撤应同时考虑INVALIDATION与DEFENSE。"
    "只用已有slots，不捏造证券；需要股票却没有任何slots时mode=clarify。"
    "channel=holding或portfolio时mode必须同channel，即使提及未来或比较也不能脱离已核对持仓。"
    "工作台显式语义优先于selected_mode；多项独立任务按用户顺序完整保留最多四项。"
    "三个月/中期用MEDIUM，波段用SWING，省略则继承上轮对应任务期限。"
    "个股未来三个月方向用medium；多股比较/投资计划/方法研究用investment；"
    "明日方向用next，市场大盘用market，板块候选用candidates，私人仓位卖点用holding，"
    "账户组合风险用portfolio，单纯公开比较可用compare。对比包含中期研究可合并investment覆盖所有目标。"
    "有真实多步骤请求可保留多任务，不把解释反证当另一项独立任务。"
    "inherited表示是否使用previous解释省略或指代。只返回既定JSON枚举，不输出金额或私人信息。"
)


def projection(question, registry, *, policy="financial-projection-v2"):
    """Positive vocabulary projection; arbitrary private substrings never pass through."""
    if policy not in {"financial-projection-v1", "financial-projection-v2"}:
        raise ValueError("未知匿名语义投影协议")
    text = re.sub(r"(?:未来\s*)?[3三]\s*个月", "未来三个月", question)
    if policy == "financial-projection-v2":
        # A credential can itself be a financial word. Hide the whole named field,
        # and never preserve an indicator embedded inside an arbitrary identifier.
        text = re.sub(
            r"(?:密码|口令|密钥|私钥|验证码|TOKEN|API[\s_-]*KEY|账号|账户号|银行卡|"
            r"身份证|手机号|手机号码|姓名|住址|邮箱)[^，,。；;\n？?!！]*",
            "[私密]",
            text,
            flags=re.I,
        )
    names = []
    for i, stock in enumerate(registry, 1):
        code = stock.get("code", "")
        for name in (stock["entity"], stock.get("name"), code, code[:6]):
            if name:
                names.append((name, f"§{i}§"))
    for name, slot in sorted(names, key=lambda p: -len(p[0])):
        pattern = re.escape(name)
        if re.fullmatch(r"\d{6}(?:\.(?:SH|SZ))?", name, re.I):
            pattern = r"(?<!\d)" + pattern + r"(?!\d)"
        text = re.sub(pattern, lambda _, value=slot: value, text, flags=re.I)
    if policy == "financial-projection-v2":
        text = re.sub(
            r"[A-Za-z_][A-Za-z0-9_.:@/\-]*",
            lambda m: m[0].upper() if m[0].upper() in {"MACD", "RSI", "KDJ"} else "[私密]",
            text,
        )
    result, private = [], False
    index = 0
    while index < len(text):
        slot = re.match(r"§(\d)§", text[index:])
        if slot:
            result.append("S" + slot[1])
            index += len(slot[0])
            private = False
            continue
        token = TOKEN.match(text, index)
        if token:
            result.append(token[0].upper() if token[0].isascii() else token[0])
            index = token.end()
            private = False
            continue
        if text[index] in "，,。；;？?：:！!（）()、\n ":
            result.append("，" if text[index] in ",\n" else text[index])
            private = False
        elif text[index].isdigit() or text[index] in "一二三四五六七八九十百千万零点两":
            if not private:
                result.append("[数值]")
            private = True
        elif not private:
            result.append("[私密]")
            private = True
        index += 1
    return "".join(result).strip()[:1800]


def guard(answer, context):
    allowed = set(context["slots"])
    for task in answer.tasks:
        if len(set(task.targets)) != len(task.targets) or not set(task.targets) <= allowed:
            raise ValueError("目标必须使用本地提供的匿名股票标记")
        if context["channel"] != "workbench" and task.mode != context["channel"]:
            raise ValueError("私人咨询不能变更任务或账户设置")
        if task.mode in {"next", "medium", "investment", "compare"} and not task.targets:
            raise ValueError("未明确股票时应请求澄清")
        if task.mode in {"next", "medium", "holding"} and len(task.targets) > 1:
            raise ValueError("单股任务需逐项列出，不能丢弃目标")
        if task.mode in {"investment", "compare"} and len(task.targets) > 4:
            raise ValueError("单轮公开研究最多四只股票，请分批")


def resolved(answer, registry):
    tasks = []
    for task in answer.tasks:
        value = task.model_dump(mode="json")
        value["entities"] = [registry[int(s[1:]) - 1]["entity"] for s in task.targets]
        value["horizon_sessions"] = 60 if task.horizon == "MEDIUM" else 20
        tasks.append(value)
    return dict(
        tasks=tasks,
        inherited=answer.inherited,
        model_used=True,
        summary="；".join("、".join(FOCUS_LABELS[f] for f in t.focuses) for t in answer.tasks),
        sources=[],
    )


class QuestionStore(DecisionStore):
    prefix = "RUN-QU-"

    def create(self, definition, *, registry=None):
        request = QuestionRequest.model_validate(definition)
        rid = self.prefix + request.request_key.hex
        if self.exists(rid):
            old = self.get(rid)
            if old["request"] != request.model_dump(mode="json"):
                raise ValueError("问题标识已绑定其他输入")
            return old, False
        previous = self.get(request.previous_run_id) if request.previous_run_id else None
        if previous and previous["status"] != "COMPLETED":
            raise ValueError("上一轮问题理解完成后才能追问")
        if previous and previous["turn_count"] >= 100:
            raise ValueError("已保存一百轮，请开始新咨询")
        state = dict(
            schema_version=VERSION,
            run_id=rid,
            request=request.model_dump(mode="json"),
            status="RUNNING",
            created_at=datetime.now(UTC).isoformat(),
            scenario="question_understanding",
            title="问题理解",
            calls=[],
            process=[],
            tool_results={},
            config=dict(prompt_version=VERSION, budget=BUDGET),
            model_calls=0,
            tokens=0,
            charged_tokens=0,
            elapsed_seconds=0,
            turn_count=previous["turn_count"] + 1 if previous else 1,
            projection_policy="financial-projection-v2",
        )
        if registry is not None:
            state["registry_ref"] = self.blob(registry)
        try:
            with self.base.sessions.begin() as session:
                session.add(
                    ResearchRunRow(
                        run_id=rid, status="RUNNING", created_at=state["created_at"], payload=state
                    )
                )
        except IntegrityError:
            return self.create(definition, registry=registry)
        return state, True

    def context(self, state):
        request = QuestionRequest.model_validate(state["request"])
        registry = self.value(state["registry_ref"])
        policy = state.get("projection_policy", "financial-projection-v1")
        previous = []
        rid = request.previous_run_id
        for _ in range(3):
            if not rid:
                break
            parent = self.base.get(rid, with_data=False)
            old_registry = self.value(parent["registry_ref"])
            current_slots = {s["code"]: f"S{i}" for i, s in enumerate(registry, 1)}
            tasks = []
            for task in self.value(parent["answer_ref"])["tasks"]:
                targets = [old_registry[int(s[1:]) - 1]["code"] for s in task["targets"]]
                tasks.append(
                    dict(task, targets=[current_slots[s] for s in targets if s in current_slots])
                )
            # Re-project against the current slot ordering; old public identifiers
            # and all original prose remain in the local archive only.
            previous.append(
                dict(
                    question=projection(parent["request"]["question"], registry, policy=policy),
                    tasks=tasks,
                )
            )
            rid = parent["request"]["previous_run_id"]
        return dict(
            version=VERSION,
            channel=request.channel,
            selected_mode=request.selected_mode
            if request.selected_mode
            in {"next", "investment", "medium", "market", "candidates", "holding", "portfolio"}
            else None,
            slots=[f"S{i}" for i in range(1, len(registry) + 1)],
            question=projection(request.question, registry, policy=policy),
            previous=list(reversed(previous)),
        )

    def get(self, run_id, *, with_data=True):
        if not re.fullmatch(r"RUN-QU-[0-9a-f]{32}", run_id):
            raise KeyError(run_id)
        state = self.base.get(run_id, with_data=with_data)
        if not with_data:
            return state
        self.verify(state)
        rid = state["request"]["previous_run_id"]
        seen = {run_id}
        while rid:
            if rid in seen or len(seen) >= 100:
                raise ValueError("问题对话链重复或超出上限")
            seen.add(rid)
            parent = self.base.get(rid)
            self.verify(parent)
            if parent["status"] != "COMPLETED":
                raise ValueError("问题对话前序未完成")
            rid = parent["request"]["previous_run_id"]
        return copy.deepcopy(state)

    def verify(self, state):
        if state["schema_version"] != VERSION or state["config"] != dict(
            prompt_version=VERSION, budget=BUDGET
        ):
            raise ValueError("问题理解协议或预算不一致")
        if state.get("result_ref"):
            model_calls = [c for c in state["calls"] if c.get("kind") == "model"]
            if (
                len(model_calls) != state["model_calls"]
                or sum(c.get("charged_tokens", c["reserved_tokens"]) for c in model_calls)
                != state["charged_tokens"]
                or sum(
                    c.get("input_tokens", 0) + c.get("output_tokens", 0)
                    for c in model_calls
                    if "charged_tokens" in c
                )
                != state["tokens"]
                or not 0 <= state["elapsed_seconds"] <= BUDGET["max_seconds"]
            ):
                raise ValueError("问题理解累计用量或耗时与实际调用不一致")
            for item in model_calls:
                definition = self.value(item["request_ref"])
                if (
                    item["role"] != "Analyst"
                    or definition["role"] != "Analyst"
                    or item["model"] != definition["model"]
                    or definition["response_schema_version"] != VERSION
                    or any(
                        item.get(k) != v or definition.get(k) != v
                        for k, v in dict(
                            thinking_enabled=False,
                            reasoning_effort=None,
                            reasoning_tokens=0,
                            max_output_tokens=1000,
                        ).items()
                    )
                ):
                    raise ValueError("问题理解的固定模型身份或推理预算不一致")
            context = self.context(state)
            answer = Understanding.model_validate(self.value(state["answer_ref"]))
            guard(answer, context)
            call = state["calls"][state["answer_call_index"]]
            request = self.value(call["request_ref"])
            if (
                call["status"] != "COMPLETED"
                or self.value(call["response_ref"]) != answer.model_dump(mode="json")
                or request["context"] != context
                or request["instructions"] != PROMPT
                or request["schema"] != Understanding.model_json_schema()
                or request["messages"]
                != [
                    dict(role="system", content=PROMPT),
                    dict(role="user", content=canonical(context)),
                ]
                or request["prompt_version"] != VERSION
                or state["model_calls"] > BUDGET["max_model_calls"]
                or state["charged_tokens"] > BUDGET["max_tokens"]
                or state["result_ref"] != "blob://sha256/" + state["result_sha256"]
                or state["data"] != resolved(answer, self.value(state["registry_ref"]))
                or digest(state["data"]) != state["result_sha256"]
            ):
                raise ValueError("问题理解与封存语义、模型调用或预算不一致")
            if state.get("catalog_source"):
                catalog = {
                    r["ts_code"]: r["name"]
                    for r in records(
                        json.loads(self.raw_bytes(state["catalog_source"]["blob_ref"]))
                    )
                }
                if any(
                    catalog.get(s["code"]) != s["name"] or s["entity"] != s["name"]
                    for s in self.value(state["registry_ref"])
                ):
                    raise ValueError("股票标记与封存证券目录不一致")


def collect_registry(client, question, previous):
    source, rows = fetch(
        client, "question_catalog", "stock_basic", dict(exchange="", list_status="L")
    )
    matched = []
    for row in rows:
        code, name = row["ts_code"], row["name"]
        if not code.endswith((".SH", ".SZ")):
            continue
        code_match = re.search(
            r"(?<!\d)" + re.escape(code[:6]) + r"(?:\.(?:SH|SZ))?(?!\d)", question, re.I
        )
        locations = [question.find(name), code_match.start() if code_match else -1]
        positions = [p for p in locations if p >= 0]
        if positions:
            matched.append((min(positions), dict(entity=name, name=name, code=code)))
    registry = list(previous)
    for _, stock in sorted(matched, key=lambda p: p[0]):
        if stock["code"] not in {s["code"] for s in registry}:
            registry.append(stock)
    if len(registry) > 8:
        raise ValueError("一次最多理解八只股票，请分批输入")
    return registry, source


async def run(repo, run_id, stop, settings, *, port=None, catalog=collect_registry):
    state = repo.get(run_id, with_data=False)
    if state["status"] == "COMPLETED":
        return repo.get(run_id)
    started, elapsed = time.monotonic(), state["elapsed_seconds"]

    def save():
        state["elapsed_seconds"] = elapsed + time.monotonic() - started
        repo.save(state)

    def check():
        if stop.is_set() or elapsed + time.monotonic() - started >= BUDGET["max_seconds"]:
            raise ValueError("问题理解已停止或达到时间上限，原问题保留")

    try:
        state.update(status="RUNNING", error=None)
        if "registry_ref" not in state:
            previous_id = state["request"]["previous_run_id"]
            previous = repo.value(repo.get(previous_id)["registry_ref"]) if previous_id else []
            client = SourceClient(repo, state, save, stop, max_requests=4, budget_profile=BUDGET)
            registry, source = await asyncio.to_thread(
                catalog, client, state["request"]["question"], previous
            )
            state.update(registry_ref=repo.blob(registry), catalog_source=source)
            save()
        context = repo.context(state)
        state["public_projection"] = context
        save()
        async with DailyRoles(
            repo,
            state,
            settings,
            save,
            check,
            port=port,
            prompts={"Analyst": PROMPT},
            schema_version=VERSION,
            output_tokens=1000,
            reserve_limit=24000,
        ) as roles:
            answer, call = await roles.call(
                "Analyst", Understanding, context, lambda p: guard(p, context)
            )
        data = resolved(answer, repo.value(state["registry_ref"]))
        state.update(
            status="COMPLETED",
            answer_ref=call["response_ref"],
            answer_call_index=state["calls"].index(call),
            result_ref=repo.blob(data),
            result_sha256=digest(data),
        )
        save()
    except asyncio.CancelledError:
        state.update(status="INTERRUPTED", error="服务停止，问题和已用预算保留")
        save()
        raise
    except Exception as error:
        state.update(
            status="INTERRUPTED",
            error=str(error)[:180]
            if isinstance(error, ValueError)
            else "问题理解暂不可用，可使用原有入口",
        )
        save()
    return repo.get(run_id)


async def understand_advisor(repo, state, studies, stop, settings, *, port=None):
    """Only published stock identities enter the local registry; no account values."""
    request = state["request"]
    if not request.get("semantic_understanding"):
        return None
    registry = advisor_registry(request, studies)
    parent = (
        repo.base.get(request["previous_run_id"], with_data=False)
        if request.get("previous_run_id")
        else None
    )
    previous = previous_understanding(repo, parent)
    key = uuid5(UUID(request["request_key"]), "semantic-understanding-v1")
    semantic = QuestionStore(repo.base)
    understanding, _ = semantic.create(
        dict(
            request_key=str(key),
            question=request["question"],
            channel="portfolio" if "assets" in request else "holding",
            previous_run_id=previous,
        ),
        registry=registry,
    )
    understanding = await run(semantic, understanding["run_id"], stop, settings, port=port)
    state["understanding"] = {
        k: understanding[k]
        for k in ("run_id", "status", "model_calls", "tokens", "result_ref", "public_projection")
        if k in understanding
    }
    if understanding["status"] != "COMPLETED":
        state["understanding"]["error"] = understanding.get("error")
    repo.save(state)
    return understanding.get("data")


def advisor_registry(request, studies):
    registry = []
    entities = (
        [a["entity"] for a in request["assets"]]
        if "assets" in request
        else [request["position"]["entity"]]
    )
    candidates = [c for s in studies for c in s["data"]["candidates"]]
    for entity in entities:
        candidate = next(
            c for c in candidates if entity in (c["name"], c["security_id"], c["security_id"][:6])
        )
        registry.append(dict(entity=entity, name=candidate["name"], code=candidate["security_id"]))
    return registry


def previous_understanding(repo, parent):
    """A failed optional understanding must never poison subsequent consultations."""
    for _ in range(100):
        if not parent:
            return None
        rid = parent.get("understanding", {}).get("run_id")
        if rid and QuestionStore(repo.base).get(rid)["status"] == "COMPLETED":
            return rid
        previous = parent["request"].get("previous_run_id")
        parent = repo.base.get(previous, with_data=False) if previous else None
    raise ValueError("咨询对话超过上限")


def bound_understanding(repo, state, studies):
    if not state["request"].get("semantic_understanding"):
        return None
    recorded = state.get("understanding")
    if not recorded:
        raise ValueError("缺少本轮问题理解记录")
    semantic = QuestionStore(repo.base).get(recorded["run_id"])
    key = uuid5(UUID(state["request"]["request_key"]), "semantic-understanding-v1")
    if (
        semantic["run_id"] != QuestionStore.prefix + key.hex
        or semantic["request"]["question"] != state["request"]["question"]
    ):
        raise ValueError("问题理解未绑定当前咨询")
    channel = "portfolio" if "assets" in state["request"] else "holding"
    parent = (
        repo.base.get(state["request"]["previous_run_id"], with_data=False)
        if state["request"].get("previous_run_id")
        else None
    )
    previous = previous_understanding(repo, parent)
    expected = {
        k: semantic[k]
        for k in ("run_id", "status", "model_calls", "tokens", "result_ref", "public_projection")
        if k in semantic
    }
    if semantic["status"] != "COMPLETED":
        expected["error"] = semantic.get("error")
    if (
        recorded != expected
        or semantic["request"]["channel"] != channel
        or semantic["request"]["previous_run_id"] != previous
    ):
        raise ValueError("问题理解的前序或模型审计绑定不一致")
    if QuestionStore(repo.base).value(semantic["registry_ref"]) != advisor_registry(
        state["request"], studies
    ):
        raise ValueError("问题理解股票身份与本轮已复核研究不一致")
    if semantic["status"] != "COMPLETED":
        return None
    return semantic["data"]


def dialogue(data, understanding, *, portfolio=False, version="question-dialogue-v1"):
    """Render only approved public statements and fixed local calculations."""
    if version not in {"question-dialogue-v1", "question-dialogue-v2"}:
        raise ValueError("未知追问展示协议")
    if not understanding:
        return dict(
            model_used=False,
            focuses=[],
            target_entities=[],
            inherited=False,
            summary="本轮使用原有分析流程",
            lines=["问题理解服务暂不可用，以下为已复核研究与本地核算；可稍后继续追问。"],
        )
    tasks = understanding["tasks"]
    focuses = list(dict.fromkeys(f for t in tasks for f in t["focuses"]))
    targets = list(dict.fromkeys(e for t in tasks for e in t["entities"]))
    lines = []
    if portfolio:
        selected = [
            c
            for c in data["changes"]
            if not targets or c["entity"] in targets or c["name"] in targets
        ]
        if "CONCENTRATION" in focuses:
            breaches = [b for b in data["initial_breaches"] if "行业集中度" in b["label"]]
            lines.append(
                "存在行业集中超限，先按表中减仓数量降低重叠暴露。"
                if breaches
                else "已声明组合当前未触及行业集中上限；同一行业仍须合并观察，"
                "不能把多只股票当成充分分散。"
            )
        if "CORRELATION" in focuses:
            if data["correlation"]["status"] != "COMPUTED" or data["correlation"].get("reason"):
                lines.append("相关性存在不可计算项：" + data["correlation"]["reason"])
            else:
                lines.append(
                    "存在共同波动的持仓群，按群控制风险，不能只逐股看仓位。"
                    if data["correlated_groups"]
                    else "本轮共同窗口未识别超过已填阈值的相关持仓群；"
                    "相关性会变化，仍需保留总风险约束。"
                )
        aggregate_only = set(focuses) <= {"CONCENTRATION", "CORRELATION"}
        if version == "question-dialogue-v2":
            aggregate_only = bool(set(focuses) & {"CONCENTRATION", "CORRELATION"}) and not (
                set(focuses) & {"SELL", "ADD", "INVALIDATION", "BASIS", "ENTRY", "CASHFLOW"}
            )
        for c in selected if not aggregate_only or len(selected) < len(data["changes"]) else []:
            lines.append(c["name"] + "：" + c["opinion"]["summary"])
            if "CONCENTRATION" in focuses:
                from decimal import Decimal

                industry = next(
                    i for i in data["before"]["industries"] if i["code"] == c["industry_code"]
                )
                weight = (Decimal(industry["weight"]) * 100).quantize(Decimal(".01"))
                cap = (Decimal(data["request"]["industry_cap"]) * 100).quantize(Decimal(".01"))
                lines.append(
                    f"{c['name']}所在{industry['name']}合计占总资产 {weight}%，行业上限 {cap}%。"
                )
            if "INVALIDATION" in focuses:
                lines.extend(c["name"] + "：" + s for s in c["invalidation"][:2])
            if any(f in focuses for f in ("SELL", "ADD", "DEFENSE", "GENERAL", "HOLD")):
                lines.append(
                    f"{c['name']}：本轮条件减仓 {c['sell_shares']} 股、"
                    f"条件加仓 {c['buy_shares']} 股；实际成交后更新账户再复核。"
                )
    else:
        lines.append(data["opinion"]["summary"])
        if "INVALIDATION" in focuses:
            lines.extend(data["invalidation"][:3])
        if "DEFENSE" in focuses:
            lines.append(
                "单纯回撤不等于经营失效；同时核对原路线、下行防守参考和本地仓位风险，风险预算触发时仍需处理。"
            )
        if "CASHFLOW" in focuses:
            lines.append(
                "现金回收未变只是待核条件，不能据此确认经营无风险；对照本轮已披露财报及反证，缺少新披露时沿用资料时点。"
            )
        if "ADD" in focuses:
            lines.append(
                "加仓先满足原路线入场条件和账户风险约束；亏损或回到成本不能单独作为补仓依据。"
            )
        if "SELL" in focuses or "HOLD" in focuses:
            lines.append(data["headline"])
    if "BASIS" in focuses or "ENTRY" in focuses:
        lines.extend(k["system_rule"] for k in data["knowledge"][:2])
    return dict(
        model_used=True,
        focuses=focuses,
        target_entities=targets,
        summary=" · ".join(FOCUS_LABELS[f] for f in focuses),
        lines=list(dict.fromkeys(lines))[:10],
        inherited=understanding["inherited"],
    )
