"""Bounded original industry inputs. All displayed material is rebuilt from CAS bytes."""

import copy
import json
import re
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from marketpulse.infrastructure.storage.models import BlobRef

from ..contracts import digest
from ..live.sources import KLINE
from . import attribution, industry
from .contracts import EvidenceBundle, EvidenceItem, Locator, SourceCapability
from .disclosures import QUERY, REGISTRY, original_url, select_announcements
from .evidence import parse_pdf, retrieve, verify_document
from .flow_sources import window_spec
from .tools import SourceUnavailable, Stopped

METHOD = "industry-originals-2026-10-13-v1"
MANUFACTURING_PROTOCOL = "manufacturing-originals-2026-10-14-v2"
INDEXES = {
    "WSTS": "https://www.wsts.org/76/PRESS-ARCHIVE",
    "SEMI": "https://www.semi.org/en/taxonomy/term/45726",
}


class HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.links = []
        self.anchor = None
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.skip += 1
        if tag == "a":
            self.anchor = [dict(attrs).get("href", ""), ""]

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.skip = max(0, self.skip - 1)
        if tag == "a" and self.anchor:
            self.links.append(tuple(self.anchor))
            self.anchor = None

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)
            if self.anchor:
                self.anchor[1] += data

    @property
    def text(self):
        return re.sub(r"\s+", " ", " ".join(self.parts)).strip()


def html_text(raw):
    parser = HTMLText()
    parser.feed(raw.decode("utf8"))
    return parser


def article_links(raw, base, family):
    parser = html_text(raw)
    selected = []
    for href, title in parser.links:
        if not re.search(
            r"Global Semiconductor Market"
            if family == "WSTS"
            else r"Global Semiconductor Equipment Billings",
            title,
            re.I,
        ):
            continue
        url = urljoin(base, href)
        if urlsplit(url).hostname != urlsplit(base).hostname or urlsplit(url).scheme != "https":
            continue
        if url not in [u for u, t in selected]:
            selected.append((url, title.strip()))
    return selected[: 2 if family == "WSTS" else 1]


def catalog_params(subject, issuer, start, end, page=1, category=""):
    return dict(
        pageNum=str(page),
        pageSize="30",
        column="szse",
        tabName="fulltext",
        plate="",
        stock=f"{subject['code']},{issuer['orgId']}",
        searchkey="",
        secid="",
        category=category,
        trade="",
        seDate=f"{start}~{end}",
        sortName="time",
        sortType="desc",
        isHLtitle="false",
    )


