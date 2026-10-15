"""Bounded P0 real-source audit; never generates predictions or synthetic market data.

Run manually. Existing AKShare requests are inspected as endpoint references, not
treated as another independent producer. Copyright-restricted industry pages are
hashed in memory; only links/availability notes are retained.
"""

import argparse
import hashlib
import io
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

from marketpulse.quant.decision.contracts import SourceCapability
from marketpulse.quant.live.securities import PROFILE, SMARTBOX, parse_candidates
from marketpulse.quant.live.sources import EM, KLINE, TENCENT, em_params

ROOT = Path(__file__).resolve().parents[1]
INDUSTRY = [
    ("S01", "WSTS 历史统计", "https://www.wsts.org/67/Historical-Billings-Report", "WSTS"),
    ("S02", "WSTS 预测与日程", "https://www.wsts.org/61/Forecasts", "WSTS"),
    ("S03", "SIA 市场统计", "https://www.semiconductors.org/policies/tax/market-data/", "WSTS"),
    (
        "S04",
        "SEMI 市场研究",
        "https://www.semi.org/en/products-services/market-intelligence",
        "SEMI",
    ),
    (
        "S05",
        "SEMI Billings",
        "https://www.semi.org/en/products-services/market-data/equipment/billings-report",
        "SEMI",
    ),
    ("S06", "中国半导体行业协会", "https://web.csia.net.cn/hyyjbg", "CSIA"),
    ("S10", "巨潮披露入口", "https://www.cninfo.com.cn/", "CNINFO"),
    ("S11", "上证 Level-2", "https://www.sseinfo.com/services/assortment/level2/", "SSE_LEVEL2"),
]
REPORTS = [
    ("S07", "中微公司", "688012", "2026-08-20/1225482884.PDF"),
    ("S08", "北方华创", "002371", "2026-08-26/1225501903.PDF"),
    ("S09", "中芯国际", "688981", "2026-08-28/1225520297.PDF"),
]
HOSTS = {
    "www.wsts.org",
    "www.semiconductors.org",
    "www.semi.org",
    "web.csia.net.cn",
    "www.cninfo.com.cn",
    "static.cninfo.com.cn",
    "www.sseinfo.com",
    "smartbox.gtimg.cn",
    "emweb.securities.eastmoney.com",
    "qt.gtimg.cn",
    "proxy.finance.qq.com",
    "datacenter-web.eastmoney.com",
    "push2his.eastmoney.com",
    "17.push2.eastmoney.com",
    "29.push2.eastmoney.com",
    "query.sse.com.cn",
    "api.fund.eastmoney.com",
    "np-anotice-stock.eastmoney.com",
}


def download(url, params=None, headers=None):
    # Validate every redirect BEFORE sending; do not download a model-provided URL.
    for _ in range(4):
        if urlsplit(url).scheme != "https" or urlsplit(url).hostname not in HOSTS:
            raise ValueError("unapproved source redirect")
        with requests.get(
            url,
            params=params,
            headers={"User-Agent": "Mozilla/5.0", **(headers or {})},
            timeout=(8, 18),
            stream=True,
            allow_redirects=False,
        ) as response:
            if response.is_redirect:
                url, params = urljoin(response.url, response.headers["Location"]), None
                continue
            response.raise_for_status()
            body = bytearray()
            for chunk in response.iter_content(65536):
                body.extend(chunk)
                if len(body) > 24 * 1024 * 1024:
                    raise ValueError("source exceeds audit size limit")
            return response.url, response.status_code, bytes(body)
    raise ValueError("too many redirects")


