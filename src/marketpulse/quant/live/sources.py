"""Fixed free-source requests, bounded bodies and recorded retrieval times."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

SINA = "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022"
EM = "https://datacenter-web.eastmoney.com/api/data/v1/get"
FINANCE = "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/"
TENCENT = "https://qt.gtimg.cn/"
KLINE = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
SYMBOLS = ("sz000333", "sh000001", "sz399001", "sz399006", "sh000300", "sh000688")


def fetch(key, url, params):
    # SDK transport is optional for the main investigation application.
    import requests

    with requests.get(url, params=params, timeout=(8, 18), stream=True) as response:
        response.raise_for_status()
        body = bytearray()
        for chunk in response.iter_content(65536):
            body.extend(chunk)
            if len(body) > 3 * 1024 * 1024:
                raise ValueError("source body exceeds 3 MiB")
        raw = bytes(body)
        value = raw.decode("gb18030") if key in {"quotes", "security_search"} else json.loads(raw)
        return (
            dict(
                key=key,
                url=response.url,
                retrieved_at=datetime.now(UTC).isoformat(),
                sha256=hashlib.sha256(raw).hexdigest(),
                byte_count=len(raw),
            ),
            raw,
            value,
        )


def quote_values(text, symbols=SYMBOLS):
    result = []
    for line in text.strip().splitlines():
        symbol = line.split("=", 1)[0].removeprefix("v_")
        if symbol not in symbols:
            continue
        parts = line.split('"')[1].split("~")
        if len(parts) < 47 or parts[2] != symbol[2:]:
            raise ValueError("quote symbol/schema mismatch")
        stamp = datetime.strptime(parts[30], "%Y%m%d%H%M%S")
        price, previous = float(parts[3]), float(parts[4])
        if not 0 < price < 1e8 or previous <= 0:
            raise ValueError("invalid quote")
        result.append(
            dict(
                code=parts[2],
                symbol=symbol,
                name=parts[1],
                price=price,
                previous_close=previous,
                change=float(parts[32]),
                quoted_at=stamp.isoformat() + "+08:00",
                provider="腾讯财经",
                # Provider valuation is an observation, not independently verified.
                pe=float(parts[39]) if parts[39] else None,
                pb=float(parts[46]) if parts[46] else None,
                market_cap=float(parts[45]) * 1e8 if parts[45] else None,
            )
        )
    if {x["symbol"] for x in result} != set(symbols):
        raise ValueError("incomplete quote response")
    return result


def latest_quotes(symbol=None):
    requested = (symbol or "sz000333").split(",")
    symbols = tuple(dict.fromkeys([*requested, *SYMBOLS[1:]]))
    meta, _, value = fetch("quotes", TENCENT, {"q": ",".join(symbols)})
    return dict(quotes=quote_values(value, symbols), **meta)


def em_params(report, sort, size, field="SECURITY_CODE", code="000333"):
    return dict(
        reportName=report,
        columns="ALL",
        filter=f'({field}="{code}")',
        sortColumns=sort,
        sortTypes="-1",
        pageNumber="1",
        pageSize=str(size),
    )


def collect(start, end, save, progress, subject=None):
    subject = subject or dict(code="000333", symbol="sz000333", company_type="4")
    symbol, code = subject["symbol"], subject["code"]
    symbols = tuple(dict.fromkeys([symbol, *SYMBOLS[1:]]))

    def params(*args):
        return em_params(*args, code=code)

    jobs = {
        "quotes": (TENCENT, {"q": ",".join(symbols)}),
        "prices": (KLINE, {"param": f"{symbol},day,{start},{end},640,qfq"}),
        "benchmark": (KLINE, {"param": f"sh000300,day,{start},{end},640,"}),
        **{
            k: (SINA, dict(paperCode=symbol, source=k, type="0", page="1", num="20"))
            for k in ("lrb", "fzb", "llb")
        },
        "valuation": (EM, params("RPT_VALUEANALYSIS_DET", "TRADE_DATE", 500)),
        "margin": (EM, params("RPTA_WEB_RZRQ_GGMX", "DATE", 120, "SCODE")),
        "pledge": (EM, params("RPTA_APP_ACCUMDETAILS", "NOTICE_DATE", 100)),
        "unlock": (EM, params("RPT_LIFT_STAGE", "FREE_DATE", 100)),
        "income_check": (EM, params("RPT_DMSK_FN_INCOME", "REPORT_DATE", 20)),
    }
    values, sources = {"source_errors": {}}, []

    def batch(requests_):
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {k: pool.submit(fetch, k, *v) for k, v in requests_.items()}
            for key, future in futures.items():
                try:
                    meta, raw, value = future.result()
                    save(key, raw)
                    sources.append(meta)
                    values[key] = value
                    progress(len(sources))
                except Exception as error:
                    if key in {"quotes", "prices", "benchmark"}:
                        raise
                    values["source_errors"][key] = type(error).__name__

    batch(jobs)
    if "lrb" not in values:
        return values, sources
    today = datetime.now(UTC).date().isoformat().replace("-", "")
    periods = [
        p
        for p, r in values["lrb"]["result"]["data"]["report_list"].items()
        if r.get("publish_date") and r["publish_date"] <= today
    ]
    if not periods or subject["company_type"] != "4":
        return values, sources
    annual = max(p for p in periods if p.endswith("1231"))
    latest = max(periods)

    def iso(p):
        return f"{p[:4]}-{p[4:6]}-{p[6:]}"

    # Request the newest annual basis and latest balance explicitly, never a pinned demo year.
    dates = ",".join(dict.fromkeys([iso(annual), iso(latest)]))
    batch(
        {
            key: (
                FINANCE + endpoint + "AjaxNew",
                dict(
                    companyType=subject["company_type"],
                    reportDateType="0",
                    reportType="1",
                    dates=dates,
                    code=symbol.upper(),
                ),
            )
            for key, endpoint in (
                ("cash_em", "xjllb"),
                ("income_em", "lrb"),
                ("balance_em", "zcfzb"),
            )
        }
    )
    return values, sources