def collect(repo, client, subject, calendar):
    snapshot = dict(
        business_protocol=MANUFACTURING_PROTOCOL,
        subject=subject,
        started_at=datetime.now(UTC).isoformat(),
        responses=[],
        manufacturer_document=None,
        coverage=[],
        gaps=[],
    )

    def fetch(role, url, **kwargs):
        source, value = client.fetch("industry_" + role, url, **kwargs)
        snapshot["responses"].append(dict(role=role, source=source))
        return source, value

    for family, url in INDEXES.items():
        try:
            _, raw = fetch(family + "_index", url, family=family, binary=True)
            links = article_links(raw, url, family)
            if not links:
                raise ValueError("原始目录未定位相关行业发布")
            for i, (link, _title) in enumerate(links):
                fetch(family + "_article_" + str(i), link, family=family, binary=True)
        except Stopped:
            raise
        except (SourceUnavailable, ValueError) as error:
            snapshot["gaps"].append(dict(channel=family, reason=str(error)))
    try:
        # Same immutable issuer registry as P1; resolve by exact code, never fuzzy name.
        _, registry = client.fetch("disclosure_issuers", REGISTRY, family="CNINFO")
        issuers = registry.get("stockList", [])
        spec = window_spec(calendar, datetime.now(UTC).isoformat())
        start = str(spec.sessions[0]) if spec.sessions else str(spec.end_date - timedelta(days=100))
        issuer = next(r for r in issuers if r["code"] == subject["code"])
        for page in (1, 2):
            _, packet = fetch(
                "events_" + str(page),
                QUERY,
                family="CNINFO_EVENT",
                data=catalog_params(subject, issuer, start, str(spec.end_date), page),
            )
            rows = packet.get("announcements")
            if not isinstance(rows, list):
                raise ValueError("事件目录结构缺失")
            snapshot["coverage"].append(
                dict(
                    page=page,
                    total_reported=packet["totalAnnouncement"],
                    inspected=len(rows),
                    start=start,
                    end=str(spec.end_date),
                )
            )
            if not packet.get("hasMore") or page * 30 >= int(packet["totalAnnouncement"]):
                break
    except Stopped:
        raise
    except (SourceUnavailable, ValueError, KeyError, StopIteration) as error:
        snapshot["gaps"].append(dict(channel="CNINFO_EVENTS", reason=str(error)))
    try:
        _, registry = client.fetch("disclosure_issuers", REGISTRY, family="CNINFO")
        issuers = registry.get("stockList", [])
        spec = window_spec(calendar, datetime.now(UTC).isoformat())
        # Manufacturing is a demand-side comparison, NOT a confirmed named customer.
        manufacturers = [r for r in issuers if r["code"] == "688981"]
        if len(manufacturers) != 1 or "中芯国际" not in manufacturers[0]["zwjc"]:
            raise ValueError("制造端发行人身份不唯一")
        issuer = manufacturers[0]
        manufacturer = dict(code="688981", name="中芯国际", security_id="688981.SH")
        _, packet = fetch(
            "manufacturer_catalog",
            QUERY,
            family="CNINFO",
            data=catalog_params(
                manufacturer,
                issuer,
                str(spec.end_date - timedelta(days=250)),
                str(spec.end_date),
                category="category_bndbg_szsh;category_yjdbg_szsh;category_sjdbg_szsh;",
            ),
        )
        selected = select_announcements(
            packet.get("announcements") or [], "periodic", "季度", "688981", limit=1
        )
        if not selected:
            raise ValueError("制造端最新定期披露未取得")
        row = dict(selected[0], category="periodic")
        source, raw = fetch("manufacturer_pdf", row["original_url"], family="CNINFO", binary=True)
        snapshot["manufacturer_document"] = parse_pdf(repo, manufacturer, row, source, raw)
    except Stopped:
        raise
    except (SourceUnavailable, ValueError, KeyError, StopIteration) as error:
        snapshot["gaps"].append(dict(channel="CNINFO_DEMAND", reason=str(error)))
    try:
        spec = window_spec(calendar, datetime.now(UTC).isoformat())
        start = spec.sessions[0] if spec.sessions else spec.end_date
        fetch(
            "theme_proxy",
            KLINE,
            params={"param": f"sh512480,day,{start},{spec.end_date},640,"},
            family="TENCENT",
        )
    except Stopped:
        raise
    except (SourceUnavailable, ValueError) as error:
        snapshot["gaps"].append(dict(channel="THEME_PROXY", reason=str(error)))
    snapshot["captured_at"] = datetime.now(UTC).isoformat()
    return snapshot


def text_at(repo, item):
    raw = repo.raw_bytes(item.raw_ref)
    if item.locator.path == "html:normalized-v1":
        return html_text(raw).text
    if item.locator.path.startswith("json:"):
        value = json.loads(raw)
        for part in item.locator.path[5:].strip("/").split("/"):
            value = value[int(part)] if isinstance(value, list) else value[part]
        if not isinstance(value, str):
            raise ValueError("text locator is not a string")
        return re.sub(r"<[^>]*>", "", value)
    raise ValueError("unknown industry text locator")


def verify_item(repo, item):
    if item.method_version != METHOD or item.source_family not in {"WSTS", "SEMI", "CNINFO_EVENT"}:
        raise ValueError("unsupported text evidence method")
    if text_at(repo, item)[item.locator.start : item.locator.end] != item.text:
        raise ValueError("industry text differs from original locator")


