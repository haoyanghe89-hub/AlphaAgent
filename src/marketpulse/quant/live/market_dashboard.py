"""Observed sector rankings; classification is separate from timestamped quotes."""

import concurrent.futures
import hashlib
import json
import math
import re
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import requests

from ..decision import tushare

SINA = "https://vip.stock.finance.sina.com.cn/"
NODES = SINA + "quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
COUNT = SINA + "quotes_service/api/json_v2.php/Market_Center.getHQNodeStockCount"
QQ = "https://qt.gtimg.cn/q="
SHANGHAI = ZoneInfo("Asia/Shanghai")
SYMBOL = re.compile(r"(?:sh(?:60|68)|sz(?:00|30))\d{4}")


class _AwaitingTrades(ValueError):
    """Complete pre-open quotes have reset, but today's trading has not begun."""


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf8")
    tmp.replace(path)


def get(url, params=None):
    with requests.get(
        url,
        params=params,
        timeout=(4, 10),
        stream=True,
        headers={"User-Agent": "Mozilla/5.0", "Referer": SINA},
    ) as response:
        response.raise_for_status()
        parts, size = [], 0
        for part in response.iter_content(65536):
            size += len(part)
            if size > 3 * 1024 * 1024:
                raise ValueError("market response too large")
            parts.append(part)
    raw = b"".join(parts)
    return raw.decode("gb18030"), dict(
        url=url,
        params=params,
        retrieved_at=datetime.now(UTC).isoformat(),
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def catalog(text, kind):
    match = re.fullmatch(r"\s*var\s+\w+\s*=\s*(\{.*\})\s*;?\s*", text, re.S)
    if not match:
        raise ValueError("invalid sector catalog")
    packet, groups = json.loads(match[1]), []
    for key, value in packet.items():
        fields = value.split(",")
        if len(fields) < 2 or fields[0] != key or not re.fullmatch(r"[A-Za-z0-9_]+", key):
            raise ValueError("invalid sector identity")
        groups.append(dict(id=key, name=fields[1], kind=kind))
    if not groups:
        raise ValueError("empty sector catalog")
    return groups


def members(group, fetch=get):
    symbols, sources = set(), []
    # The upstream silently caps num at 100. Read every page in stable symbol order.
    for page in range(1, 61):
        text, source = fetch(
            NODES, dict(node=group["id"], page=page, num=100, sort="symbol", asc=1)
        )
        rows = json.loads(text)
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValueError("invalid sector membership page")
        all_symbols = [r["symbol"] for r in rows]
        if len(set(all_symbols)) != len(all_symbols) or symbols.intersection(all_symbols):
            raise ValueError("repeated sector membership page")
        symbols.update(all_symbols)
        sources.append(source)
        if len(rows) < 100:
            text, source = fetch(COUNT, dict(node=group["id"]))
            declared = int(json.loads(text))
            if declared < 0 or declared > len(symbols):
                raise ValueError("sector membership count changed or truncated")
            sources.append(source)
            return dict(
                group,
                symbols=sorted(s for s in symbols if SYMBOL.fullmatch(s)),
                sources=sources,
                provider_count=declared,
                enumerated_count=len(symbols),
            )
    raise ValueError("sector membership pagination limit")


def industry_members(root=None):
    """Current SW2021 membership, with token confined to the POST boundary."""

    def read(api, params):
        key = hashlib.sha256(json.dumps([api, params], sort_keys=True).encode()).hexdigest()
        path = root / (key + ".json") if root else None
        if path and path.is_file():
            saved = json.loads(path.read_text(encoding="utf8"))
            source = saved["source"]
            age = datetime.now(UTC) - datetime.fromisoformat(source["retrieved_at"])
            if (
                timedelta(0) <= age < timedelta(hours=24)
                and source["api"] == api
                and source["params"] == params
                and datetime.fromisoformat(source["retrieved_at"]).astimezone(SHANGHAI).date()
                == datetime.now(SHANGHAI).date()
            ):
                raw = saved["raw"].encode()
                if hashlib.sha256(raw).hexdigest() != source["sha256"]:
                    raise ValueError("classification cache hash mismatch")
                return tushare.records(json.loads(raw)), source
        body = tushare.request_body(
            dict(
                url=tushare.URL,
                family="TUSHARE",
                data=dict(api_name=api, params=params, fields=tushare.FIELDS[api]),
            )
        )
        for attempt in range(2):
            try:
                with requests.post(tushare.URL, json=body, timeout=(5, 15)) as response:
                    response.raise_for_status()
                    raw = response.content
                break
            except requests.RequestException:
                if attempt:
                    raise
        if len(raw) > 3 * 1024 * 1024:
            raise ValueError("classification response too large")
        tushare.validate_response(raw, body["token"])
        rows = tushare.records(json.loads(raw))
        source = dict(
            url=tushare.URL,
            api=api,
            params=params,
            retrieved_at=datetime.now(UTC).isoformat(),
            sha256=hashlib.sha256(raw).hexdigest(),
        )
        if path:
            write(path, dict(source=source, raw=raw.decode("utf8")))
        return rows, source

    catalog_rows, source = read("index_classify", dict(level="L1", src="SW2021"))
    master, master_source = read("stock_basic", dict(exchange="", list_status="L"))
    if not master or len(master) >= 6000 or len({r["ts_code"] for r in master}) != len(master):
        raise ValueError("incomplete current security master")
    eligible = {
        r["ts_code"]
        for r in master
        if r["list_status"] == "L"
        and SYMBOL.fullmatch(("sh" if r["ts_code"].endswith(".SH") else "sz") + r["ts_code"][:6])
        and r["ts_code"].endswith((".SH", ".SZ"))
    }
    if (
        not catalog_rows
        or len(catalog_rows) >= 2000
        or len({r["index_code"] for r in catalog_rows}) != len(catalog_rows)
    ):
        raise ValueError("invalid industry catalog")
    if any(r["level"] != "L1" or r["src"] != "SW2021" for r in catalog_rows):
        raise ValueError("mixed industry definitions")

    def one(row):
        values, evidence = read("index_member_all", dict(l1_code=row["index_code"], is_new="Y"))
        if not values or len(values) >= 2000 or len({r["ts_code"] for r in values}) != len(values):
            raise ValueError("incomplete industry members")
        if any(
            r["l1_code"] != row["index_code"] or r["is_new"] != "Y" or r.get("out_date")
            for r in values
        ):
            raise ValueError("industry membership conflict")
        return dict(
            id=row["index_code"],
            name=row["industry_name"],
            kind="industry",
            sources=[evidence],
            symbols=sorted(
                ("sh" if r["ts_code"].endswith(".SH") else "sz") + r["ts_code"][:6]
                for r in values
                if r["ts_code"] in eligible
            ),
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        groups = list(pool.map(one, catalog_rows))
    all_symbols = [s for g in groups for s in g["symbols"]]
    if len(set(all_symbols)) != len(all_symbols):
        raise ValueError("overlapping first-level industries")
    return groups, [source, master_source], len(eligible) - len(all_symbols)


def number(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("non-finite market value")
    return result


def quote_rows(text, requested, now):
    result = {}
    for symbol, body in re.findall(r'v_(\w+)="([^"]*)";', text):
        if symbol not in requested:
            continue
        parts = body.split("~")
        if len(parts) < 47 or parts[2] != symbol[2:]:
            continue
        try:
            quoted = datetime.strptime(parts[30], "%Y%m%d%H%M%S").replace(tzinfo=SHANGHAI)
            price, previous = number(parts[3]), number(parts[4])
            # Field 35 contains the unrounded CNY amount; field 45 is CNY 100 million.
            amount, cap = number(parts[35].split("/")[2]), number(parts[45]) * 100000000
            change = number(parts[32])
            if (
                price <= 0
                or previous <= 0
                or amount < 0
                or cap < 0
                or quoted > now + timedelta(minutes=2)
            ):
                continue
        except (ValueError, IndexError):
            continue
        result[symbol] = dict(
            symbol=symbol,
            code=parts[2],
            name=parts[1],
            price=price,
            change=change,
            amount=amount,
            market_cap=cap,
            quoted_at=quoted.isoformat(),
        )
    return result


def rank_groups(groups, quotes, quote_date):
    ranked = []
    for group in groups:
        observed = [
            quotes[s]
            for s in group["symbols"]
            if s in quotes and quotes[s]["quoted_at"][:10] == quote_date
        ]
        # Exclude incomplete groups from global ranking instead of treating missing values as zero.
        if len(observed) != len(group["symbols"]) or not observed:
            continue
        active = [q for q in observed if q["amount"] > 0]
        if not active:
            continue
        by_cap = sorted(active, key=lambda q: (-q["market_cap"], q["symbol"]))[:3]
        by_amount = sorted(active, key=lambda q: (-q["amount"], q["symbol"]))[:3]
        ranked.append(
            dict(
                id=group["id"],
                name=group["name"],
                kind=group["kind"],
                count=len(observed),
                active_count=len(active),
                amount=sum(q["amount"] for q in active),
                change=sum(q["change"] for q in active) / len(active),
                up=sum(q["change"] > 0 for q in active),
                down=sum(q["change"] < 0 for q in active),
                flat=sum(q["change"] == 0 for q in active),
                quoted_from=min(q["quoted_at"] for q in active),
                quoted_to=max(q["quoted_at"] for q in active),
                leaders=by_cap,
                trading_leaders=by_amount,
            )
        )
    return ranked


class MarketDashboard:
    """Single-flight, on-demand feed; no model, research job or prediction mutations."""

    def __init__(self, root: Path, fetch=get, industry=None):
        self.root, self.fetch = root, fetch
        self.industry = industry or (lambda: industry_members(root / "industry-tables"))
        self.lock, self.attempted, self.last, self.error = threading.Lock(), 0.0, None, None

    def classification(self):
        path = self.root / "classification.json"
        if path.is_file():
            cached = json.loads(path.read_text(encoding="utf8"))
            age = datetime.now(UTC) - datetime.fromisoformat(cached["retrieved_at"])
            if (
                timedelta(0) <= age < timedelta(hours=24)
                and cached.get("version") == 4
                and (not cached["errors"] or age < timedelta(minutes=5))
                and datetime.fromisoformat(cached["retrieved_at"]).astimezone(SHANGHAI).date()
                == datetime.now(SHANGHAI).date()
            ):
                return cached
        industries, sources, unclassified = self.industry()
        text, source = self.fetch(SINA + "q/view/newFLJK.php", dict(param="class"))
        groups = catalog(text, "concept")
        sources.append(source)
        completed, errors = [], []

        def one(group):
            for attempt in range(2):
                try:
                    return members(group, self.fetch)
                except (ValueError, KeyError, TypeError, requests.RequestException):
                    if attempt:
                        raise

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            tasks = {pool.submit(one, group): group for group in groups}
            for task in concurrent.futures.as_completed(tasks):
                try:
                    completed.append(task.result())
                except (ValueError, KeyError, TypeError, requests.RequestException):
                    errors.append(tasks[task]["id"])
        if not completed:
            raise ValueError("no verified sector constituents")
        # Exclude delisted concept members using the verified current A-share universe.
        eligible = {s for group in industries for s in group["symbols"]}
        for group in completed:
            group["symbols"] = sorted(set(group["symbols"]) & eligible)
        result = dict(
            version=4,
            groups=sorted(completed + industries, key=lambda g: g["id"]),
            sources=sources,
            retrieved_at=datetime.now(UTC).isoformat(),
            expected=len(groups) + len(industries),
            industry_expected=len(industries),
            concept_expected=len(groups),
            unclassified=unclassified,
            errors=errors,
        )
        write(path, result)
        return result

    def collect(self):
        classification = self.classification()
        groups = classification["groups"]
        symbols = sorted({s for g in groups for s in g["symbols"]})
        quotes, sources, failures = {}, [], 0
        now = datetime.now(SHANGHAI)

        def chunk(batch):
            for attempt in range(2):
                try:
                    text, source = self.fetch(QQ + ",".join(batch))
                    parsed = quote_rows(text, batch, now)
                    if len(parsed) != len(batch):
                        raise ValueError("quote batch missing requested securities")
                    return parsed, source
                except (ValueError, requests.RequestException):
                    if attempt:
                        raise

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            tasks = [pool.submit(chunk, symbols[i : i + 300]) for i in range(0, len(symbols), 300)]
            for task in concurrent.futures.as_completed(tasks):
                try:
                    observed, source = task.result()
                    quotes.update(observed)
                    sources.append(source)
                except (ValueError, requests.RequestException):
                    failures += 1
        if not quotes:
            raise ValueError("no timestamped market quotes")
        dates = sorted({q["quoted_at"][:10] for q in quotes.values()})
        quote_date = dates[-1]
        if (
            len(quotes) == len(symbols)
            and dates == [now.date().isoformat()]
            and now.hour * 60 + now.minute < 570
            and not any(q["amount"] > 0 for q in quotes.values())
        ):
            raise _AwaitingTrades("pre-open quotes have no executed turnover yet")
        rows = rank_groups(groups, quotes, quote_date)
        industries = sorted(
            (r for r in rows if r["kind"] == "industry"), key=lambda r: (-r["change"], r["id"])
        )
        concepts = sorted(
            (r for r in rows if r["kind"] == "concept"), key=lambda r: (-r["amount"], r["id"])
        )
        if not industries or not concepts:
            raise ValueError("insufficient verified groups for market ranking")
        today_quotes = [q for q in quotes.values() if q["quoted_at"][:10] == quote_date]
        return dict(
            industries=industries,
            hot_sectors=concepts[:10],
            quote_date=quote_date,
            quoted_from=min(q["quoted_at"] for q in today_quotes),
            quoted_to=max(q["quoted_at"] for q in today_quotes),
            retrieved_at=datetime.now(UTC).isoformat(),
            classification_at=classification["retrieved_at"],
            coverage=dict(
                groups_expected=classification["expected"],
                groups_classified=len(groups),
                industries_expected=classification["industry_expected"],
                concepts_expected=classification["concept_expected"],
                stocks_unclassified=classification["unclassified"],
                industries_ranked=len(industries),
                concepts_ranked=len(concepts),
                stocks_expected=len(symbols),
                stocks_quoted=len(today_quotes),
                quote_batches_failed=failures,
            ),
            partial=len(rows) < classification["expected"] or classification["unclassified"] > 0,
            sources=sources + classification["sources"],
            methodology=dict(
                scope=(
                    "申万2021一级行业与新浪概念分类，沪深 A 股；"
                    "概念成分可能重叠，成交额不可跨板块相加"
                ),
                hot="概念板块按成分股当日累计成交额降序",
                gain="行业按有成交的成分股涨跌幅等权平均排序，非行业指数涨幅",
                leaders="默认按当日总市值列前三只，可切换成交额前三；不等同于基本面龙头或买入建议",
            ),
        )

    def snapshot(self):
        with self.lock:
            if self.last is None and (self.root / "snapshot.json").is_file():
                self.last = json.loads((self.root / "snapshot.json").read_text(encoding="utf8"))
            if time.monotonic() - self.attempted >= 25:
                try:
                    current = self.collect()
                    coverage = current.get("coverage")
                    if coverage and (
                        coverage["industries_ranked"] != coverage["industries_expected"]
                        or coverage["stocks_quoted"] != coverage["stocks_expected"]
                        or coverage["quote_batches_failed"]
                    ):
                        raise ValueError("incomplete market refresh cannot replace a full snapshot")
                    write(self.root / "snapshot.json", current)
                    self.last, self.error = current, None
                except _AwaitingTrades:
                    self.error = None
                except (OSError, ValueError, KeyError, TypeError, requests.RequestException):
                    self.error = "行情源刷新失败，保留上次真实快照；请查看报价日期与时间。"
                self.attempted = time.monotonic()
            if self.last is None:
                raise ValueError("market dashboard unavailable")
            result = dict(self.last, refresh_error=self.error)
            now = datetime.now(SHANGHAI)
            latest = datetime.fromisoformat(result["quoted_to"])
            local_minute = now.hour * 60 + now.minute
            in_session = now.weekday() < 5 and (
                570 <= local_minute <= 690 or 780 <= local_minute <= 900
            )
            result["freshness"] = (
                "DELAYED"
                if self.error or (in_session and now - latest > timedelta(minutes=3))
                else "LIVE"
                if in_session and now.date() == latest.date()
                else "LATEST_QUOTE"
            )
            return result
