"""Free exchange lifecycle observations, never proof of a complete PIT archive."""

import asyncio
import hashlib
import json
import re
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import httpx

from marketpulse.infrastructure.storage.models import BlobRef
from marketpulse.investigation.api import _error

from ..contracts import digest
from .history import is_a_share

KINDS = ("sh_main", "sh_star", "sh_retired", "sz_active", "sz_retired")
SSE = "https://query.sse.com.cn/sseQuery/commonQuery.do"
SZSE = "https://www.szse.cn/api/report/ShowReport"


def endpoint(query):
    if set(query) != {"kind", "table"} or query["kind"] != "official_lifecycle":
        raise ValueError("fixed lifecycle query required")
    table = query["table"]
    if table not in KINDS:
        raise ValueError("unsupported lifecycle table")
    if table.startswith("sz_"):
        return (
            SZSE,
            dict(
                SHOWTYPE="xlsx",
                CATALOGID="1110" if table == "sz_active" else "1793_ssgs",
                TABKEY="tab1" if table == "sz_active" else "tab2",
            ),
            "https://www.szse.cn/",
        )
    return (
        SSE,
        {
            "sqlId": "COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L",
            "isPagination": "true",
            "STOCK_CODE": "",
            "CSRC_CODE": "",
            "REG_PROVINCE": "",
            "STOCK_TYPE": {"sh_main": "1", "sh_star": "8", "sh_retired": "1,2,8"}[table],
            "COMPANY_STATUS": "3" if table == "sh_retired" else "2,4,5,7,8",
            "type": "inParams",
            "pageHelp.cacheSize": "1",
            "pageHelp.beginPage": "1",
            "pageHelp.pageSize": "10000",
            "pageHelp.pageNo": "1",
            "pageHelp.endPage": "1",
        },
        "https://www.sse.com.cn/assortment/stock/list/share/",
    )


def xlsx_records(document):
    """Read the fixed exchange tabular export, bounding XML before parsing."""
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with ZipFile(BytesIO(document)) as book:
        entries = book.infolist()
        if (
            len({e.filename for e in entries}) != len(entries)
            or sum(e.file_size for e in entries) > 64 * 1024 * 1024
        ):
            raise ValueError("workbook archive bound/duplicate")

        def xml(name):
            content = book.read(name)
            if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
                raise ValueError("workbook external entities forbidden")
            return ET.fromstring(content)

        strings = (
            ["".join(item.itertext()) for item in xml("xl/sharedStrings.xml").findall("s:si", ns)]
            if "xl/sharedStrings.xml" in book.namelist()
            else []
        )
        rows = xml("xl/worksheets/sheet1.xml").findall("s:sheetData/s:row", ns)
        if not rows or len(rows) > 20001:
            raise ValueError("workbook row bound/empty")
        values = []
        for row in rows:
            record = {}
            for cell in row.findall("s:c", ns):
                match = re.fullmatch(r"([A-Z]{1,2})\d+", cell.attrib.get("r", ""))
                if not match or cell.find("s:f", ns) is not None:
                    raise ValueError("workbook cell address/formula")
                column = match[1]
                value = cell.findtext("s:v", default="", namespaces=ns)
                if cell.attrib.get("t") == "s":
                    value = strings[int(value)]
                elif cell.attrib.get("t") == "inlineStr":
                    value = "".join(cell.find("s:is", ns).itertext())
                if column in record:
                    raise ValueError("duplicate workbook cell")
                record[column] = value.strip()
            values.append(record)
        headers = values[0]
        if len(set(headers.values())) != len(headers):
            raise ValueError("duplicate workbook header")
        return [{name: row.get(col, "") for col, name in headers.items()} for row in values[1:]]


def day(value, *, optional=False):
    if value in {"", "-", None} and optional:
        return None
    value = str(value)
    if re.fullmatch(r"\d{8}", value):
        return datetime.strptime(value, "%Y%m%d").date().isoformat()
    if re.fullmatch(r"\d{5}", value):
        return (date(1899, 12, 30) + timedelta(days=int(value))).isoformat()
    return date.fromisoformat(value).isoformat()


