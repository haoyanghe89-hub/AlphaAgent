"""Bounded official originals and company-specific policy hypotheses."""

import re
from datetime import date, datetime

from ..decision import policy_sources as legacy

VERSION = "investment-policy-originals-v1"
SOURCES = {
    **legacy.SOURCES,
    "CN-RENEWAL-2024": dict(
        url="https://www.gov.cn/zhengce/content/202403/content_6939232.htm",
        family="POLICY_CN",
        jurisdiction="CN",
        selectors={"body": "UCAP-CONTENT"},
        title="推动大规模设备更新和消费品以旧换新行动方案",
        number="国发〔2024〕7号",
        issuer="国务院",
        published_at="2024-03-13",
    ),
    "CN-HOME-2026": dict(
        url="https://www.mofcom.gov.cn/zfxxgk/fdzdgknr/ztfl/gnmygl/art/2025/art_b0f88c7df21c4d7aa74d7408e84f8860.html",
        family="POLICY_CN",
        jurisdiction="CN",
        selectors={"body": "gsj_htmlcon_bot", "heading": "ctitle", "meta": "content_head"},
        title="关于做好2026年家电以旧换新、数码和智能产品购新补贴工作的通知",
        number="商办流通函2025年第469号",
        issuer="商务部等五部门办公厅（室）",
        published_at="2025-12-31",
    ),
}


class PolicyHTML(legacy.PolicyHTML):
    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        for node in self.selectors.values():
            if node in values.get("class", "").split():
                attrs = [(k, v) for k, v in attrs if k != "id"] + [("id", node)]
                break
        super().handle_starttag(tag, attrs)


def category(subject):
    text = subject.get("main_business", "")
    if legacy.semiconductor_subject(dict(industry=subject["industry"], description=text)):
        return "semi"
    if "软件" in subject["industry"]:
        return "software"
    if re.search(r"家电|家用电器", subject["industry"]) or re.search(r"空调|冰箱|洗衣机", text):
        return "home"
    if re.search(r"银行|证券|保险|多元金融", subject["industry"]):
        return "finance"
    if re.search(r"机械|设备|煤炭|钢铁|交通运输|化工|有色|电力|汽车", subject["industry"]):
        return "equipment"
    return "other"


def selected(subject):
    kind = category(subject)
    if kind == "semi":
        return ["CN-STATE-2020-8", "US-EAR-74423"]
    if kind == "software":
        return ["CN-STATE-2020-8"]
    if kind == "home":
        return ["CN-HOME-2026", "CN-RENEWAL-2024"]
    return ["CN-RENEWAL-2024"] if kind in {"finance", "equipment"} else []


def temporal(effective, expires, cutoff, status="UNKNOWN"):
    day = datetime.fromisoformat(cutoff).date()
    if status in {"REPEALED", "CONSULTATION", "NOT_YET_EFFECTIVE"}:
        return status
    if effective and date.fromisoformat(effective) > day:
        return "NOT_YET_EFFECTIVE"
    if expires and date.fromisoformat(expires) < day:
        return "EXPIRED"
    return "WITHIN_DECLARED_WINDOW" if effective else "UNKNOWN"


def parse(raw, policy_id, fetched_at, kind):
    spec = SOURCES[policy_id]
    if policy_id in legacy.SOURCES:
        parsed, texts = legacy.parse_policy(raw, policy_id, fetched_at)
        spans = parsed.pop("excerpts")
    else:
        parser = PolicyHTML(spec["selectors"])
        parser.feed(raw.decode("utf-8-sig"))
        texts = parser.texts()
        body = texts["body"]
        all_text = " ".join(texts.values())
        if (
            len(body) < 600
            or spec["number"] not in all_text
            or spec["title"] not in all_text.replace(" ", "")
        ):
            raise ValueError("官方题录与政策正文未核对")
        dates = set(re.findall(r"\d{4}-\d{2}-\d{2}", texts["meta"]))
        dates.update(
            date(int(y), int(m), int(d)).isoformat()
            for y, m, d in re.findall(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", texts["meta"])
        )
        if spec["published_at"] not in dates:
            raise ValueError("政策发布日期未由原文题录确认")
        status, validity, effective, expires = legacy.document_state(
            spec["title"], body, texts["meta"], fetched_at
        )
        if policy_id == "CN-HOME-2026":
            effective = "2026-01-01" if "2026年1月1日起" in body else None
            markers = ["一、明确享受补贴品类、范围和标准", "四、优化政策实施方式"]
        else:
            markers = [{"home": "（八）", "finance": "（十八）"}.get(kind, "（一）")]
        spans = []
        for marker in markers:
            start = body.find(marker)
            if start < 0:
                raise ValueError("相关政策条款未定位")
            spans.append(
                dict(key="body", start=start, end=min(len(body), start + 220), label=marker)
            )
        parsed = dict(
            policy_id=policy_id,
            title=spec["title"],
            issuer=spec["issuer"],
            jurisdiction="CN",
            document_number=spec["number"],
            document_kind="FORMAL_POLICY",
            document_status=status,
            published_at=spec["published_at"],
            effective_at=effective,
            expires_at=expires,
            current_validity=validity,
            version_as_of=None,
            amendment_status="UNKNOWN",
            qualification_status="UNKNOWN",
            company_link_status="HYPOTHESIS",
            scope="限定原文条款，产品、项目、地域与公司资格未核定",
        )
    if parsed["published_at"] and parsed["published_at"] > fetched_at[:10]:
        raise ValueError("未来政策发布日期")
    if parsed["version_as_of"] and parsed["version_as_of"] > fetched_at[:10]:
        raise ValueError("未来政策版本")
    declared_end = "2026-12-31" if policy_id == "CN-HOME-2026" else parsed["expires_at"]
    parsed.update(
        temporal_status=temporal(
            parsed["effective_at"], declared_end, fetched_at, parsed["document_status"]
        ),
        declared_scope_end=declared_end,
        scope_end_basis="年度实施范围，非正式废止日期" if policy_id == "CN-HOME-2026" else None,
        clauses=[
            dict(span, text=texts[span["key"]][span["start"] : span["end"]]) for span in spans
        ],
        limitations=legacy.LIMITS,
    )
    return parsed
