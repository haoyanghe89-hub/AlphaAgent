"""Explicit publication policy for already-public investment research content."""

import json
import math
import re
from datetime import date, datetime
from decimal import Decimal

PUBLIC_CONTENT_POLICY = "alphaagent-public-investment-v1"
MAX_IO_CHARACTERS = 60000
_HIDDEN = "[私有字段已隐藏]"
_TRUNCATED = "\n[观测截断；完整内容保留在本地调用归档]"
_PRIVATE_KEYS = frozenset(
    "profile account accountid accountnumber bankaccount position positions holdings shares "
    "sellableshares boughttoday costprice capital totalassets availablecash reservecash "
    "marketvalue credentials apikey secret secretkey password token accesstoken refreshtoken "
    "authorization headers requestheaders responseheaders cookies cookie rawusertext question "
    "analysisfocus useranalysisfocus "
    "资金 本金 持仓 股数 成本 成本价 可用现金 账号 密码 密钥".split()
)
_CREDENTIAL = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{8,}|(?:Bearer|Basic)\s+[A-Za-z0-9+/=_-]+)", re.I)
_QUERY_SECRET = re.compile(
    r"([?&](?:api[_-]?key|token|secret|signature|auth|password)=)[^&#\s]+", re.I
)


def public_research(state):
    return state.get("request", {}).get("schema_version") in {
        "investment-research-v1",
        "investment-research-v2",
        "investment-research-v3",
        "investment-research-v4",
    }


def public_payload(value):
    """Preserve readable values, removing private fields before and during export."""
    info = {"redactions": 0, "truncated": False}

    def clean(item, depth=0):
        if depth > 24:
            info["truncated"] = True
            return "[观测截断：嵌套层数超限]"
        if isinstance(item, dict):
            result = {}
            for key, child in list(item.items())[:200]:
                normalized = re.sub(r"[\s_\-]", "", str(key)).lower()
                if normalized in _PRIVATE_KEYS:
                    result[str(key)] = _HIDDEN
                    info["redactions"] += 1
                else:
                    result[str(key)] = clean(child, depth + 1)
            if len(item) > 200:
                result["observability_truncated"] = True
                info["truncated"] = True
            return result
        if isinstance(item, (list, tuple)):
            if len(item) > 200:
                info["truncated"] = True
            return [clean(child, depth + 1) for child in item[:200]]
        if isinstance(item, str):
            stripped = item.lstrip()
            if stripped.startswith(("{", "[")):
                try:
                    decoded, end = json.JSONDecoder().raw_decode(stripped)
                    safe = clean(decoded, depth + 1)
                    if safe != decoded:
                        item = json.dumps(safe, ensure_ascii=False) + stripped[end:]
                except (ValueError, RecursionError):
                    pass
            item, count = _CREDENTIAL.subn("[凭据已隐藏]", item)
            info["redactions"] += count
            item, count = _QUERY_SECRET.subn(r"\1[凭据已隐藏]", item)
            info["redactions"] += count
            return item
        if item is None or isinstance(item, (bool, int)):
            return item
        if isinstance(item, float):
            return item if math.isfinite(item) else None
        if isinstance(item, (date, datetime, Decimal)):
            return str(item)
        if hasattr(item, "model_dump"):
            return clean(item.model_dump(mode="json"), depth + 1)
        return "[未发布的对象类型]"

    safe = clean(value)
    encoded = json.dumps(safe, ensure_ascii=False)
    if len(encoded) > MAX_IO_CHARACTERS:
        info["truncated"] = True
        if isinstance(safe, str):
            safe = safe[:MAX_IO_CHARACTERS] + _TRUNCATED
        elif isinstance(safe, list) and all(
            isinstance(m, dict) and "role" in m and isinstance(m.get("content"), str) for m in safe
        ):
            remaining = MAX_IO_CHARACTERS
            for message in safe:
                content = message["content"]
                if len(content) > remaining:
                    message["content"] = content[:remaining] + _TRUNCATED
                remaining = max(0, remaining - len(content))
        else:
            safe = encoded[:MAX_IO_CHARACTERS] + _TRUNCATED
    return safe, info


def validation_failure(error):
    """Publish known check categories, never arbitrary exception messages or locals."""
    categories = (
        (
            "数字引用未绑定",
            "数字未绑定本条引用证据的指标、数值、单位或窗口；需引用封存表达或改为定性判断。",
        ),
        ("数字引用未通过", "数字引用不得用于捏造交易参数、概率或收益承诺。"),
        ("正文含未绑定数字", "正文含未绑定数字、概率或无条件交易指令；需改为定性表述。"),
        ("方法引用不在", "方法引用不在该角色实际收到的知识输入中。"),
        ("方法引用不属于", "方法引用不属于本证券选定的方法。"),
        ("分析未引用", "分析未覆盖要求的资金、政策或技术证据。"),
        ("摘要与明确方向", "摘要与结构化方向不一致。"),
        ("方向判断或判断ID", "主张 ID 或方向依据不一致。"),
        ("Planner选择", "规划选择了未通过准入的路线。"),
        ("复核未逐条覆盖", "复核未逐条覆盖支持观点与反证。"),
        ("未通过全部复核", "尚未满足全部复核条件，不能批准发布。"),
        ("Writer修改", "发布内容与已批准的结果不一致。"),
        ("政策公司连接", "政策与公司连接尚未标明假设或资格待核。"),
        ("本次证据不能证明", "使用了现有证据不能支持的资格、身份或因果断言。"),
    )
    text = str(error)
    for prefix, reason in categories:
        if text.startswith(prefix):
            return "校验未通过：" + reason
    if "跨证券或不存在的事实引用" in text:
        return "校验未通过：引用了跨证券或不存在的证据 ID。"
    return "校验未通过（" + type(error).__name__ + "）；详细诊断保留在本地归档。"
