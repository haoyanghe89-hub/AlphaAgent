"""Readable projections of public investment boundaries; never load private reports wholesale."""

import json


def text(value):
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def request_input(state):
    request = state.get("request", {})
    return {
        "任务": "生成投资研究计划",
        **{
            k: request[k]
            for k in (
                "entities",
                "schema_version",
                "horizon",
                "horizon_sessions",
                "execution_contract",
            )
            if k in request
        },
        "隐私": "账户资金及持仓仅在本地计算，不进入此观测内容。",
    }


def report_output(state):
    report = state.get("data") or {}
    lines = ["# 投资研究交付", "状态：" + state.get("status", "UNKNOWN")]
    for key in ("market_date", "conclusion"):
        if report.get(key):
            lines.append(str(report[key]))
    for item in report.get("candidates", []):
        lines.append(
            "\n## " + item.get("name", item["security_id"]) + "（" + item["security_id"] + "）"
        )
        lines.append(
            "路线：" + str(item.get("selected_route")) + "；行动：" + str(item.get("action"))
        )
    for opinion in report.get("research_opinions", []):
        lines.extend(
            [
                "\n### " + opinion["security_id"],
                opinion.get("summary", ""),
                "方向：" + opinion.get("direction", "UNKNOWN"),
            ]
        )
        lines.append("最终引用方法：" + "、".join(opinion.get("method_rule_ids", [])))
        for kind, label in (("claims", "支持观点"), ("opposing", "反证")):
            for claim in opinion.get(kind, []):
                lines.append(
                    "- "
                    + label
                    + " "
                    + claim["claim_id"]
                    + "："
                    + claim["text"]
                    + "\n  证据："
                    + "、".join(claim["evidence_ids"])
                )
        for key, label in (("invalidation", "失效条件"), ("next_checks", "下一步核查")):
            lines.extend("- " + label + "：" + value for value in opinion.get(key, []))
    lines.extend("- 限制：" + v for v in report.get("caveats", []))
    if state.get("result_ref"):
        lines.append("完整报告本地归档：" + state["result_ref"])
    return "\n".join(lines)


def collect_input(arguments):
    packet = arguments["packet"]
    return (
        "采集公开公司业务、日频/分钟资金代理及定向官方政策。\n行情日期："
        + str(packet["market_date"])
        + "\n证券："
        + "、".join(s["ts_code"] for s in packet["stocks"])
    )


def sources_input(arguments):
    snapshot = arguments["snapshot"]
    return {
        "market_date": snapshot["market_date"],
        "captured_at": snapshot["captured_at"],
        "entries": snapshot["entries"],
    }


def external_output(report, _arguments=None):
    lines = [
        "# 实际取得并解析的公开资料",
        "截止：" + str(report.get("cutoff")),
        "以下是归档原文的解析展示；候选资料不代表已被模型或最终报告引用。",
    ]
    for stock in report["stocks"]:
        lines.append("\n## " + stock["security_id"])
        lines.append("主营业务：" + (stock.get("main_business") or "未取得"))
        for key, label in (("daily", "日频资金代理"), ("minute", "分钟资金代理")):
            view = stock.get(key)
            if not view:
                lines.append(label + "：未取得")
                continue
            lines.append("\n### " + label + "；数据时间：" + str(view.get("asof")))
            # Minute series are deliberately sampled here; full bytes remain content-addressed.
            details = {k: v for k, v in view.items() if k not in {"source", "points"}}
            points = view.get("points", [])
            lines.append(text(details))
            if points:
                sample = points if len(points) <= 20 else points[:3] + points[-3:]
                lines.append(
                    "原序列 "
                    + str(len(points))
                    + " 条；展示 "
                    + str(len(sample))
                    + " 条：\n"
                    + text(sample)
                )
        for policy in stock.get("policies", []):
            lines.extend(
                [
                    "\n### 政策：" + policy["title"],
                    text({k: v for k, v in policy.items() if k != "source"}),
                ]
            )
    lines.append("\n## 来源与原文定位")
    for source in report.get("sources", []):
        lines.extend(
            [
                "- "
                + source.get("source_family", "")
                + " "
                + source.get("api_name", source.get("key", "")),
                "  URL：" + source.get("url", ""),
                "  参数：" + text(source.get("parameters", {})),
                "  获取时间：" + source.get("fetched_at", ""),
                "  完整原文：" + source.get("blob_ref", ""),
            ]
        )
    lines.append("\n缺项：" + text(report.get("gaps", [])))
    return "\n".join(lines)


def collected_output(snapshot, arguments):
    from marketpulse.quant.knowledge.context import rebuild

    # Pure replay of already archived bytes: no network, tool call or model invocation.
    report = rebuild.__wrapped__(arguments["repo"], arguments["packet"], snapshot)
    return external_output(report)


def knowledge_input(arguments):
    return {
        "cutoff": arguments["cutoff"],
        "selection": "当前发布规则" if arguments.get("dynamic") else "固定投资方法集合",
        "status": "候选知识快照；后续角色输入及最终引用分别记录。",
    }


def knowledge_output(result, _arguments):
    lines = [
        "# 本次加载的知识规则",
        "候选知识共 "
        + str(len(result["fragments"]))
        + " 条；是否交给角色及最终采用，请查看上下文、model.call 和最终交付。",
    ]
    for item in result["fragments"]:
        lines.extend(
            [
                "\n## " + item["rule_id"] + " · " + item["title"],
                "版本：" + str(item["version"]),
                "来源：" + item["source_title"] + "\n" + item["url"],
                "文档："
                + item["document_id"]
                + " @ "
                + item["document_version"]
                + "；字符定位："
                + str(item["start"])
                + "—"
                + str(item["end"]),
                "原文摘录：\n> " + item["quote"],
                "系统执行规则：" + item["system_rule"],
                "来源主张：" + item["source_claim"],
                "适配说明：" + item["adaptation"],
                "反例：" + text(item["counterexamples"]),
            ]
        )
    return "\n".join(lines)


def context_input(arguments):
    context = arguments.get("context", {})
    return {
        "method_ids_before": [m["rule_id"] for m in context.get("methods", [])],
        "evidence_ids_before": [e["evidence_id"] for e in context.get("evidence", [])],
        **{
            k: arguments[k]
            for k in ("version", "plan", "review_round", "sessions", "cutoff")
            if k in arguments
        },
    }


def context_output(result, _arguments):
    return result[0] if isinstance(result, tuple) else result


def validation_input(arguments):
    return {
        k: v
        for k, v in arguments.items()
        if k in {"plan", "analysis", "review", "writing", "approved", "context"}
    }


def validation_output(_result, _arguments):
    return "校验通过：本阶段所有实际执行的业务约束均通过。"