def extract_lifecycles(document, query):
    endpoint(query)
    table, rows, excluded, seen = query["table"], [], 0, set()
    if table.startswith("sh_"):
        raw = json.loads(document)
        records = raw.get("result")
        if (
            not isinstance(records, list)
            or not records
            or len(records) > 20000
            or raw.get("pageHelp", {}).get("total") != len(records)
        ):
            raise ValueError("exchange pagination/empty response")
    else:
        records = xlsx_records(document)
    for record in records:
        if table.startswith("sh_"):
            # A/B dual listings repeat A_STOCK_CODE on the B-share row;
            # LIST_DATE belongs to STOCK_TYPE, not necessarily that A code.
            if record["STOCK_TYPE"] == "2":
                excluded += 1
                continue
            if record["STOCK_TYPE"] not in {"1", "8"}:
                raise ValueError("unknown exchange stock type")
            code = record["A_STOCK_CODE"]
            listed, retired = record["LIST_DATE"], record["DELIST_DATE"]
            name = record["COMPANY_ABBR"]
        elif table == "sz_active":
            code, name = record["A股代码"], record["A股简称"]
            listed, retired = record["A股上市日期"], None
        else:
            code, name = record["证券代码"], record["证券简称"]
            listed, retired = record["上市日期"], record["终止上市日期"]
        prefix = "sh." if table.startswith("sh_") else "sz."
        if not is_a_share(prefix + code):
            excluded += 1
            continue
        listed, retired = day(listed), day(retired, optional=not table.endswith("retired"))
        if code in seen or not name or (retired and retired <= listed):
            raise ValueError("duplicate lifecycle/invalid interval")
        if not table.endswith("retired") and retired:
            raise ValueError("active table contains retired interval")
        seen.add(code)
        rows.append(dict(code=prefix + code, name=name, listed_at=listed, delisted_at=retired))
    if not rows:
        raise ValueError("no A-share lifecycle rows")
    return dict(rows=sorted(rows, key=lambda r: r["code"]), excluded_non_a_share=excluded)


async def fetch_lifecycles(query, store):
    url, params, referer = endpoint(query)
    async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as client:
        async with client.stream(
            "GET", url, params=params, headers={"Referer": referer}
        ) as response:
            response.raise_for_status()
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > 4 * 1024 * 1024:
                    raise ValueError("lifecycle document byte bound")
                chunks.append(chunk)
    document = b"".join(chunks)
    extracted = await asyncio.to_thread(extract_lifecycles, document, query)
    blob = store.blobs.put_bytes(document)
    return dict(
        provider="sse_official" if query["table"].startswith("sh_") else "szse_official",
        query=query,
        source_url=url,
        params=params,
        **extracted,
        retrieved_at=datetime.now(UTC).isoformat(),
        document_sha256=blob.ref.sha256,
        document_size=size,
    )


def lifecycle_target(runtime, parent_id):
    from .api import load_check

    parent = load_check(runtime, parent_id)
    if parent["check_status"] != "COMPLETED" or not parent.get("historical_data"):
        raise _error(422, "LIFECYCLE_PARENT_INCOMPLETE", "请先完成历史股票池采集。")
    basics, annual = {}, []
    for source in parent["historical_data"]["sources"]:
        if source["query"]["kind"] not in {"basic", "members"}:
            continue
        packet = json.loads(runtime.service.store.blobs.get_bytes(BlobRef(source["sha256"])))
        if digest(packet) != source["sha256"]:
            raise ValueError("parent lifecycle source drift")
        if source["query"]["kind"] == "basic":
            basics.update({r["code"]: r for r in packet["rows"]})
        else:
            annual.append(
                dict(
                    day=source["query"]["day"],
                    source_hash=source["sha256"],
                    codes=sorted(r["code"] for r in packet["rows"] if is_a_share(r["code"])),
                )
            )
    for year in annual:
        year["codes"] = [
            c for c in year["codes"] if basics.get(c, {}).get("type") not in {"2", "3", "4", "5"}
        ]
    if not annual:
        raise _error(422, "LIFECYCLE_MEMBERS_MISSING", "父报告没有冻结年度名单。")
    return dict(
        start=parent["meta"]["date_start"],
        end=parent["meta"]["date_end"],
        native=basics,
        annual=sorted(annual, key=lambda a: a["day"]),
        queries=[dict(kind="official_lifecycle", table=table) for table in KINDS],
    )