def probe(spec, output):
    key, name, url, family, parser, params, headers, archive = spec
    row = dict(
        source_id=key,
        name=name,
        url=url,
        source_family=family,
        acquisition="bounded HTTPS GET; inspected provider/AKShare endpoint",
        authentication="本次公开入口未使用账户",
        license_note=(
            "公开读取不等于获准批量采集或再分发；原始资料不对外导出。"
            if archive
            else "许可未确认：只保留入口、哈希和方法笔记，不存全文入RAG。"
        ),
        coverage=name,
        frequency="按源实际披露/交易日",
        status="UNTESTED",
        reason="未执行",
    )
    try:
        resolved, status, raw = download(url, params, headers)
        row.update(
            url=resolved,
            http_status=status,
            content_hash=hashlib.sha256(raw).hexdigest(),
            tested_at=datetime.now(UTC).isoformat(),
        )
        details = parser(raw)
        row.update(details)
        if archive:
            path = output / "objects" / row["content_hash"]
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                with path.open("xb") as f:
                    f.write(raw)
            row["raw_ref"] = str(path.relative_to(ROOT)).replace("\\", "/")
    except Exception as error:
        row.update(
            tested_at=datetime.now(UTC).isoformat(),
            status="UNAVAILABLE",
            reason=f"{type(error).__name__}: {str(error)[:200]}",
        )
        if isinstance(error, requests.HTTPError):
            row["http_status"] = error.response.status_code
    return SourceCapability.model_validate(row).model_dump(mode="json")