def original_items(repo, snapshot):
    subject = snapshot["subject"]
    items = []
    events = []
    facts = []

    def item(source, eid, text, path, start=0, published=None, original=None, **kwargs):
        return EvidenceItem(
            evidence_id=eid,
            subject_id=subject["security_id"],
            kind="industry",
            source_family=source["source_family"],
            original_source_id=original or source["source_family"] + ":" + source["sha256"],
            url=source["url"],
            raw_ref=source["blob_ref"],
            content_hash=source["sha256"],
            locator=Locator(kind="text", path=path, start=start, end=start + len(text)),
            text=text,
            published_at=published,
            fetched_at=source["fetched_at"],
            available_at=source["fetched_at"],
            time_precision="day" if published else "unknown",
            method_version=METHOD,
            quality_status="PARTIAL",
            limitations=(
                "仅作行业背景或标题观察，跨地域和产品环节不直接映射公司订单。",
                "首次取得时点不证明历史可得；名义销售金额不等于出货数量。",
            ),
            **kwargs,
        ).model_dump(mode="json")

    for entry in snapshot["responses"]:
        role, source = entry["role"], entry["source"]
        raw = repo.raw_bytes(source["blob_ref"])
        if "_article_" in role:
            text = html_text(raw).text
            family = source["source_family"]
            markers = (
                ("Statistic:", "Outlook:")
                if family == "WSTS" and "Statistic:" in text
                else ("2026: Semiconductor",)
                if family == "WSTS"
                else ("MILPITAS,",)
            )
            for j, marker in enumerate(markers):
                start = text.find(marker)
                if start < 0:
                    continue
                content = text[start : start + 520]
                eid = "EV-IND-" + role + "-" + str(j)
                # Publication dates are not inferred from forecast periods or URL slugs.
                items.append(item(source, eid, content, "html:normalized-v1", start))
                facts.append(
                    dict(
                        evidence_id=eid,
                        label="芯片销售" if family == "WSTS" else "设备销售",
                        metric_scope="SEMICONDUCTOR_PRODUCT_SALES"
                        if family == "WSTS"
                        else "EQUIPMENT_BILLINGS",
                        basis="ACTUAL_STATISTICS"
                        if marker == "Statistic:" or family == "SEMI"
                        else "FORECAST_OR_ACTUAL_REPLACEMENT",
                        aggregation="发布原文中的半年/季度或年度预测；不混合月值和移动平均",
                        unit_scope="名义美元金额，非出货数量",
                        geography="全球",
                        underlying_statistics="WSTS" if family == "WSTS" else "SEMI_SEAJ_WWSEMS",
                        version=source["sha256"],
                        available_at=source["fetched_at"],
                        url=source["url"],
                    )
                )
        if role.startswith("events_"):
            packet = json.loads(raw)
            for i, row in enumerate(packet["announcements"]):
                if row["secCode"] != subject["code"]:
                    raise ValueError("event issuer mismatch")
                title = re.sub(r"<[^>]*>", "", row["announcementTitle"])
                published = datetime.fromtimestamp(row["announcementTime"] / 1000, UTC).isoformat()
                if datetime.fromisoformat(published) > datetime.fromisoformat(
                    snapshot["captured_at"]
                ):
                    continue
                eid = "EV-EVENT-" + str(row["announcementId"])
                if any(e["evidence_id"] == eid for e in items):
                    continue
                original_url(row["adjunctUrl"])
                items.append(
                    item(
                        source,
                        eid,
                        title,
                        f"json:/announcements/{i}/announcementTitle",
                        published=published,
                        original="CNINFO:" + str(row["announcementId"]),
                    )
                )
                events.append(
                    dict(
                        event_id=str(row["announcementId"]),
                        title=title,
                        published_at=published,
                        available_at=source["fetched_at"],
                        evidence_ids=[eid],
                        url=original_url(row["adjunctUrl"]),
                        expectation_basis_refs=[],
                        source_scope="CATALOG_TITLE_ONLY",
                    )
                )
    doc = snapshot.get("manufacturer_document")
    if doc:
        if doc["subject_id"] != "688981.SH":
            raise ValueError("manufacturer identity mismatch")
        pages = verify_document(doc, repo.value(doc["text_ref"]))
        selected = retrieve(
            [dict(doc, pages=pages)],
            "资本开支 资本支出 产能利用 现金流",
            datetime.fromisoformat(snapshot["captured_at"]),
            limit=2,
        )
        focused = []
        for marker in ("资本开支及资金来源", "产能利用率"):
            match = next(
                (
                    (i, t, t.find(marker))
                    for i, t in enumerate(pages, 1)
                    if marker in t and "目录" not in t
                ),
                None,
            )
            if match and selected:
                page, text, start = match
                start = max(0, start - 40)
                content = text[start : start + 400]
                focused.append(
                    selected[0].model_copy(
                        update=dict(
                            locator=Locator(
                                kind="pdf",
                                path=doc["text_ref"],
                                page=page,
                                start=start,
                                end=start + len(content),
                            ),
                            text=content,
                        )
                    )
                )
        if focused:
            selected = focused
        seen = set()
        for e in selected:
            key = (e.raw_ref, e.locator.page, e.locator.start)
            if snapshot.get("business_protocol") == MANUFACTURING_PROTOCOL and key in seen:
                continue
            seen.add(key)
            own = (
                snapshot.get("business_protocol") == MANUFACTURING_PROTOCOL
                and doc["subject_id"] == subject["security_id"]
            )
            # Evidence is relevant to the research subject but explicitly owned by manufacturer.
            value = e.model_copy(
                update=dict(
                    evidence_id="EV-IND-DEMAND-" + str(e.locator.page) + "-" + str(e.locator.start),
                    subject_id=subject["security_id"],
                    kind="disclosure" if own else "industry",
                    limitations=e.limitations
                    + (
                        ("本公司制造业务原文；利用率、资本开支及折旧口径仍需规范化核查。",)
                        if own
                        else (
                            "制造端中芯国际披露，不代表其为目标公司已核实客户，也不代表目标设备订单。",
                        )
                    ),
                )
            )
            items.append(value.model_dump(mode="json"))
    return items, events, facts


