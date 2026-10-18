"""Bind quantity arithmetic to sealed current exchange clauses."""

import re

from ..decision.next_session.calendar import plain, rule_text


def verify_trading_rules(packet, raw_bytes):
    sources = packet["trading_rule_sources"]
    result = []
    for exchange in packet["calendars"]:
        item = next(s for s in sources if s["exchange"] == exchange and s["kind"] == "RULES")
        text = plain(rule_text(raw_bytes(item["source"]["blob_ref"])))
        match = re.search(r"(?<!\d\.)3\.3\.8(.{0,150})3\.3\.9(.{0,180})3\.3\.10", text)
        if (
            not match
            or "100股" not in match[1]
            or "整数倍" not in match[1]
            or "100万股" not in match[2]
            or "2026年7月6日" not in text
            or packet["market_date"] < "20260706"
        ):
            raise ValueError("current exchange quantity clauses not verified")
        if exchange == "SZSE" and ("30万股" not in match[2] or "15万股" not in match[2]):
            raise ValueError("ChiNext quantity clauses not verified")
        locators = [dict(start=match.start(), end=match.end(), text=match[0])]
        if exchange == "SSE":
            star = re.search(r"(?<!\.)6\.7(.{0,650})6\.8", text)
            if not star or "200股" not in star[1] or "10万股" not in star[1]:
                raise ValueError("STAR quantity clauses not verified")
            deferred = next(
                s for s in sources if s["exchange"] == exchange and s["kind"] == "DEFERRED"
            )
            deferred_text = plain(rule_text(raw_bytes(deferred["source"]["blob_ref"])))
            if any(anchor in deferred_text for anchor in ("3.3.8", "3.3.9", "6.7")):
                raise ValueError("quantity clauses appear in deferred annex")
            locators.append(dict(start=star.start(), end=star.end(), text=star[0]))
        result.append(
            dict(
                exchange=exchange,
                source=item["source"],
                locators=locators,
                effective_date="20260706",
                order_kind="LIMIT_COMPETITIVE_BUY",
            )
        )
    return result
