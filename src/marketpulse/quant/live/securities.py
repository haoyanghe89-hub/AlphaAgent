"""Resolve live Shanghai/Shenzhen A shares, never model-invented securities."""

import json
import re

from .sources import fetch

SMARTBOX = "https://smartbox.gtimg.cn/s3/"
PROFILE = "https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax"


def parse_candidates(text):
    raw = text.partition("=")[2].strip().rstrip(";")
    content = json.loads(raw)
    result = []
    for row in content.split("^"):
        parts = row.split("~")
        if len(parts) < 5:
            continue
        market, code, name, _, kind = parts[:5]
        if kind != "GP-A" or market not in {"sh", "sz"} or not re.fullmatch(r"\d{6}", code):
            continue
        if not (
            (market == "sh" and code.startswith(("60", "68")))
            or (market == "sz" and code.startswith(("00", "30")))
        ):
            continue
        result.append(
            dict(
                code=code,
                name=name,
                symbol=market + code,
                exchange="SSE" if market == "sh" else "SZSE",
            )
        )
    return result


def choose(candidates, query):
    exact = [c for c in candidates if query in {c["code"], c["name"], c["symbol"]}]
    options = exact or candidates
    if len(options) != 1:
        if options:
            raise ValueError(
                "匹配多只股票，请明确名称或代码："
                + "、".join(c["name"] + " " + c["code"] for c in options[:8])
            )
        raise ValueError("未找到沪深 A 股，请提供准确名称或六位股票代码。")
    return options[0]


def financial_type(industry):
    if "保险" in industry:
        return "2"
    if "证券" in industry:
        return "3"
    if "银行" in industry and "非银行" not in industry:
        return "1"
    return "4"


def search(query):
    if not query.strip() or len(query) > 80:
        raise ValueError("证券查询长度应为 1–80 字。")
    meta, raw, text = fetch("security_search", SMARTBOX, {"q": query, "t": "all"})
    return parse_candidates(text), meta, raw


def resolve(query, save=None):
    candidates, meta, raw = search(query)
    subject = choose(candidates, query)
    profile_meta, profile_raw, profile = fetch(
        "profile", PROFILE, {"code": subject["symbol"].upper()}
    )
    rows = profile.get("jbzl") or []
    if len(rows) != 1 or rows[0].get("SECURITY_CODE") != subject["code"]:
        raise ValueError("公司资料未能确认证券身份，停止研究。")
    row = rows[0]
    industry = row.get("EM2016") or row.get("INDUSTRYCSRC1")
    if not industry or "A股" not in row.get("SECURITY_TYPE", ""):
        raise ValueError("缺少行业或 A 股身份资料，不能确定计算适用性。")
    subject.update(
        name=row["SECURITY_NAME_ABBR"],
        industry=industry,
        company_type=financial_type(industry),
        description=str(row.get("ORG_PROFILE") or "").strip()[:1500],
    )
    if save:
        save("security_search", raw)
        save("profile", profile_raw)
    return subject, [meta, profile_meta]
