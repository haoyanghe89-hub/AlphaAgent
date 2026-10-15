"""One bounded batch: dynamic equipment constituents, aligned observations and originals."""

import copy
import json
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from statistics import median

from marketpulse.infrastructure.storage.models import BlobRef

from ..contracts import digest
from ..live.securities import PROFILE, financial_type
from ..live.sources import EM, KLINE, em_params
from .contracts import CandidateUniverse, EvidenceBundle, EvidenceItem, Locator
from .disclosures import QUERY, REGISTRY, select_announcements
from .evidence import parse_pdf, retrieve, verify_document
from .market import number
from .next_session.calendar import load_calendar, parse_annual
from .sector_profiles import profile as business_profile
from .service import SHANGHAI, source_capability
from .tools import SourceClient, SourceUnavailable

VERSION = "candidate-sources-2026-10-13-v1"
VERSION_V2 = "candidate-sources-2026-10-14-v2"
LIST = "https://push2.eastmoney.com/api/qt/clist/get"
FIELDS = "f12,f13,f14,f20,f9"
PRODUCTS = r"刻蚀|薄膜|沉积|清洗|抛光|测试|检测|量测|计量|MOCVD|CMP"
LIMITATIONS = [
    "仅当前供应商半导体设备分类池，不是全市场或历史成分；分类归属不能证明经营地位。",
    "财务为供应商转录的合并报表同期间数据，未做逐项原文及外部独立数值核验。",
    "动态市盈率仅供应商快照，负值或缺失不作低估；不代表完整估值或一致预期。",
    "停牌仅依本窗行情观察；ST、退市和重大风险档案未全量核定，未知不等于安全。",
    "成交额只是流动性；融资为单一渠道，不表示全市场净流入或机构意图。",
]


def rows(packet):
    values = (packet.get("data") or {}).get("diff") or []
    return list(values.values()) if isinstance(values, dict) else values


def report_date(today):
    # Latest quarter whose normal reporting deadline has passed for the whole pool.
    if today.month >= 11:
        return f"{today.year}-09-30"
    if today.month >= 9:
        return f"{today.year}-06-30"
    if today.month >= 5:
        return f"{today.year}-03-31"
    return f"{today.year - 1}-09-30"


def checked(repo, source, cutoff):
    raw = repo.raw_bytes(source["blob_ref"])
    if (
        hashlib_of(raw) != source["sha256"]
        or BlobRef.from_uri(source["blob_ref"]).sha256 != source["sha256"]
    ):
        raise ValueError("candidate source hash mismatch")
    if datetime.fromisoformat(source["fetched_at"]) > datetime.fromisoformat(cutoff):
        raise ValueError("candidate source exceeds cutoff")
    return json.loads(raw)


def hashlib_of(raw):
    import hashlib

    return hashlib.sha256(raw).hexdigest()


def resolve_member(client, member):
    code = member["code"]
    sid = member["security_id"]
    symbol = ("sh" if sid.endswith(".SH") else "sz") + code
    _, packet = client.fetch(
        "profile", PROFILE, params={"code": symbol.upper()}, family="EASTMONEY_PROFILE"
    )
    values = packet.get("jbzl") or []
    if (
        len(values) != 1
        or values[0].get("SECURITY_CODE") != code
        or values[0].get("SECURITY_NAME_ABBR") != member["name"]
    ):
        raise ValueError("candidate profile differs from sealed member identity")
    row = values[0]
    industry = row.get("EM2016") or row.get("INDUSTRYCSRC1")
    if not industry or "A股" not in row.get("SECURITY_TYPE", ""):
        raise ValueError("candidate profile lacks A-share/industry identity")
    return dict(
        code=code,
        security_id=sid,
        name=member["name"],
        symbol=symbol,
        exchange="SSE" if sid.endswith(".SH") else "SZSE",
        industry=industry,
        company_type=financial_type(industry),
        description=str(row.get("ORG_PROFILE") or "")[:1500],
    )