def rebuild(repo, data, snapshot):
    for entry in snapshot["responses"]:
        s = entry["source"]
        if BlobRef.from_uri(s["blob_ref"]).sha256 != s["sha256"] or datetime.fromisoformat(
            s["fetched_at"]
        ) > datetime.fromisoformat(data["bundle"]["cutoff_at"]):
            raise ValueError("industry original hash or cutoff mismatch")
        repo.raw_bytes(s["blob_ref"])
    items, events, facts = original_items(repo, snapshot)
    proxy = {}
    for entry in snapshot["responses"]:
        if entry["role"] == "theme_proxy":
            packet = json.loads(repo.raw_bytes(entry["source"]["blob_ref"]))
            for row in packet["data"]["sh512480"]["day"]:
                if row[0] in proxy:
                    raise ValueError("duplicate proxy session")
                if DecimalSafe(row[2]) <= 0:
                    raise ValueError("invalid proxy price")
                proxy[row[0]] = row[2]
    observation = attribution.build(
        data["flow_report"], data["observation_calendar"], events, proxy
    )
    target = [
        e
        for e in data["bundle"]["items"]
        if e["kind"] == "disclosure" and e["evidence_id"] in snapshot["target_evidence_ids"]
    ]
    # The issuer's PDF header contains “半导体设备” on every page; that is identity,
    # not a product-exposure observation. Require actual named product language.
    exposures = [e["evidence_id"] for e in target if re.search(r"刻蚀|薄膜沉积|MOCVD", e["text"])]
    demand = [e["evidence_id"] for e in items if e["evidence_id"].startswith("EV-IND-DEMAND")]
    role = industry.business_role(snapshot["subject"])
    equipment = (
        bool(exposures) and role == "EQUIPMENT" and "半导体" in snapshot["subject"]["industry"]
    )
    # Each link is a testable mechanism, never an asserted observed causal edge.
    chain = [
        dict(
            edge_id="EDGE-" + str(i + 1),
            from_label=a,
            to_label=b,
            status="HYPOTHESIS",
            evidence_ids=refs,
            mechanism=m,
            next_check=c,
        )
        for i, (a, b, refs, m, c) in enumerate(
            [
                (
                    "终端需求",
                    "制造端资本开支",
                    [f["evidence_id"] for f in facts] + demand,
                    "若需求转为新增产能投资，才可能增加设备采购需求。",
                    "核查地域、存储/逻辑结构及制造端实际资本支出",
                ),
                (
                    "制造端资本开支",
                    "设备导入与验收",
                    demand + exposures,
                    "若采购工艺与公司产品匹配且通过验证，才可能转为设备导入。",
                    "核查客户身份、工艺适配、订单与验收，不由全球增长直接推受益",
                ),
                (
                    "设备导入与验收",
                    "收入与利润",
                    exposures,
                    "若完成验收且满足收入确认条件，才可能体现为收入；盈利仍取决于结构与费用。",
                    "区分产品验证与量产收入，核查验收周期和利润结构",
                ),
                (
                    "收入与利润",
                    "现金回收",
                    [e["evidence_id"] for e in target if "现金" in e["text"]],
                    "若客户按约回款，收入才可能转为现金；合同负债不等于完整订单。",
                    "核查应收、账龄、回款和经营现金流",
                ),
            ]
        )
    ]
    for fact in facts:
        fact["relevance"] = (
            industry.metric_priority("EQUIPMENT", fact["metric_scope"])
            if equipment
            else "BACKGROUND_ONLY"
        )
    report = dict(
        method_version=METHOD,
        domain_policy=industry.DOMAIN_POLICY,
        business_role=role,
        template="SEMICONDUCTOR_EQUIPMENT" if equipment else "UNAVAILABLE",
        template_status="CONDITIONAL_ENABLED" if equipment and demand and facts else "LIMITED",
        target_exposure_refs=exposures,
        manufacturer="中芯国际（需求侧比较，客户关系未核定）",
        facts=facts,
        chain=chain if equipment else [],
        coverage=snapshot["coverage"],
        gaps=snapshot["gaps"],
        source_dependencies=industry.independent_statistics(facts),
        limitations=[
            "行业背景不等于本公司订单；合同负债不是完整订单，验证不等于量产收入。",
            "原预测与后续替换实际值的版本分别封存；不回填历史，也不称为同一时点新情景预测。",
            "公司指引、历史同比与市场一致预期分开；没有可靠一致预期基准，不判断超预期。",
        ],
    )
    if snapshot.get("business_protocol") == MANUFACTURING_PROTOCOL and role == "MANUFACTURING":
        own_refs = [e["evidence_id"] for e in items if e["kind"] == "disclosure"]
        original_refs = own_refs + [e["evidence_id"] for e in target]
        report.update(
            domain_policy=MANUFACTURING_PROTOCOL,
            template="SEMICONDUCTOR_MANUFACTURING",
            template_status="CONDITIONAL_ENABLED" if original_refs else "LIMITED",
            manufacturer="本公司晶圆制造原文（与外部行业统计分别核查）",
            chain=industry.manufacturing_chain(original_refs),
            limitations=[
                "制造端按需求、利用率、售价与折旧、资本开支和现金回收分析，不套设备订单及验收逻辑。",
                "利用率、晶圆尺寸、工艺、币种与资本开支未统一口径，不由行业增长推断公司满产或超预期。",
                "所有传导边都是待验证机制；本公司原文不能替代独立行业和竞争证据。",
            ],
        )
        for fact in report["facts"]:
            fact["relevance"] = industry.metric_priority("MANUFACTURING", fact["metric_scope"])
    return dict(items=items, industry_report=report, attribution_report=observation)