def inspect_lifecycles(packets, target, store):
    sources, seen, by_code = [], set(), defaultdict(list)
    for packet in packets:
        query = packet["query"]
        url, params, _ = endpoint(query)
        provider = "sse_official" if query["table"].startswith("sh_") else "szse_official"
        if (
            query not in target["queries"]
            or query["table"] in seen
            or packet["source_url"] != url
            or packet["params"] != params
            or packet["provider"] != provider
        ):
            raise ValueError("lifecycle frozen target/provider drift")
        document = store.blobs.get_bytes(BlobRef(packet["document_sha256"]))
        if (
            len(document) != packet["document_size"]
            or hashlib.sha256(document).hexdigest() != packet["document_sha256"]
            or extract_lifecycles(document, query)
            != {k: packet[k] for k in ("rows", "excluded_non_a_share")}
        ):
            raise ValueError("lifecycle raw extraction drift")
        seen.add(query["table"])
        for row in packet["rows"]:
            by_code[row["code"]].append(row)
        sources.append(
            dict(
                query=query,
                source_url=url,
                params=params,
                row_count=len(packet["rows"]),
                excluded_non_a_share=packet["excluded_non_a_share"],
                retrieved_at=packet["retrieved_at"],
                document_sha256=packet["document_sha256"],
                packet_sha256=digest(packet),
            )
        )
    complete = len(seen) == len(target["queries"])
    annual = []
    for window in target["annual"]:
        day_ = window["day"]
        observed = {
            code
            for code, rows in by_code.items()
            if any(
                r["listed_at"] <= day_ and (not r["delisted_at"] or day_ < r["delisted_at"])
                for r in rows
            )
        }
        native = set(window["codes"])
        details = []
        for code in sorted(native | observed):
            rows = by_code.get(code, [])
            active = [
                r
                for r in rows
                if r["listed_at"] <= day_ and (not r["delisted_at"] or day_ < r["delisted_at"])
            ]
            if code not in native or code not in observed or len(active) != 1:
                details.append(
                    dict(
                        code=code,
                        name=rows[0]["name"] if rows else "—",
                        status="UNAVAILABLE"
                        if not rows
                        else "CONFLICT"
                        if code not in observed
                        else "UNAVAILABLE"
                        if code not in native
                        else "METHOD_DIFFERENCE",
                        reason="only_official_observation"
                        if code not in native
                        else "official_lifecycle_unavailable"
                        if not rows
                        else "outside_official_interval"
                        if not active
                        else "ambiguous_lifecycle",
                    )
                )
        annual.append(
            dict(
                signal_day=day_,
                native_source_hash=window["source_hash"],
                native_count=len(native),
                official_observed_count=len(observed) if complete else None,
                common_count=len(native & observed),
                only_native_count=len(native - observed) if complete else None,
                only_official_count=len(observed - native),
                details=details,
            )
        )
    checks = []
    for code in sorted({c for a in target["annual"] for c in a["codes"]}):
        rows, native = by_code.get(code, []), target["native"].get(code, {})
        status = "UNAVAILABLE"
        if len(rows) > 1:
            status = "METHOD_DIFFERENCE"
        elif rows and native.get("ipoDate"):
            row = rows[0]
            status = (
                "CONFLICT"
                if row["listed_at"] != native["ipoDate"]
                else "UNAVAILABLE"
                if bool(row["delisted_at"]) != bool(native.get("outDate"))
                else "CONFLICT"
                if row["delisted_at"] and row["delisted_at"] != native["outDate"]
                else "MATCH"
            )
        checks.append(
            dict(
                code=code,
                status=status,
                native_listed_at=native.get("ipoDate"),
                native_delisted_at=native.get("outDate") or None,
                official_intervals=rows,
            )
        )
    return dict(
        schema_version="backtest-lifecycle-audit-v1",
        start=target["start"],
        end=target["end"],
        sources=sources,
        annual=annual,
        checks=checks,
        check_counts={
            s: sum(c["status"] == s for c in checks)
            for s in ("MATCH", "CONFLICT", "METHOD_DIFFERENCE", "UNAVAILABLE")
        },
        complete=complete,
        pit_level="PARTIAL",
        execution_status="BLOCKED",
        gaps=[
            "这是当前下载的交易所生命周期回溯观察，未证明当年完整名单、改码及全部修订，不能升级STRICT。",
            "年度双方独有和日期冲突均保留；官方独有也可能来自代码历史或范围差异，不自动加入或删除父名单。",
            "财报修订、完整公司行为及成交限制仍未准入；本次不执行筛选、成交或绩效。",
        ],
    )
