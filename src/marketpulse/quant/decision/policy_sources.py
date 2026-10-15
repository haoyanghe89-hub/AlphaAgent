"""Bounded policy originals; metadata and excerpts are rebuilt from sealed HTML bytes."""

import copy
import hashlib
import re
from datetime import UTC, date, datetime
from functools import lru_cache
from html.parser import HTMLParser

from marketpulse.infrastructure.storage.models import BlobRef

from .contracts import EvidenceBundle, EvidenceItem, Locator, SourceCapability
from .tools import SourceUnavailable, Stopped

METHOD = "policy-originals-2026-10-13-v1"
SOURCES = {
    "CN-STATE-2020-8": dict(
        url="https://www.gov.cn/zhengce/content/2020-08/04/content_5532370.htm",
        family="POLICY_CN",
        jurisdiction="CN",
        selectors={"body": "UCAP-CONTENT"},
    ),
    "US-EAR-74423": dict(
        url="https://www.bis.gov/regulations/ear/744",
        family="POLICY_US",
        jurisdiction="US",
        selectors={
            "body": "744.23",
            "advanced": "p-744.23(a)(2)",
            "equipment": "p-744.23(a)(4)",
        },
    ),
}
LIMITS = [
    "定向摘录仅供研究背景，不是完整政策库或法律适用结论；还需核查后续修订及配套细则。",
    "首次取得时点不证明历史可得；旧文件不证明公司当前已获补贴、税收资格或已被禁运。",
    "公司资格、产品物项、最终用户、地域和许可适用均未核定，政策至公司经营的连接仅是假设。",
]


def normalized(text):
    return re.sub(r"\s+", " ", text).strip()


class PolicyHTML(HTMLParser):
    """Capture only fixed content nodes; scripts and navigation never enter clauses."""

    def __init__(self, selectors):
        super().__init__(convert_charrefs=True)
        self.selectors = selectors
        self.stack = []
        self.parts = {key: [] for key in (*selectors, "meta", "title")}
        self.header = []
        self.header_size = 0

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        keys = [key for key, value in self.selectors.items() if values.get("id") == value]
        classes = values.get("class", "").split()
        if "border-table" in classes and "pages_content" not in classes:
            keys.append("meta")
        if tag == "title":
            keys.append("title")
        blocked = tag in {"script", "style", "nav", "footer", "noscript"}
        if tag not in {"br", "hr", "img", "input", "meta", "link", "source", "wbr"}:
            self.stack.append((tag, keys, blocked))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, text):
        if any(blocked for _, _, blocked in self.stack):
            return
        if self.header_size < 12000:
            self.header.append(text)
            self.header_size += len(text)
        for _, keys, _ in self.stack:
            for key in keys:
                self.parts[key].append(text)

    def texts(self):
        return {key: normalized(" ".join(parts)) for key, parts in self.parts.items()}


@lru_cache(maxsize=2)
def _html_texts(raw, policy_id):
    # Cache only pure extraction from exact original bytes (at most two source
    # documents). Every verification still rereads and hashes the original.
    parser = PolicyHTML(SOURCES[policy_id]["selectors"])
    parser.feed(raw.decode("utf-8-sig"))
    text = parser.texts()
    header = normalized(" ".join(parser.header))
    version = re.search(
        r"Current from the eCFR as of ([A-Za-z]+ \d{1,2}, \d{4}, [^<]{1,35}? EDT)",
        header,
    )
    text["version"] = version[0] if version else ""
    return tuple(text.items())


def html_texts(raw, policy_id):
    # Callers may derive metadata; never expose the cached mapping for mutation.
    return dict(_html_texts(raw, policy_id))


def field(meta, label, following):
    pattern = label + r"\s*[：:]\s*(.*?)\s*(?=" + following + r"\s*[：:]|$)"
    match = re.search(pattern, meta)
    return match[1].strip() if match else None