def collect(repo, state, stop, save, step):
    from . import candidate_margin, tushare

    if tushare.load_token():
        return candidate_margin.collect(repo, state, stop, save, step)
    client = SourceClient(repo, state, save, stop, max_requests=52)
    entity = state["request"].get("entity", "半导体设备")
    business = business_profile(entity)
    snapshot = dict(
        version=VERSION_V2,
        industry_profile=business,
        entries=[],
        documents=[],
        subjects=[],
        gaps=[],
        depth_limit=state["request"]["budget_profile"]["max_candidates"],
    )

    def fetch(role, key, url, **kwargs):
        source, value = client.fetch(key, url, **kwargs)
        snapshot["entries"].append(dict(role=role, source=source))
        return source, value

    step("pool", "读取运行时" + entity + "分类与成分")
    board = None
    for page in range(1, 7):
        _, packet = fetch(
            "classification",
            f"candidate_boards_{page}",
            LIST,
            family="EASTMONEY_CLASSIFICATION",
            params=dict(
                pn=page, pz=100, fs="m:90+s:8+f:!50", fid="f12", po=1, fields="f12,f14", np=1
            ),
        )
        matches = [r for r in rows(packet) if r["f14"] == entity]
        if matches:
            if len(matches) != 1 or not re.fullmatch(r"BK\d+", matches[0]["f12"]):
                raise ValueError("ambiguous equipment classification")
            board = matches[0]
            break
        if page * 100 >= int(packet["data"]["total"]):
            break
    if board is None:
        raise SourceUnavailable("当前行业分类未找到" + entity + "，不使用固定股票名单代替")
    snapshot["board_id"] = board["f12"]
    _, packet = fetch(
        "membership",
        "candidate_members",
        LIST,
        family="EASTMONEY_CLASSIFICATION",
        params=dict(pn=1, pz=100, fs="b:" + board["f12"], fid="f12", po=1, fields=FIELDS, fltt=2),
    )
    members = rows(packet)
    if len(members) != int(packet["data"]["total"]) or not members:
        raise SourceUnavailable("成分分页未完整取得，不能缩小分母或构造候选")
    if len(members) > 30:
        raise SourceUnavailable("成分数量超出本轮有界全池行情预算；未截断或换排序规则")
    snapshot["calendar"] = load_calendar(client, "SSE").model_dump(mode="json")
    local = datetime.now(UTC).astimezone(SHANGHAI)
    if (
        not snapshot["calendar"]["coverage_start"]
        <= local.date().isoformat()
        <= snapshot["calendar"]["coverage_end"]
    ):
        raise SourceUnavailable("年度交易日历未核定，未用旧日历回填")
    snapshot["market_at"] = local.isoformat()
    sessions = [
        d
        for d in snapshot["calendar"]["sessions"]
        if datetime.fromisoformat(d + "T15:00:00+08:00") <= local
    ]
    snapshot["sessions"] = sessions[-21:]
    snapshot["period"] = report_date(local.date())
    step("pool", f"已封存{len(members)}只成分，逐只核对共同交易窗口")
    for member in members:
        code, exchange = member["f12"], member["f13"]
        if not re.fullmatch(r"\d{6}", code) or exchange not in (0, 1):
            raise ValueError("invalid A-share member")
        symbol = ("sh" if exchange == 1 else "sz") + code
        try:
            fetch(
                "daily:" + code,
                "candidate_daily_" + code,
                KLINE,
                family="TENCENT",
                params=dict(param=f"{symbol},day,{sessions[-21]},{sessions[-1]},640,"),
            )
        except SourceUnavailable as error:
            snapshot["gaps"].append(dict(code=code, category="daily", reason=str(error)))
    codes = ",".join('"' + r["f12"] + '"' for r in members)
    for role, report in (("income", "INCOME"), ("balance", "BALANCE"), ("cash", "CASHFLOW")):
        try:
            fetch(
                role,
                "candidate_financial_" + role,
                EM,
                family="EASTMONEY_FINANCIAL",
                params=dict(
                    reportName="RPT_DMSK_FN_" + report,
                    columns="ALL",
                    filter=f"(SECURITY_CODE in ({codes}))(REPORT_DATE='{snapshot['period']}')",
                    pageSize=100,
                    pageNumber=1,
                ),
            )
        except SourceUnavailable as error:
            snapshot["gaps"].append(dict(category=role, reason=str(error)))
    return collect_originals(repo, state, snapshot, client, save, step, fetch, local)