def DecimalSafe(value):
    from decimal import Decimal

    v = Decimal(str(value))
    if not v.is_finite():
        raise ValueError("non-finite price")
    return v


def attach(repo, data, snapshot):
    data = copy.deepcopy(data)
    snapshot = copy.deepcopy(snapshot)
    snapshot["target_evidence_ids"] = [
        e["evidence_id"] for e in data["bundle"]["items"] if e["kind"] == "disclosure"
    ]
    rebuilt = rebuild(repo, data, snapshot)
    ref = repo.blob(rebuilt)
    sources = [e["source"] for e in snapshot["responses"]]
    sources.append(
        dict(
            key="industry_calculation",
            url=KLINE,
            source_family="DECISION_ATTRIBUTION_CALC",
            blob_ref=ref,
            sha256=BlobRef.from_uri(ref).sha256,
            fetched_at=data["bundle"]["cutoff_at"],
        )
    )
    data["sources"] = list({s["blob_ref"]: s for s in data["sources"] + sources}.values())
    families = {s["source_family"] for s in data["bundle"]["sources"]}
    for source in sources:
        family = source["source_family"]
        if family in families:
            continue
        data["bundle"]["sources"].append(
            SourceCapability(
                source_id="industry-" + family,
                name=family + " 本次行业与事件资料",
                url=source["url"],
                source_family=family,
                acquisition="有界公开原始响应封存",
                authentication="公开访问",
                license_note="保持原来源限制，不扩大再分发许可",
                coverage="有限发布或同窗目录，不代表完整资料库",
                frequency="每次研究",
                status="PARTIAL",
                reason="首次取数，不证明历史可得；连接与因果仍受限制",
                raw_ref=source["blob_ref"],
                content_hash=source["sha256"],
                tested_at=source["fetched_at"],
            ).model_dump(mode="json")
        )
        families.add(family)
    data["bundle"]["items"] += rebuilt["items"] + calculation_items(data, rebuilt, ref, snapshot)
    data["bundle"] = EvidenceBundle.model_validate(data["bundle"]).model_dump(mode="json")
    data.update(
        evidence=data["bundle"]["items"],
        industry_snapshot=snapshot,
        industry_calculation_ref=ref,
        industry_documents=[snapshot["manufacturer_document"]]
        if snapshot.get("manufacturer_document")
        else [],
        industry_report=rebuilt["industry_report"],
        attribution_report=rebuilt["attribution_report"],
    )
    return data


