"""Directed, typed CNINFO retrieval; bounded coverage is always disclosed."""

import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from .tools import SourceUnavailable

REGISTRY = "https://www.cninfo.com.cn/new/data/szse_stock.json"
QUERY = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CATEGORIES = ("periodic", "earnings", "investor")
LABELS = dict(
    periodic="定期报告与经营讨论", earnings="业绩预告/快报及更正", investor="投资者关系活动"
)


def classify(title):
    if re.search(r"管理办法|管理制度|工作制度", title):
        return None
    if re.search(r"投资者关系|调研|机构接待", title):
        return "investor"
    if re.search(r"业绩.*(预告|快报|更正)", title):
        return "earnings"
    if re.search(r"(年度|半年度|季度|季)报告", title) and not re.search(
        r"摘要|英文|取消|预约|董事会|监事会", title
    ):
        return "periodic"
    return None


def original_url(value):
    if re.fullmatch(r"finalpage/\d{4}-\d{2}-\d{2}/\d+\.[Pp][Dd][Ff]", value or ""):
        return "https://static.cninfo.com.cn/" + value
    parsed = urlsplit(value or "")
    if (
        parsed.scheme == "https"
        and parsed.netloc == "static.cninfo.com.cn"
        and re.fullmatch(r"/finalpage/\d{4}-\d{2}-\d{2}/\d+\.[Pp][Dd][Ff]", parsed.path)
    ):
        return value
    raise ValueError("公告原文 URL 未核定")


def select_announcements(rows, category, question, code, limit=2):
    valid = []
    for row in rows:
        if str(row.get("secCode")) != code:
            raise ValueError("公告目录主体冲突")
        title = re.sub(r"<[^>]*>", "", row.get("announcementTitle", ""))
        if classify(title) != category:
            continue
        if not re.fullmatch(r"\d+", str(row.get("announcementId", ""))):
            raise ValueError("公告标识格式不符")
        row = dict(row, announcementTitle=title, original_url=original_url(row.get("adjunctUrl")))
        year = re.search(r"(20\d{2})年", question)
        if year and category == "periodic" and year[1] not in title:
            continue
        valid.append(row)
    ordered = sorted(
        valid, key=lambda v: (-int(v.get("announcementTime") or 0), str(v["announcementId"]))
    )
    if category == "periodic" and not re.search(r"季度|季报", question):
        full = [r for r in ordered if "年度报告" in r["announcementTitle"]]
        ordered = full + [r for r in ordered if r not in full]
    return ordered[:limit]


def catalog(client, subject, question, cutoff):
    _, registry = client.fetch("disclosure_issuers", REGISTRY, family="CNINFO")
    issuers = [r for r in registry.get("stockList", []) if str(r.get("code")) == subject["code"]]
    if len(issuers) != 1 or not issuers[0].get("orgId"):
        raise SourceUnavailable("巨潮发行人代码未能唯一核对")
    issuer = issuers[0]
    end = cutoff.date()
    year = re.search(r"(20\d{2})年", question)
    start = datetime(int(year[1]), 1, 1).date() if year else end - timedelta(days=450)
    selected, coverage = [], []
    for category in CATEGORIES:
        rows, total, failure = [], 0, None
        for page in (1, 2):
            params = dict(
                pageNum=str(page),
                pageSize="30",
                column="szse",
                tabName="relation" if category == "investor" else "fulltext",
                plate="",
                stock=f"{subject['code']},{issuer['orgId']}",
                searchkey="业绩" if category == "earnings" else "",
                secid="",
                category="category_ndbg_szsh;category_bndbg_szsh;category_yjdbg_szsh;category_sjdbg_szsh;"
                if category == "periodic"
                else "",
                trade="",
                seDate=f"{start}~{end}",
                sortName="time",
                sortType="desc",
                isHLtitle="false",
            )
            try:
                _, packet = client.fetch(
                    f"catalog_{category}_{page}", QUERY, data=params, family="CNINFO"
                )
                if "announcements" not in packet or "totalAnnouncement" not in packet:
                    raise SourceUnavailable("公告目录结构缺失")
                total = int(packet["totalAnnouncement"])
                rows.extend(packet["announcements"] or [])
                if len(rows) >= total:
                    break
            except SourceUnavailable as error:
                failure = str(error)
                break
        if category == "investor" and not rows and failure is None:
            params.update(tabName="fulltext", searchkey="投资者关系", pageNum="1")
            try:
                _, packet = client.fetch(
                    "catalog_investor_fulltext", QUERY, data=params, family="CNINFO"
                )
                rows = packet.get("announcements") or []
                total = int(packet.get("totalAnnouncement") or 0)
            except SourceUnavailable as error:
                failure = str(error)
        chosen = select_announcements(rows, category, question, subject["code"])
        # A provider date/timestamp does not prove historical version availability.
        chosen = [
            r
            for r in chosen
            if not r.get("announcementTime")
            or datetime.fromtimestamp(r["announcementTime"] / 1000, UTC).date() <= end
        ]
        selected.extend(dict(r, category=category) for r in chosen)
        coverage.append(
            dict(
                category=category,
                title=LABELS[category],
                start=str(start),
                end=str(end),
                total_reported=total,
                inspected=len(rows),
                selected=len(chosen),
                status="UNAVAILABLE"
                if failure
                else "PARTIAL"
                if total > len(rows)
                else "AVAILABLE",
                reason=failure or "每类最多检索前2页、选择2篇相关原文；不是完整披露档案。",
            )
        )
    unique = {}
    for row in selected:
        unique.setdefault(str(row["announcementId"]), row)
    return list(unique.values()), coverage