def cn_date(text):
    match = re.search(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", text or "")
    return date(*(int(part) for part in match.groups())).isoformat() if match else None


def document_state(title, body, meta, fetched_at):
    # Only an explicit statement about this document can override unknown current validity.
    current = "UNKNOWN"
    effective = None
    expires = None
    match = re.search(r"本(?:文件|政策)自(\d{4}年\d{1,2}月\d{1,2}日)(?:起)?(?:实施|施行)", body)
    if match:
        effective = cn_date(match[1])
    match = re.search(r"本(?:文件|政策)有效期至(\d{4}年\d{1,2}月\d{1,2}日)", body)
    if match:
        expires = cn_date(match[1])
    if re.search(r"(?:本(?:文件|政策)|本文)(?:现已|已)?(?:废止|失效)", meta + " " + body):
        status, current = "REPEALED", "REPEALED"
    elif "征求意见稿" in title or "公开征求意见" in body[:500]:
        status = "CONSULTATION"
    elif effective and date.fromisoformat(effective) > datetime.fromisoformat(fetched_at).date():
        status, current = "NOT_YET_EFFECTIVE", "NOT_YET_EFFECTIVE"
    elif "尚未生效" in body[:500]:
        status, current = "NOT_YET_EFFECTIVE", "NOT_YET_EFFECTIVE"
    elif "此件公开发布" in body and re.search(r"现将.{1,120}印发给你们", body):
        status = "FORMAL"
    else:
        status = "UNKNOWN"
    return status, current, effective, expires


def parse_policy(raw, policy_id, fetched_at):
    texts = html_texts(raw, policy_id)
    body, meta = texts["body"], texts["meta"]
    if policy_id == "CN-STATE-2020-8":
        title = field(meta, r"标\s*题", "发文字号")
        issuer = field(meta, "发文机关", "成文日期")
        number = field(meta, "发文字号", "发布日期")
        published = cn_date(field(meta, "发布日期", r"(?:无后续字段)"))
        if (
            issuer != "国务院"
            or number != "国发〔2020〕8号"
            or not title
            or number not in body
            or "集成电路" not in body
            or len(body) < 600
        ):
            raise ValueError("未定位可核对题录与政策正文；门户或导航不作为政策条款")
        status, validity, effective, expires = document_state(title, body, meta, fetched_at)
        kind = "CONSULTATION" if status == "CONSULTATION" else "FORMAL_POLICY"
        scope = "集成电路及软件产业；所摘条款含设计、装备、材料等条件与适用主体，资格需另核。"
        version = None
        selected = [
            ("body", "（二）", "财税条件（第二项起节选）"),
            ("body", "（三十八）", "适用主体与实施说明（节选，含第三十八项）"),
        ]
    else:
        heading = re.match(r"§\s*744\.23\s+(.{1,240}?controls\.)", body)
        if (
            not heading
            or "Bureau of Industry and Security" not in texts["title"]
            or "General prohibition." not in body
            or "subject to the EAR" not in body
            or not texts["advanced"]
            or not texts["equipment"]
        ):
            raise ValueError("未定位EAR §744.23及半导体条款；导航入口不作为法规正文")
        title = heading[0]
        issuer = "Bureau of Industry and Security"
        number, published, effective, expires = "§ 744.23", None, None, None
        kind, status, validity = "REGULATORY_COMPILATION", "UNKNOWN", "UNKNOWN"
        scope = "仅所取得的先进节点集成电路、半导体制造设备终端用途条款；物项与许可适用待核。"
        version_match = re.search(r"as of ([A-Za-z]+ \d{1,2}, \d{4})", texts["version"])
        version = (
            datetime.strptime(version_match[1], "%B %d, %Y").date().isoformat()
            if version_match
            else None
        )
        # This website compilation is a dated source version, not proof of legal applicability.
        texts["meta"] = texts["title"] + " " + title
        selected = [
            ("advanced", "", "§744.23(a)(2) 先进节点集成电路（节选）"),
            ("equipment", "", "§744.23(a)(4) 半导体制造设备（节选）"),
        ]
    excerpts = []
    for key, marker, label in selected:
        text = texts[key]
        start = text.find(marker) if marker else 0
        if start < 0:
            raise ValueError("政策相关条款未定位")
        end = min(len(text), start + 500)
        if end - start < 200:
            start = max(0, end - 200)
        if end - start < 200:
            raise ValueError("政策条款不足以支持限定范围的摘录")
        excerpts.append(dict(key=key, start=start, end=end, label=label))
    return dict(
        policy_id=policy_id,
        title=title,
        jurisdiction=SOURCES[policy_id]["jurisdiction"],
        issuer=issuer,
        document_number=number,
        document_kind=kind,
        document_status=status,
        published_at=published,
        effective_at=effective,
        expires_at=expires,
        current_validity=validity,
        version_as_of=version,
        amendment_status="UNKNOWN",
        qualification_status="UNKNOWN",
        company_link_status="HYPOTHESIS",
        scope=scope,
        excerpts=excerpts,
    ), texts


def semiconductor_subject(subject):
    if re.search(r"半导体|集成电路", subject.get("industry", "")):
        return True
    description = subject.get("description", "")
    return not re.search(r"不从事|非半导体", description) and bool(
        re.search(
            r"(?:从事|专注|主营|主要产品|研发|制造|生产).{0,35}(?:半导体|集成电路|晶圆|刻蚀|MOCVD)",
            description,
            re.I,
        )
    )


def selected_policies(subject):
    semi = semiconductor_subject(subject)
    return (["CN-STATE-2020-8"] if semi or "软件" in subject.get("industry", "") else []) + (
        ["US-EAR-74423"] if semi else []
    )


def collect(repo, client, subject):
    snapshot = dict(
        method_version=METHOD,
        subject=copy.deepcopy(subject),
        target_subject_id=subject["security_id"],
        started_at=datetime.now(UTC).isoformat(),
        entries=[],
        coverage=[],
        gaps=[],
    )
    plans = selected_policies(subject)
    for policy_id in plans:
        spec = SOURCES[policy_id]
        entry = None
        try:
            source, raw = client.fetch(
                "policy_" + policy_id,
                spec["url"],
                family=spec["family"],
                binary=True,
            )
            entry = dict(policy_id=policy_id, source=source, parsedpolicy=None)
            snapshot["entries"].append(entry)
            entry["parsedpolicy"], _ = parse_policy(raw, policy_id, source["fetched_at"])
        except Stopped:
            raise
        except (SourceUnavailable, ValueError, UnicodeError) as error:
            snapshot["gaps"].append(
                dict(
                    channel=spec["family"],
                    policy_id=policy_id,
                    reason="官方原文未取得或正文未能核对",
                    detail=str(error),
                )
            )
        snapshot["coverage"].append(
            dict(
                channel=spec["family"],
                attempted=1,
                available=int(bool(entry and entry["parsedpolicy"])),
                complete=False,
                scope="固定官方文件的定向条款，不代表完整政策或修订覆盖",
            )
        )
    if not plans:
        snapshot["gaps"].append(
            dict(channel="POLICY", reason="主体行业未匹配本次半导体及软件政策范围")
        )
    snapshot["captured_at"] = datetime.now(UTC).isoformat()
    return snapshot


def checked_source(repo, source, policy_id, cutoff=None):
    spec = SOURCES[policy_id]
    if source["source_family"] != spec["family"] or source["url"] != spec["url"]:
        raise ValueError("policy source identity mismatch")
    raw = repo.raw_bytes(source["blob_ref"])
    if (
        BlobRef.from_uri(source["blob_ref"]).sha256 != source["sha256"]
        or hashlib.sha256(raw).hexdigest() != source["sha256"]
    ):
        raise ValueError("policy original hash mismatch")
    if cutoff and datetime.fromisoformat(source["fetched_at"]) > datetime.fromisoformat(cutoff):
        raise ValueError("policy source exceeds evidence cutoff")
    return raw


def rebuild(repo, data, snapshot):
    target = snapshot["target_subject_id"]
    if snapshot["method_version"] != METHOD or snapshot["subject"]["security_id"] != target:
        raise ValueError("policy subject or method mismatch")
    owners = {
        e["subject_id"]
        for e in data["bundle"]["items"]
        if not e["evidence_id"].startswith("EV-POL-")
    }
    owners.update(doc["subject_id"] for doc in data.get("documents", []))
    if owners and owners != {target}:
        raise ValueError("policy subject differs from research subject")
    plans = selected_policies(snapshot["subject"])
    items, policies = [], []
    seen = set()
    for entry in snapshot["entries"]:
        policy_id, source = entry["policy_id"], entry["source"]
        if policy_id not in plans or policy_id in seen:
            raise ValueError("unexpected or duplicate policy source")
        seen.add(policy_id)
        raw = checked_source(repo, source, policy_id, data["bundle"]["cutoff_at"])
        try:
            parsed, texts = parse_policy(raw, policy_id, source["fetched_at"])
        except (ValueError, UnicodeError):
            if entry["parsedpolicy"] is not None:
                raise ValueError("policy metadata differs from original") from None
            continue
        if entry["parsedpolicy"] != parsed:
            raise ValueError("policy metadata differs from original")
        policy = {key: value for key, value in parsed.items() if key != "excerpts"}
        policy.update(clause_refs=[], evidence_ids=[], limitations=LIMITS.copy())
        spans = [dict(key="meta", start=0, end=len(texts["meta"]), label="原文题录")]
        if policy_id == "CN-STATE-2020-8":
            spans.append(
                dict(
                    key="body",
                    start=0,
                    end=min(500, len(texts["body"])),
                    label="原文文件性质及发布说明",
                )
            )
        if texts["version"]:
            spans.append(
                dict(key="version", start=0, end=len(texts["version"]), label="页面版本说明")
            )
        spans += parsed["excerpts"]
        for index, span in enumerate(spans):
            eid = f"EV-POL-{policy_id}-{index}"
            text = texts[span["key"]][span["start"] : span["end"]]
            value = EvidenceItem(
                evidence_id=eid,
                subject_id=target,
                kind="industry",
                source_family=source["source_family"],
                original_source_id="POLICY:" + policy_id,
                url=source["url"],
                raw_ref=source["blob_ref"],
                content_hash=source["sha256"],
                locator=Locator(
                    kind="text",
                    path=f"policy-html-v1:{policy_id}:{span['key']}",
                    start=span["start"],
                    end=span["end"],
                ),
                text=text,
                published_at=parsed["published_at"] + "T00:00:00+00:00"
                if parsed["published_at"]
                else None,
                fetched_at=source["fetched_at"],
                available_at=source["fetched_at"],
                availability_basis="FIRST_FETCH",
                time_precision="day" if parsed["published_at"] else "unknown",
                method_version=METHOD,
                quality_status="PARTIAL",
                limitations=tuple(LIMITS),
            ).model_dump(mode="json")
            items.append(value)
            policy["evidence_ids"].append(eid)
            policy["clause_refs"].append(
                dict(clause_id=span["key"], label=span["label"], evidence_id=eid)
            )
        policies.append(policy)
    report = dict(
        method_version=METHOD,
        target_subject_id=target,
        status="PARTIAL" if policies else "UNAVAILABLE" if plans else "NOT_APPLICABLE",
        policies=policies,
        coverage=snapshot["coverage"],
        gaps=snapshot["gaps"],
        limitations=LIMITS.copy(),
    )
    return dict(items=items, report=report)


def attach(repo, data, snapshot):
    data, snapshot = copy.deepcopy(data), copy.deepcopy(snapshot)
    rebuilt = rebuild(repo, data, snapshot)
    snapshot["sealed_ref"] = repo.blob(snapshot)
    sources = [entry["source"] for entry in snapshot["entries"]]
    data["sources"] = list(
        {(s["blob_ref"], s["key"]): s for s in data["sources"] + sources}.values()
    )
    known = {s["source_family"] for s in data["bundle"]["sources"]}
    for source in sources:
        if source["source_family"] in known:
            continue
        data["bundle"]["sources"].append(
            SourceCapability(
                source_id="policy-" + source["source_family"],
                name="官方政策原文定向取证",
                url=source["url"],
                source_family=source["source_family"],
                acquisition="固定官方文件原文封存",
                authentication="公开访问",
                license_note="保持原来源限制，不扩大再分发许可",
                coverage="有限条款与页面版本，未核定修订完整性和公司资格",
                frequency="每次研究",
                status="PARTIAL",
                reason="只支持原文与范围核查，不认定当前资格或法律适用",
                raw_ref=source["blob_ref"],
                content_hash=source["sha256"],
                tested_at=source["fetched_at"],
            ).model_dump(mode="json")
        )
        known.add(source["source_family"])
    data["bundle"]["items"] += rebuilt["items"]
    data["bundle"] = EvidenceBundle.model_validate(data["bundle"]).model_dump(mode="json")
    data.update(
        evidence=data["bundle"]["items"], policy_snapshot=snapshot, policy_report=rebuilt["report"]
    )
    return data


def verify_item(repo, item):
    if item.method_version != METHOD or item.locator.kind != "text":
        raise ValueError("unsupported policy evidence method")
    parts = item.locator.path.split(":")
    if len(parts) != 3 or parts[0] != "policy-html-v1" or parts[1] not in SOURCES:
        raise ValueError("unsupported policy text locator")
    policy_id, key = parts[1:]
    spec = SOURCES[policy_id]
    if (
        item.source_family != spec["family"]
        or str(item.url) != spec["url"]
        or item.original_source_id != "POLICY:" + policy_id
    ):
        raise ValueError("policy evidence source identity mismatch")
    raw = repo.raw_bytes(item.raw_ref)
    if (
        BlobRef.from_uri(item.raw_ref).sha256 != item.content_hash
        or hashlib.sha256(raw).hexdigest() != item.content_hash
    ):
        raise ValueError("policy evidence hash mismatch")
    _, texts = parse_policy(raw, policy_id, item.fetched_at.isoformat())
    if key not in texts or texts[key][item.locator.start : item.locator.end] != item.text:
        raise ValueError("policy text differs from original locator")
    if item.availability_basis != "FIRST_FETCH" or item.available_at != item.fetched_at:
        raise ValueError("policy original cannot backdate first-fetch availability")


def verify(repo, data):
    snapshot = copy.deepcopy(data["policy_snapshot"])
    sealed_ref = snapshot.pop("sealed_ref")
    if snapshot != repo.value(sealed_ref):
        raise ValueError("policy snapshot differs from sealed originals")
    rebuilt = rebuild(repo, data, snapshot)
    if data["policy_report"] != rebuilt["report"]:
        raise ValueError("policy report differs from sealed originals")
    actual = [e for e in data["bundle"]["items"] if e["evidence_id"].startswith("EV-POL-")]
    if actual != rebuilt["items"]:
        raise ValueError("policy evidence was changed")
    visible = [e for e in data["evidence"] if e["evidence_id"].startswith("EV-POL-")]
    if visible != rebuilt["items"]:
        raise ValueError("policy evidence views disagree")
    sources = {(s["blob_ref"], s["key"]): s for s in data["sources"]}
    if any(
        sources.get((entry["source"]["blob_ref"], entry["source"]["key"])) != entry["source"]
        for entry in snapshot["entries"]
    ):
        raise ValueError("policy source was changed")


def model_context(data):
    report = data["policy_report"]
    return dict(
        policy=dict(
            status=report["status"],
            target_subject_id=report["target_subject_id"],
            policies=[
                {
                    key: p[key]
                    for key in (
                        "policy_id",
                        "jurisdiction",
                        "document_kind",
                        "document_status",
                        "published_at",
                        "effective_at",
                        "current_validity",
                        "version_as_of",
                        "amendment_status",
                        "qualification_status",
                        "company_link_status",
                        "scope",
                        "evidence_ids",
                    )
                }
                for p in report["policies"]
            ],
            gaps=[g["channel"] for g in report["gaps"]],
            limits="资格与现行适用未知；政策至公司经营仅为待核假设；条款原文见证据。",
        )
    )