def collect_originals(repo, state, snapshot, client, save, step, fetch, local):
    from . import tushare_candidates

    snapshot["cutoff_at"] = datetime.now(UTC).isoformat()
    preliminary = rebuild(repo, snapshot)
    shortlist = [
        r
        for r in preliminary["members"]
        if r["core_complete"] and r["liquidity_ready"] and not r["excluded"]
    ][: snapshot["depth_limit"]]
    step("originals", f"按固定规则定向核查{len(shortlist)}家公司；共享同一预算")
    registry = None
    for row in shortlist:
        if len(state["calls"]) > client.max_requests - 6:
            snapshot["gaps"].append(
                dict(
                    code=row["code"], category="budget", reason="保留复核与错误重试空间，本次未深研"
                )
            )
            continue
        try:
            subject = (
                tushare_candidates.resolve_member(repo, snapshot, client, row)
                if snapshot["version"] in tushare_candidates.VERSIONS
                else resolve_member(client, row)
            )
            if subject["security_id"] != row["security_id"] or subject["name"] != row["name"]:
                raise ValueError("candidate member/profile identity mismatch")
            snapshot["subjects"].append(subject)
            if registry is None:
                _, registry = fetch("registry", "candidate_issuers", REGISTRY, family="CNINFO")
            issuers = [i for i in registry["stockList"] if i["code"] == row["code"]]
            if len(issuers) != 1:
                raise ValueError("candidate issuer not unique")
            params = dict(
                pageNum="1",
                pageSize="30",
                column="sse" if subject["exchange"] == "SSE" else "szse",
                tabName="fulltext",
                plate="",
                stock=row["code"] + "," + issuers[0]["orgId"],
                searchkey="半年度报告" if "06-30" in snapshot["period"] else "季度报告",
                secid="",
                category="category_ndbg_szsh;category_bndbg_szsh;category_sjdbg_szsh;category_yjdbg_szsh;",
                trade="",
                seDate=f"{local.year}-01-01~{local.date()}",
                sortName="time",
                sortType="desc",
                isHLtitle="true",
            )
            _, packet = fetch(
                "catalog:" + row["code"],
                "candidate_catalog_" + row["code"],
                QUERY,
                family="CNINFO",
                data=params,
            )
            selected = select_announcements(
                packet.get("announcements") or [],
                "periodic",
                f"{local.year}年半年度报告" if "06-30" in snapshot["period"] else "季度报告",
                row["code"],
                1,
            )
            if selected:
                source, raw = client.fetch(
                    "candidate_pdf_" + row["code"],
                    selected[0]["original_url"],
                    family="CNINFO",
                    binary=True,
                )
                snapshot["documents"].append(
                    parse_pdf(repo, subject, dict(selected[0], category="periodic"), source, raw)
                )
            try:
                fetch(
                    "margin:" + row["code"],
                    "candidate_margin_" + row["code"],
                    EM,
                    family="EASTMONEY_MARGIN",
                    params=em_params("RPTA_WEB_RZRQ_GGMX", "DATE", 60, "SCODE", row["code"]),
                )
            except SourceUnavailable as error:
                snapshot["gaps"].append(
                    dict(code=row["code"], category="margin", reason=str(error))
                )
        except (SourceUnavailable, ValueError, KeyError) as error:
            snapshot["gaps"].append(dict(code=row["code"], category="original", reason=str(error)))
    snapshot["sources"] = [c["source"] for c in state["calls"] if c.get("source")]
    snapshot["cutoff_at"] = datetime.now(UTC).isoformat()
    return snapshot