def page(raw):
    soup = BeautifulSoup(raw, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else "无标题"
    if len(soup.get_text(strip=True)) < 100:
        raise ValueError("page has no meaningful content")
    return dict(status="LINK_ONLY", reason=f"入口可访问：{title[:100]}；未接通结构化序列。")


def report_parser(name, code):
    def parse(raw):
        if not raw.startswith(b"%PDF-"):
            raise ValueError("expected original PDF")
        reader = PdfReader(io.BytesIO(raw))
        first = "\n".join(p.extract_text() or "" for p in reader.pages[:12])
        compact = "".join(first.split())
        if name not in compact or code not in compact or "2026" not in compact:
            raise ValueError("report cover security/year mismatch")
        audit = "未经审计" if "未经审计" in compact else "审计属性需进一步定位"
        return dict(
            status="AVAILABLE",
            sample_count=len(reader.pages),
            data_start="2026-01-01",
            data_end="2026-06-30",
            fields=["PDF封面身份", "报告期"],
            reason=(
                f"原始PDF {len(reader.pages)}页；前12页身份/年度一致；{audit}。不是全文深研完成。"
            ),
        )

    return parse


def json_rows(path, date_key=None, units=(), required=()):
    def parse(raw):
        value = json.loads(raw)
        for key in path:
            value = value[key]
        if isinstance(value, dict):
            value = list(value.values())
        if not isinstance(value, list) or not value:
            raise ValueError("empty source rows")
        if not isinstance(value[0], dict) or not set(required) <= value[0].keys():
            raise ValueError("source fields missing")
        days = sorted(str(r[date_key])[:10] for r in value) if date_key else []
        return dict(
            status="AVAILABLE",
            sample_count=len(value),
            fields=sorted(value[0]),
            units=units,
            data_start=days[0] if days else None,
            data_end=days[-1] if days else None,
            reason="小样本真实响应已取得；单位、延迟与历史版本能力尚不等于独立核验。",
        )

    return parse


def kline_parser(symbol):
    def parse(raw):
        data = json.loads(raw)["data"][symbol]
        rows = data.get("day")
        if not rows or len(rows[-1]) < 6:
            raise ValueError("raw daily schema/rows missing")
        return dict(
            status="AVAILABLE",
            sample_count=len(rows),
            data_start=rows[0][0],
            data_end=rows[-1][0],
            fields=["date", "open", "close", "high", "low", "volume"],
            units=["CNY/share"],
            reason="不复权日线可读；日线本身没有逐日交易所参考价，不可单独作除息日标签。",
        )

    return parse


def quote_parser(raw):
    text = raw.decode("gb18030")
    rows = [line.split('"')[1].split("~") for line in text.strip().splitlines()]
    if len(rows) != 2 or {r[2] for r in rows} != {"688012", "002371"}:
        raise ValueError("quote identity mismatch")
    if any(len(r) < 47 or not r[3] or not r[4] for r in rows):
        raise ValueError("missing raw quote price/reference")
    return dict(
        status="PARTIAL",
        sample_count=2,
        data_end=max(r[30] for r in rows),
        fields=["code[2]", "price[3]", "reference[4]", "time[30]"],
        units=["CNY/share"],
        reason="原始报价/前收字段可读；当前若在盘中不可当完整收盘。前收与交易所除权息参考、全日停牌仍需独立核查。",
    )


def specs():
    jobs = [(k, n, u, f, page, None, None, False) for k, n, u, f in INDUSTRY]
    jobs += [
        (
            k,
            n + "2026半年报",
            "https://static.cninfo.com.cn/finalpage/" + suffix,
            "CNINFO_" + code,
            report_parser(n, code),
            None,
            None,
            True,
        )
        for k, n, code, suffix in REPORTS
    ]
    jobs.append(
        (
            "quotes",
            "两证券原始报价",
            TENCENT,
            "TENCENT",
            quote_parser,
            {"q": "sh688012,sz002371"},
            None,
            True,
        )
    )
    for code, symbol in [("688012", "sh688012"), ("002371", "sz002371")]:

        def security(raw, code=code):
            values = parse_candidates(raw.decode("gb18030"))
            if not any(v["code"] == code for v in values):
                raise ValueError("security identity not returned")
            return dict(
                status="AVAILABLE", sample_count=len(values), reason="动态搜索返回代码与名称。"
            )

        jobs.extend(
            [
                (
                    "identity_" + code,
                    code + "动态身份",
                    SMARTBOX,
                    "TENCENT",
                    security,
                    {"q": code, "t": "all"},
                    None,
                    True,
                ),
                (
                    "profile_" + code,
                    code + "公司资料",
                    PROFILE,
                    "EASTMONEY",
                    json_rows(["jbzl"], required=["SECURITY_CODE", "SECURITY_NAME_ABBR"]),
                    {"code": symbol.upper()},
                    None,
                    True,
                ),
                (
                    "daily_" + code,
                    code + "原始日行情",
                    KLINE,
                    "TENCENT",
                    kline_parser(symbol),
                    {"param": f"{symbol},day,2026-06-01,2026-10-09,640,"},
                    None,
                    True,
                ),
                (
                    "margin_" + code,
                    code + "融资融券序列",
                    EM,
                    "EASTMONEY",
                    json_rows(["result", "data"], "DATE", ["CNY"], ["SCODE", "RZYE"]),
                    em_params("RPTA_WEB_RZRQ_GGMX", "DATE", 60, "SCODE", code),
                    None,
                    True,
                ),
                (
                    "flow_" + code,
                    code + "供应商资金估算",
                    "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
                    "EASTMONEY",
                    lambda raw: flow_parser(raw),
                    {
                        "lmt": "60",
                        "klt": "101",
                        "secid": ("1." if code[0] == "6" else "0.") + code,
                        "fields1": "f1,f2,f3,f7",
                        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
                    },
                    None,
                    True,
                ),
            ]
        )
    jobs.extend(
        [
            (
                "industry_members",
                "同源行业目录（首100项探测）",
                "https://17.push2.eastmoney.com/api/qt/clist/get",
                "EASTMONEY",
                json_rows(["data", "diff"], required=["f12", "f14"]),
                {
                    "pn": 1,
                    "pz": 100,
                    "np": 1,
                    "fs": "m:90 t:2 f:!50",
                    "fields": "f12,f14,f6,f104,f105",
                    "fltt": 2,
                },
                None,
                True,
            ),
            (
                "etf_shares",
                "上交所ETF份额（2026-10-09）",
                "https://query.sse.com.cn/commonQuery.do",
                "SSE",
                json_rows(["result"], "STAT_DATE", ["10k_shares"], ["TOT_VOL", "SEC_CODE"]),
                {
                    "isPagination": "true",
                    "pageHelp.pageSize": "10000",
                    "pageHelp.pageNo": "1",
                    "sqlId": "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L",
                    "STAT_DATE": "2026-10-09",
                },
                {"Referer": "https://www.sse.com.cn/"},
                True,
            ),
            (
                "etf_nav",
                "512480 ETF单位净值",
                "https://api.fund.eastmoney.com/f10/lsjz",
                "EASTMONEY",
                json_rows(["Data", "LSJZList"], "FSRQ", ["CNY/share"], ["DWJZ"]),
                {
                    "fundCode": "512480",
                    "pageIndex": 1,
                    "pageSize": 20,
                    "startDate": "2026-09-01",
                    "endDate": "2026-10-09",
                },
                {"Referer": "https://fundf10.eastmoney.com/jjjz_512480.html"},
                True,
            ),
        ]
    )
    return jobs


def flow_parser(raw):
    rows = json.loads(raw)["data"]["klines"]
    if not rows or len(rows[0].split(",")) != 15:
        raise ValueError("fund flow schema missing")
    return dict(
        status="AVAILABLE",
        estimated=True,
        sample_count=len(rows),
        units=["CNY", "percent"],
        data_start=rows[0].split(",")[0],
        data_end=rows[-1].split(",")[0],
        fields=["date", "vendor_classified_net_amounts", "ratios", "close"],
        reason="供应商按单量分类估算；不等于真实机构身份/意图；不得与融资或ETF相加。",
    )


def calendar_probe(output):
    code = """import json, baostock as bs
bs.login()
r=bs.query_trade_dates(start_date="2026-09-01",end_date="2026-12-01")
rows=[]
while r.error_code=="0" and r.next(): rows.append(r.get_row_data())
bs.logout()
print(json.dumps({"fields":r.fields,"rows":rows,"error":r.error_code}))"""
    base = dict(
        source_id="calendar",
        name="BaoStock交易日历",
        url="https://www.baostock.com/",
        source_family="BAOSTOCK",
        acquisition="provider query_trade_dates; subprocess timeout 40s",
        authentication="公开SDK登录",
        license_note="公开SDK不等于交易所官方保证或再分发授权。",
        coverage="沪深统一交易日期；未来日历需与交易所休市公告核对",
        frequency="交易日",
        tested_at=datetime.now(UTC).isoformat(),
        reason="未探测",
        status="UNTESTED",
    )
    try:
        p = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, timeout=40, check=True
        )
        value = json.loads(p.stdout.decode().strip().splitlines()[-1])
        if value["error"] != "0" or len(value["rows"]) < 60:
            raise ValueError("calendar incomplete")
        raw = json.dumps(value, sort_keys=True).encode()
        sha = hashlib.sha256(raw).hexdigest()
        path = output / "objects" / sha
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(raw)
        base.update(
            status="PARTIAL",
            fields=value["fields"],
            sample_count=len(value["rows"]),
            data_start=value["rows"][0][0],
            data_end=value["rows"][-1][0],
            content_hash=sha,
            raw_ref=str(path.relative_to(ROOT)).replace("\\", "/"),
            reason="供应商真实交易日历已存档；交易所分别核查、目标日及停牌仍未完成，不启用主签发。",
        )
    except Exception as e:
        base.update(status="UNAVAILABLE", reason=f"{type(e).__name__}: {str(e)[:160]}")
    return SourceCapability.model_validate(base).model_dump(mode="json")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / ".phase3-quant-o") or output.exists():
        raise ValueError("use a new audit directory within .phase3-quant-o")
    output.mkdir(parents=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda s: probe(s, output), specs()))
    rows.append(calendar_probe(output))
    audit = dict(
        schema_version="source-audit-v1",
        audited_at=datetime.now(UTC).isoformat(),
        kind="REAL_SOURCE_PROBE_SNAPSHOT",
        sources=rows,
    )
    (output / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf8"
    )
    print(json.dumps(audit, ensure_ascii=True))


if __name__ == "__main__":
    main()