def verify(repo, data):
    rebuilt = rebuild(repo, data, data["industry_snapshot"])
    if digest(rebuilt) != digest(repo.value(data["industry_calculation_ref"])) or any(
        data[k] != rebuilt[k] for k in ("industry_report", "attribution_report")
    ):
        raise ValueError("industry observations differ from sealed originals")
    actual = {e["evidence_id"]: e for e in data["bundle"]["items"]}
    if any(actual.get(e["evidence_id"]) != e for e in rebuilt["items"]):
        raise ValueError("industry evidence was changed")
    calculated = calculation_items(
        data, rebuilt, data["industry_calculation_ref"], data["industry_snapshot"]
    )
    if any(actual.get(e["evidence_id"]) != e for e in calculated):
        raise ValueError("attribution evidence was changed")
    expected = (
        [data["industry_snapshot"]["manufacturer_document"]]
        if data["industry_snapshot"].get("manufacturer_document")
        else []
    )
    if data.get("industry_documents") != expected:
        raise ValueError("manufacturer documents differ")


def calculation_items(data, rebuilt, ref, snapshot):
    result = []
    labels = {
        "security_return": "个股同期涨跌",
        "market_return": "沪深三百同期涨跌",
        "proxy_return": "半导体主题ETF代理同期涨跌",
        "relative_market": "个股减市场的观察差",
        "relative_proxy": "个股减主题代理的观察差",
    }
    for wi, w in enumerate(rebuilt["attribution_report"]["windows"]):
        for key, label in labels.items():
            if w[key] is None:
                continue
            result.append(
                EvidenceItem(
                    evidence_id=f"EV-ATTR-{w['window']}-{key}",
                    subject_id=snapshot["subject"]["security_id"],
                    kind="industry",
                    source_family="DECISION_ATTRIBUTION_CALC",
                    original_source_id="CALC:" + attribution.METHOD,
                    url=KLINE,
                    raw_ref=ref,
                    content_hash=BlobRef.from_uri(ref).sha256,
                    locator=Locator(kind="json", path=f"/attribution_report/windows/{wi}/{key}"),
                    text=(
                        f"近{w['window']}交易日{label}：{w[key]} ratio；"
                        f"基准日{w['baseline_date']}。这是观察，不是事件贡献。"
                    ),
                    value=w[key],
                    unit="ratio",
                    period_start=w["sessions"][0],
                    period_end=w["sessions"][-1],
                    fetched_at=data["bundle"]["cutoff_at"],
                    available_at=data["bundle"]["cutoff_at"],
                    time_precision="day",
                    method_version=attribution.METHOD,
                    quality_status="PARTIAL",
                    limitations=attribution.LIMITS,
                ).model_dump(mode="json")
            )
    return result


def model_context(data, evidence_ids=None):
    original = data["industry_report"]
    report = {
        k: original[k]
        for k in (
            "template",
            "template_status",
            "manufacturer",
            "source_dependencies",
        )
    }
    report["facts"] = [
        {
            k: f[k]
            for k in (
                "evidence_id",
                "metric_scope",
                "basis",
                "unit_scope",
                "underlying_statistics",
                "relevance",
            )
        }
        for f in original["facts"]
    ]
    report["chain"] = [
        {k: edge[k] for k in ("edge_id", "from_label", "to_label", "status", "evidence_ids")}
        for edge in original["chain"]
    ]
    report["gaps"] = [g["channel"] for g in original["gaps"]]
    if evidence_ids is not None:
        for edge in report["chain"]:
            edge["evidence_ids"] = [i for i in edge["evidence_ids"] if i in evidence_ids][:2]
    return dict(
        industry=report,
        price_windows=[
            {
                k: v
                for k, v in w.items()
                if k
                not in {
                    "events",
                    "sessions",
                    "limitations",
                    "alternative_explanations",
                    "identification",
                }
            }
            for w in data["attribution_report"]["windows"]
        ],
        recent_events=[
            {
                k: e[k]
                for k in ("event_id", "title", "effective_session", "precision", "source_scope")
            }
            for e in data["attribution_report"]["timeline"][:8]
        ],
        causal_identification="NOT_IDENTIFIED",
        price_limits=(
            "RAW_CLOSE; ex-dividend not verified; theme ETF is a cross-segment proxy; "
            "catalog titles only; no causal identification"
        ),
    )
