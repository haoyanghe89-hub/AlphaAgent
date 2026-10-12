"""Bounded lexical RAG over captured disclosure text with character locators."""

import re

from .overview import build_overview
from .sources import fetch

ANN_LIST = "https://np-anotice-stock.eastmoney.com/api/security/ann"
ANN_BODY = "https://np-cnotice-stock.eastmoney.com/api/content/ann"


def announcements(subject, save):
    meta, raw, packet = fetch(
        "announcements",
        ANN_LIST,
        dict(sr="-1", page_size=10, page_index=1, ann_type="A", stock_list=subject["code"]),
    )
    save("announcements", raw)
    sources, documents = [meta], []
    for item in (packet.get("data") or {}).get("list", [])[:4]:
        if not any(c.get("stock_code") == subject["code"] for c in item.get("codes", [])):
            raise ValueError("announcement security mismatch")
        code = item.get("art_code", "")
        if not re.fullmatch(r"AN\d{18}", code):
            continue
        key = "notice_" + code
        detail, body, value = fetch(
            key, ANN_BODY, dict(art_code=code, page_index=1, client_source="web")
        )
        data = value.get("data") or {}
        if data.get("art_code") != code:
            raise ValueError("announcement identity mismatch")
        text = data.get("notice_content") or ""
        if not text.strip():
            continue
        save(key, body)
        sources.append(detail)
        documents.append(
            dict(
                source=key,
                title=item["title"],
                disclosed=item["notice_date"][:10],
                text=text[:40000],
                url=detail["url"],
                sha256=detail["sha256"],
                kind="供应商转载公告正文",
            )
        )
        if len(documents) == 2:
            break
    return documents, sources


def terms(text):
    return set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]{2}", text.lower()))


def retrieve(documents, question, limit=6):
    wanted = terms(question)
    chunks = []
    for doc in documents:
        text = doc["text"]
        for start in range(0, len(text), 700):
            end = min(start + 900, len(text))
            content = text[start:end]
            chunks.append(
                dict(
                    id=f"J{len(chunks)}",
                    text=content,
                    source=doc["source"],
                    title=doc["title"],
                    start=start,
                    end=end,
                    sha256=doc["sha256"],
                    url=doc["url"],
                    disclosed=doc.get("disclosed"),
                    text_ref=doc.get("text_ref"),
                    score=len(wanted & terms(content)),
                )
            )
    return sorted(chunks, key=lambda c: (-c["score"], c["id"]))[:limit]


def facts(data):
    result = []
    for m in data["metrics"]:
        result.append(
            dict(
                id="F" + str(len(result)),
                text=f"{m['name']}：{m['value']} {m['unit']}",
                source=m["source"].split(" / "),
                locator="metrics/" + m["name"],
            )
        )
    for name, item in [("DCF 适用性", data["dcf"]), ("估值资料", data["valuation"])]:
        if item.get("reason"):
            result.append(
                dict(
                    id="F" + str(len(result)),
                    text=name + "：" + item["reason"],
                    source=["profile"],
                    locator=name,
                )
            )
    if "per_share" in data["dcf"]:
        result.append(
            dict(
                id="F" + str(len(result)),
                text=(
                    f"DCF 条件情景估算：{data['dcf']['per_share']} 元/股，属于参数假设，不是目标价"
                ),
                source=["cash_em", "income_em", "balance_em", "valuation"],
                locator="dcf/per_share",
            )
        )
    risk_sources = {
        "股权质押": "pledge",
        "限售股解禁": "unlock",
        "商誉与减值": "fzb",
        "现金流与审计": "llb",
        "ST / 退市观察": "quotes",
        "权益与商誉口径": "fzb",
    }
    for r in data["risks"]:
        result.append(
            dict(
                id="F" + str(len(result)),
                text=r["title"] + "：" + r["text"],
                source=[risk_sources.get(r["title"], "profile")],
                locator="risks/" + r["title"],
            )
        )
    result.append(
        dict(
            id="F" + str(len(result)),
            text="原始公告独立数值核验：UNAVAILABLE；供应商核对一致不证明官方口径已经验证。",
            source=["lrb"],
            locator="checks",
        )
    )
    # Append facts so existing IDs remain stable. These same calculations power
    # the reader page; the model receives dates and method limits with values.
    reading = build_overview(data)
    if reading["company"]["description"]:
        result.append(
            dict(
                id="F" + str(len(result)),
                text="供应商公司简介（不等于业务收入贡献）：" + reading["company"]["description"],
                source=["profile"],
                locator="subject/description",
            )
        )
    for key in ("price", "growth", "valuation", "dcf"):
        item = reading[key]
        if item["status"] == "AVAILABLE" and item.get("proof"):
            p = item["proof"]
            result.append(
                dict(
                    id="F" + str(len(result)),
                    text=p["text"],
                    source=p["source"],
                    locator=p["locator"],
                )
            )
    return result