def rebuild(repo, snapshot):
    from . import candidate_margin

    if snapshot["version"] == candidate_margin.VERSION:
        return candidate_margin.rebuild(repo, snapshot)
    from . import tushare_candidates, tushare_market

    is_tushare = snapshot["version"] in tushare_candidates.VERSIONS
    if snapshot["version"] not in {VERSION, VERSION_V2, *tushare_candidates.VERSIONS}:
        raise ValueError("unknown candidate source version")
    business = snapshot.get("industry_profile")
    entity = business["entity"] if business else "半导体设备"
    if business and business != business_profile(entity):
        raise ValueError("industry business contract changed")
    limitations = list(LIMITATIONS)
    if business:
        limitations[0] = (
            "仅当前供应商" + entity + "分类池，不是全市场或历史成分；分类归属不能证明经营地位。"
        )
        limitations.extend(business["limits"])
    if is_tushare:
        limitations[0] = "申万2021当前沪深A股分类池；北交所明确排除，不是历史成分回放。"
        limitations[2] = "Tushare过去十二月市盈率快照；缺项/负值不作低估，不代表完整估值。"
        if snapshot["version"] == tushare_candidates.VERSION:
            limitations.append(
                "财务同期间多版本按供应商update_flag=1选择唯一最新版；原始版本均保留，非严格历史时点可知。"
            )
    entries = (
        tushare_candidates.entries(repo, snapshot)
        if is_tushare
        else {
            e["role"]: (e["source"], checked(repo, e["source"], snapshot["cutoff_at"]))
            for e in snapshot["entries"]
        }
    )
    classification = [
        r for key, (_, p) in entries.items() if key.startswith("classification") for r in rows(p)
    ]
    if [r for r in classification if r["f12"] == snapshot["board_id"]] != [
        dict(f12=snapshot["board_id"], f14=entity)
    ]:
        raise ValueError("equipment classification differs from original")
    member_source, packet = entries["membership"]
    members = rows(packet)
    if not members or len(members) != int(packet["data"]["total"]):
        raise ValueError("incomplete candidate membership")
    codes = [r["f12"] for r in members]
    if len(codes) != len(set(codes)):
        raise ValueError("duplicate candidate member")
    calendar = snapshot["calendar"]
    market_at = datetime.fromisoformat(snapshot.get("market_at", snapshot["cutoff_at"]))
    if market_at > datetime.fromisoformat(snapshot["cutoff_at"]):
        raise ValueError("candidate market anchor exceeds cutoff")
    official = [
        str(d)
        for d in parse_annual(repo.raw_bytes(calendar["source_refs"][0]).decode("utf8"), 2026)
    ]
    if official != calendar["sessions"] or snapshot["sessions"] != (
        tushare_market.ready_sessions(calendar, market_at)
        if is_tushare
        else [d for d in official if datetime.fromisoformat(d + "T15:00:00+08:00") <= market_at][
            -21:
        ]
    ):
        raise ValueError("candidate calendar/window mismatch")
    universe = CandidateUniverse(
        universe_id=("SW2021-" if is_tushare else "EM-")
        + snapshot["board_id"]
        + "-"
        + member_source["sha256"][:12],
        members=tuple(
            sorted(
                c + (".SH" if r["f13"] == 1 else ".SZ") for c, r in zip(codes, members, strict=True)
            )
        ),
        classification_version=(
            "SW2021:L3:"
            if is_tushare
            else "EASTMONEY_DC_LEVEL3:"
            if business
            else "EASTMONEY_INDUSTRY:"
        )
        + snapshot["board_id"],
        membership_at=member_source["fetched_at"],
        source_refs=(member_source["blob_ref"],),
        business_scope=entity + "供应商分类池，非全市场",
    ).model_dump(mode="json")
    evidence, result = [], []

    def numeric(source, sid, field, value, path, label, period=None):
        if is_tushare and source["source_family"] == "TUSHARE":
            match = re.search(r"/result/data/(\d+)/", path)
            path = tushare_candidates.locator(repo, source, field, int(match[1]) if match else 0)
            if field == "pe":
                label = "Tushare过去十二月市盈率快照"
        eid = "EV-CAND-" + sid + "-" + field
        evidence.append(
            EvidenceItem(
                evidence_id=eid,
                subject_id=sid,
                kind="fact",
                source_family=source["source_family"],
                original_source_id=source["key"],
                url=source["url"],
                raw_ref=source["blob_ref"],
                content_hash=source["sha256"],
                locator=Locator(kind="json", path=path),
                text=label + "：" + str(value),
                value=value,
                unit="ratio" if field == "pe" else "CNY",
                fetched_at=source["fetched_at"],
                available_at=source["fetched_at"],
                time_precision="day",
                period_end=period,
                method_version=snapshot["version"],
                quality_status="PARTIAL",
                limitations=tuple(limitations),
            ).model_dump(mode="json")
        )
        return eid

    for index, member in enumerate(members):
        code = member["f12"]
        if not re.fullmatch(r"\d{6}", code) or member["f13"] not in (0, 1):
            raise ValueError("invalid member identity")
        sid = code + (".SH" if member["f13"] == 1 else ".SZ")
        row = dict(
            code=code,
            security_id=sid,
            name=member["f14"],
            business_group="MANUFACTURING_UNVERIFIED"
            if business and business["role"] == "MANUFACTURING"
            else "EQUIPMENT_UNVERIFIED",
            metrics={},
            evidence_ids=[],
            business_evidence_ids=[],
            excluded=False,
            gaps=[],
            period=snapshot["period"],
            risk_status="UNKNOWN",
        )
        key = (
            str(index)
            if isinstance(packet["data"]["diff"], list)
            else next(k for k, v in packet["data"]["diff"].items() if v["f12"] == code)
        )
        for field, output, label in (
            ("f20", "market_cap", "供应商总市值快照（元）"),
            ("f9", "pe", "供应商动态市盈率快照"),
        ):
            try:
                value = number(member[field], positive=True)
                row["metrics"][output] = str(value)
                row["evidence_ids"].append(
                    numeric(
                        entries["valuation:" + code][0] if is_tushare else member_source,
                        sid,
                        output,
                        value,
                        f"/data/diff/{key}/{field}",
                        label,
                    )
                )
            except (ValueError, KeyError, InvalidOperation):
                row["gaps"].append(output + "缺项或非正值，不评价低估")
        daily = entries.get("daily:" + code)
        try:
            symbol = ("sh" if member["f13"] == 1 else "sz") + code
            original = daily[1]["data"][symbol]["day"] if daily else []
            actual = {r[0]: r for r in original if r[0] in snapshot["sessions"]}
            if len(actual) != 21 or len([r for r in original if r[0] in actual]) != 21:
                raise ValueError("共同窗口缺交易日或重复，不补零")
            ordered = [actual[d] for d in snapshot["sessions"]]
            amounts = [number(r[8], nonnegative=True) * 10000 for r in ordered[-20:]]
            prices = [number(r[2], positive=True) for r in ordered]
            for observation in ordered:
                opening, closing, high, low = [
                    number(observation[j], positive=True) for j in (1, 2, 3, 4)
                ]
                if not low <= min(opening, closing) <= max(opening, closing) <= high:
                    raise ValueError("candidate price OHLC conflict")
            row["metrics"].update(
                median_20_turnover=str(median(amounts)), return_20=str(prices[-1] / prices[0] - 1)
            )
            row["liquidity_ready"] = all(a > 0 for a in amounts)
            if amounts[-1] == 0:
                row["excluded"] = True
                row["gaps"].append("末日无成交，不能判为当前可观察交易候选")
            row["daily_ref"] = daily[0]["blob_ref"]
        except (ValueError, KeyError, IndexError, InvalidOperation):
            row["liquidity_ready"] = False
            row["gaps"].append("共同二十交易日行情覆盖不足")
        for role, fields in (
            ("income", ("TOTAL_OPERATE_INCOME", "PARENT_NETPROFIT")),
            ("cash", ("NETCASH_OPERATE",)),
            ("balance", ("TOTAL_ASSETS", "TOTAL_LIABILITIES")),
        ):
            source_packet = entries.get(role + ":" + sid) if is_tushare else entries.get(role)
            candidates = (
                (source_packet[1].get("result") or {}).get("data") or [] if source_packet else []
            )
            matches = [
                (i, r)
                for i, r in enumerate(candidates)
                if r.get("SECUCODE") == sid
                and str(r.get("REPORT_DATE", ""))[:10] == snapshot["period"]
            ]
            if snapshot["version"] == tushare_candidates.VERSION:
                matches = tushare_candidates.select_revision(matches, snapshot["cutoff_at"])
            if (
                len(matches) != 1
                or str(matches[0][1].get("NOTICE_DATE", ""))[:10] > snapshot["cutoff_at"][:10]
            ):
                row["gaps"].append(role + "同期间财务缺项或版本冲突")
                continue
            i, financial = matches[0]
            for field in fields:
                try:
                    value = number(financial[field])
                    row["metrics"][field] = str(value)
                    row["evidence_ids"].append(
                        numeric(
                            source_packet[0],
                            sid,
                            field,
                            value,
                            f"/result/data/{i}/{field}",
                            field + "（供应商报表元）",
                            snapshot["period"],
                        )
                    )
                except (ValueError, KeyError, InvalidOperation):
                    row["gaps"].append(field + "缺项")
        row["core_complete"] = all(
            f in row["metrics"]
            for f in (
                "TOTAL_OPERATE_INCOME",
                "PARENT_NETPROFIT",
                "NETCASH_OPERATE",
                "TOTAL_ASSETS",
                "TOTAL_LIABILITIES",
            )
        )
        row["complete_core_dimensions"] = sum(
            f in row["metrics"]
            for f in (
                "TOTAL_OPERATE_INCOME",
                "PARENT_NETPROFIT",
                "NETCASH_OPERATE",
                "TOTAL_ASSETS",
                "TOTAL_LIABILITIES",
            )
        )
        row["excluded"] |= bool(re.search(r"ST|退", row["name"], re.I))
        if re.search(r"ST|退", row["name"], re.I):
            row["gaps"].append("当前名称含风险标识，单列排除；非完整ST历史核查")
        docs = [d for d in snapshot["documents"] if d["subject_id"] == sid]
        materialized = []
        for doc in docs:
            repo.raw_bytes(doc["raw_ref"])
            pages = verify_document(doc, repo.value(doc["text_ref"]))
            materialized.append(dict(doc, pages=pages))
        originals = retrieve(
            materialized,
            business["query"]
            if business
            else "主营业务 公司设备 刻蚀 薄膜 沉积 清洗 测试 现金流 风险",
            datetime.fromisoformat(snapshot["cutoff_at"]),
            limit=2,
        )
        evidence.extend(e.model_dump(mode="json") for e in originals)
        row["business_evidence_ids"] = [
            e.evidence_id
            for e in originals
            if re.search(business["original_pattern"] if business else PRODUCTS, e.text, re.I)
            and re.search(business["required_word"] if business else "设备", e.text)
        ]
        row["business_verified"] = bool(row["business_evidence_ids"])
        subjects = [s for s in snapshot["subjects"] if s["security_id"] == sid]
        if subjects:
            if len(subjects) != 1 or subjects[0]["name"] != row["name"]:
                raise ValueError("candidate resolved subject differs from member")
            profiles = []
            if is_tushare:
                source = entries["profile:" + code][0]
                values = tushare_candidates.json_packet(repo, source)
                from .tushare import records as ts_records

                profiles = [
                    dict(
                        SECURITY_NAME_ABBR=row["name"],
                        ORG_PROFILE=" ".join(
                            str(r.get(k) or "")
                            for k in ("main_business", "introduction", "business_scope")
                        ),
                    )
                    for r in ts_records(values)
                    if r["ts_code"] == sid
                ]
            for source in snapshot.get("sources", []):
                if source["source_family"] == "EASTMONEY_PROFILE":
                    profile = checked(repo, source, snapshot["cutoff_at"])
                    profiles.extend(
                        r for r in profile.get("jbzl", []) if r["SECURITY_CODE"] == code
                    )
            if len(profiles) != 1 or profiles[0]["SECURITY_NAME_ABBR"] != row["name"]:
                raise ValueError("candidate profile original identity missing")
            description = str(profiles[0].get("ORG_PROFILE") or "")
            if row["business_verified"]:
                front = bool(re.search(r"刻蚀|薄膜|沉积|清洗|抛光|MOCVD|CMP", description, re.I))
                test = bool(re.search(r"测试|检测|量测|计量|分选", description))
                row["business_group"] = (
                    "EQUIPMENT_MIXED"
                    if front and test
                    else "EQUIPMENT_FRONT_END"
                    if front
                    else "EQUIPMENT_TEST"
                    if test
                    else "EQUIPMENT_OTHER"
                )
                if business and business["role"] == "MANUFACTURING":
                    row["business_group"] = (
                        "MANUFACTURING_FOUNDRY"
                        if re.search(r"晶圆代工|晶圓代工|代工", description)
                        else "MANUFACTURING_OTHER"
                    )
        elif row["business_verified"]:
            raise ValueError("candidate business original lacks resolved issuer identity")
        funding = entries.get("margin:" + code)
        row["funding_change"] = None
        if funding:
            values = (funding[1].get("result") or {}).get("data") or []
            by_date = {r["DATE"][:10]: r for r in values if str(r.get("SCODE")) == code}
            if set(snapshot["sessions"]) <= set(by_date):
                first, last = (
                    by_date[d]["RZYE"] for d in (snapshot["sessions"][0], snapshot["sessions"][-1])
                )
                row["funding_change"] = str(
                    number(last, nonnegative=True) - number(first, nonnegative=True)
                )
                row["funding_ref"] = funding[0]["blob_ref"]
        operating = (
            row["core_complete"]
            and number(row["metrics"]["PARENT_NETPROFIT"]) > 0
            and number(row["metrics"]["NETCASH_OPERATE"]) > 0
        )
        row["group"] = (
            ("经营与资金相互支持" if number(row["funding_change"]) >= 0 else "经营有支持但资金分歧")
            if operating and row["funding_change"] is not None
            else "资料不足"
            if operating
            else "资金活跃但经营未证实"
            if row["liquidity_ready"]
            else "资料不足"
        )
        row["eligible"] = (
            row["core_complete"]
            and row["liquidity_ready"]
            and row["business_verified"]
            and not row["excluded"]
        )
        row["leader_type"] = "UNKNOWN"
        if not row["business_verified"]:
            row["gaps"].append(
                "本轮未取得通过核查的公司业务原文；未深研不表示经营差"
                if business
                else "本轮未取得通过核查的公司设备业务原文；未深研不表示经营差"
            )
        result.append(row)
    result.sort(
        key=lambda r: (
            r["business_group"],
            -r["complete_core_dimensions"],
            -Decimal(r["metrics"].get("median_20_turnover", "0")),
            r["security_id"],
        )
    )
    largest = sorted(
        result, key=lambda r: (-Decimal(r["metrics"].get("market_cap", "0")), r["security_id"])
    )[:3]
    for r in largest:
        if "market_cap" in r["metrics"]:
            r["leader_type"] = "LARGE_REPRESENTATIVE"
    selected = [r["security_id"] for r in result if r["eligible"]][: snapshot["depth_limit"]]
    coverage = dict(
        universe_total=len(result),
        attempted=len(result),
        available=len(selected),
        excluded=sum(r["excluded"] for r in result),
        missing=len(result) - len(selected) - sum(r["excluded"] for r in result),
        universe_ref=digest(universe),
        member_refs=universe["source_refs"],
        source_dates=[snapshot["cutoff_at"]],
        method="candidate-v1:业务可比组、核心维度完整度、共同二十日成交额中位数、证券ID",
        status="PARTIAL",
    )
    sources = snapshot.get("sources") or [e["source"] for e in snapshot["entries"]]
    bundle = EvidenceBundle(
        items=tuple(evidence),
        sources=tuple(source_capability(s) for s in sources),
        coverage=coverage,
        cutoff_at=snapshot["cutoff_at"],
    ).model_dump(mode="json")
    return dict(
        universe=universe,
        members=result,
        selected=selected,
        coverage=coverage,
        bundle=bundle,
        documents=copy.deepcopy(snapshot["documents"]),
        sources=copy.deepcopy(sources),
        limitations=limitations,
        **(
            {
                "data_supply": dict(
                    data_source="Tushare",
                    classification="SW2021",
                    excluded_market_members=snapshot["excluded_market_members"],
                    data_date=snapshot["sessions"][-1],
                )
            }
            if is_tushare
            else {}
        ),
    )
