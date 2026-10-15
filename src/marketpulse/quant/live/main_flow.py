"""Dated main-fund proxies; Tencent minute and Tushare daily definitions stay separate."""

import hashlib
import json
import threading
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

import requests

from marketpulse.infrastructure.storage.models import BlobRef

from ..decision import tushare
from .market_dashboard import SHANGHAI, SYMBOL, write

QQ = "https://proxy.finance.qq.com/cgi/cgi-bin/fundflow/hsfundtab"
MINUTE_FIELDS = dict(
    main="MainNetInflow",
    large="BigNetInflow",
    extra="SuperNetInflow",
    inflow="MainInflow",
    outflow="MainOutflow",
    price="Price",
)


def amount(value, nonnegative=False):
    if isinstance(value, bool) or value in (None, "", "-"):
        raise ValueError("missing funding value")
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError("invalid funding value") from error
    if not result.is_finite() or (nonnegative and result < 0):
        raise ValueError("invalid funding value")
    return result


def minute_projection(packet, symbol, now):
    if packet.get("code") != 0 or packet.get("data", {}).get("ffHide"):
        raise ValueError("funding data unavailable")
    data = packet["data"]
    flow, trend = data.get("todayFundFlow") or {}, data.get("todayFundTrend") or {}
    if flow.get("stockCode") != symbol or trend.get("stockCode") != symbol:
        raise ValueError("funding identity mismatch")
    points = []
    for row in trend.get("minList", []):
        stamp = datetime.strptime(row["time"], "%Y%m%d%H%M").replace(tzinfo=SHANGHAI)
        clock = stamp.hour * 60 + stamp.minute
        if (
            stamp > now
            or not (570 <= clock <= 690 or 780 <= clock <= 900)
            or (points and stamp.isoformat() <= points[-1]["at"])
        ):
            raise ValueError("invalid or repeated minute funding time")
        values = {
            key: amount(row[field], key in {"price", "inflow", "outflow"})
            for key, field in MINUTE_FIELDS.items()
        }
        if (
            values["price"] <= 0
            or abs(values["main"] - values["large"] - values["extra"]) > 2
            or abs(values["main"] - values["inflow"] + values["outflow"]) > 2
        ):
            raise ValueError("minute funding arithmetic mismatch")
        points.append(
            dict(at=stamp.isoformat(), **{key: float(value) for key, value in values.items()})
        )
    if not points or len(points) > 242 or len({p["at"][:10] for p in points}) != 1:
        raise ValueError("empty, oversized or cross-date funding series")
    latest = points[-1]
    by_time = {p["at"]: p for p in points}
    windows = []
    for width in (5, 15):
        end = datetime.fromisoformat(latest["at"])
        start = end - timedelta(minutes=width)
        required = [(start + timedelta(minutes=i)).isoformat() for i in range(width + 1)]
        if not all(stamp in by_time for stamp in required):
            windows.append(dict(minutes=width, main=None, price_change=None, divergence=None))
            continue
        baseline = by_time[required[0]]
        net = latest["main"] - baseline["main"]
        change = (latest["price"] / baseline["price"] - 1) * 100
        divergence = (
            "PRICE_UP_FLOW_OUT"
            if change > 0 and net < 0
            else "PRICE_DOWN_FLOW_IN"
            if change < 0 and net > 0
            else "ALIGNED_OR_FLAT"
        )
        windows.append(
            dict(
                minutes=width,
                main=net,
                price_change=change,
                divergence=divergence,
                from_at=baseline["at"],
                to_at=latest["at"],
            )
        )
    return dict(
        symbol=symbol,
        code=symbol[2:],
        asof=latest["at"],
        latest=latest,
        points=points,
        windows=windows,
        provider="腾讯证券",
        granularity="minute",
        method="QQ_MAIN_MINUTE_V1",
        definition=flow.get("desc", "主力=超大单+大单；供应商估算"),
    )


def daily_projection(rows, master, classification, session):
    if not rows or len(rows) >= 6000 or len({r["ts_code"] for r in rows}) != len(rows):
        raise ValueError("daily funding truncated or duplicated")
    if any(r["trade_date"] != session.replace("-", "") for r in rows):
        raise ValueError("cross-date daily funding")
    identities = {r["ts_code"]: r for r in master}
    if not master or len(master) >= 6000 or len(identities) != len(master):
        raise ValueError("invalid security master")
    groups = [g for g in classification["groups"] if g["kind"] == "industry"]
    universe = {s for g in groups for s in g["symbols"]}
    if (
        not groups
        or len({g["id"] for g in groups}) != len(groups)
        or len(universe) != sum(len(g["symbols"]) for g in groups)
    ):
        raise ValueError("overlapping or missing industry membership")
    stocks = {}
    for row in rows:
        sid = row["ts_code"]
        symbol = ("sh" if sid.endswith(".SH") else "sz") + sid[:6]
        if symbol not in universe:
            continue
        if sid not in identities or identities[sid]["list_status"] != "L":
            raise ValueError("daily funding issuer not listed")
        lg_buy, lg_sell, elg_buy, elg_sell = [
            amount(row[k], True) * 10000
            for k in ("buy_lg_amount", "sell_lg_amount", "buy_elg_amount", "sell_elg_amount")
        ]
        large, extra = lg_buy - lg_sell, elg_buy - elg_sell
        stocks[symbol] = dict(
            symbol=symbol,
            code=sid[:6],
            name=identities[sid]["name"],
            main=float(large + extra),
            large=float(large),
            extra=float(extra),
            inflow=float(lg_buy + elg_buy),
            outflow=float(lg_sell + elg_sell),
        )
    if set(stocks) != universe:
        raise ValueError("daily funding missing declared industry members")
    industries = []
    for group in groups:
        observed = [stocks[s] for s in group["symbols"]]
        if not observed:
            raise ValueError("empty industry funding scope")
        for stock in observed:
            stock["industry"] = group["name"]
        industries.append(
            dict(
                id=group["id"],
                name=group["name"],
                count=len(observed),
                main=sum(s["main"] for s in observed),
                large=sum(s["large"] for s in observed),
                extra=sum(s["extra"] for s in observed),
                inflow_count=sum(s["main"] > 0 for s in observed),
                outflow_count=sum(s["main"] < 0 for s in observed),
                leaders=sorted(observed, key=lambda s: (-s["main"], s["symbol"]))[:3],
            )
        )
    return dict(
        session=session,
        asof=session,
        granularity="daily",
        provider="Tushare",
        method="TS_LARGE_EXTRA_NET_V1",
        stocks=list(stocks.values()),
        industries=sorted(industries, key=lambda g: (-g["main"], g["id"])),
        coverage=dict(industries=len(industries), stocks=len(stocks)),
        definition="大单与特大单买入金额减卖出金额，万元换算为元；主力代理，非机构身份识别",
    )


class MainFlowFeed:
    def __init__(self, repo, market):
        self.repo, self.market = repo, market
        self.root = repo.legacy_root.parent / "main-flow-cache"
        self.locks = {}
        self.attempted = {}
        self.errors = {}

    def response(self, url, params=None, body=None):
        # A bounded retry; errors and request credentials are never archived or logged.
        for attempt in range(2):
            try:
                call = requests.post if body else requests.get
                options = dict(json=body) if body else dict(params=params)
                with call(
                    url,
                    timeout=(5, 18),
                    stream=True,
                    headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"},
                    **options,
                ) as response:
                    response.raise_for_status()
                    parts, size = [], 0
                    for part in response.iter_content(65536):
                        size += len(part)
                        if size > 4 * 1024 * 1024:
                            raise ValueError("funding response too large")
                        parts.append(part)
                raw = b"".join(parts)
                if body:
                    tushare.validate_response(raw, body["token"])
                packet = json.loads(raw)
                return packet, dict(
                    url=url,
                    params=params or body["params"],
                    api=body["api_name"] if body else "hsfundtab",
                    retrieved_at=datetime.now(UTC).isoformat(),
                    sha256=hashlib.sha256(raw).hexdigest(),
                    blob_ref=self.repo.raw(raw),
                )
            except requests.RequestException:
                if attempt:
                    raise

    def table(self, api, params):
        public = dict(api_name=api, params=params, fields=tushare.FIELDS[api])
        body = tushare.request_body(dict(url=tushare.URL, family="TUSHARE", data=public))
        packet, source = self.response(tushare.URL, body=body)
        return tushare.records(packet), source

    def load(self, key):
        path = self.root / (key + ".json")
        if not path.is_file():
            return None
        pointer = json.loads(path.read_text(encoding="utf8"))
        value = self.repo.value(pointer["result_ref"])
        for source in value["sources"]:
            ref = BlobRef.from_uri(source["blob_ref"])
            if ref.sha256 != source["sha256"]:
                raise ValueError("funding source descriptor mismatch")
            self.repo.blobs.get_bytes(ref)
        return value

    def save(self, key, value):
        write(self.root / (key + ".json"), dict(result_ref=self.repo.blob(value)))

    def daily(self):
        def collect():
            now = datetime.now(SHANGHAI)
            days, calendar = self.table(
                "trade_cal",
                dict(
                    exchange="SSE",
                    start_date=(now.date() - timedelta(days=20)).strftime("%Y%m%d"),
                    end_date=now.strftime("%Y%m%d"),
                ),
            )
            sessions = sorted(
                {
                    r["cal_date"]
                    for r in days
                    if int(r["is_open"]) == 1
                    and datetime.strptime(r["cal_date"], "%Y%m%d").replace(tzinfo=SHANGHAI, hour=15)
                    <= now
                },
                reverse=True,
            )
            classification = self.market.classification()
            master, master_source = self.table("stock_basic", dict(exchange="", list_status="L"))
            for day in sessions[:2]:
                rows, source = self.table("moneyflow", dict(trade_date=day))
                if not rows:
                    continue
                session = datetime.strptime(day, "%Y%m%d").date().isoformat()
                result = daily_projection(rows, master, classification, session)
                result.update(
                    sources=[calendar, master_source, source], retrieved_at=source["retrieved_at"]
                )
                return result
            raise ValueError("published daily funding not ready")

        return self.cached("daily", collect, 600)

    def stock(self, symbol):
        if not SYMBOL.fullmatch(symbol):
            raise ValueError("unsupported funding security")

        def collect():
            groups = self.market.classification()["groups"]
            group = next(
                (g for g in groups if g["kind"] == "industry" and symbol in g["symbols"]), None
            )
            if group is None:
                raise ValueError("funding security outside verified scope")
            packet, source = self.response(
                QQ,
                dict(
                    code=symbol,
                    type="historyFundFlow,todayFundFlow,todayFundTrend",
                    klineNeedDay=20,
                ),
            )
            result = minute_projection(packet, symbol, datetime.now(SHANGHAI))
            daily = self.load("daily") or {}
            name = next(
                (s["name"] for s in daily.get("stocks", []) if s["symbol"] == symbol), symbol[2:]
            )
            result.update(
                sources=[source],
                retrieved_at=source["retrieved_at"],
                industry=group["name"],
                name=name,
            )
            return result

        return self.cached(symbol, collect, 25)

    def cached(self, key, collect, ttl):
        with self.locks.setdefault(key, threading.RLock()):
            last = self.load(key)
            # The file survives restart, but failed reads never get a new quote time.
            age = (
                (datetime.now(UTC) - datetime.fromisoformat(last["retrieved_at"])).total_seconds()
                if last
                else float("inf")
            )
            if age >= ttl and time.monotonic() - self.attempted.get(key, 0) >= 25:
                try:
                    current = collect()
                    self.save(key, current)
                    last, self.errors[key] = current, None
                except (OSError, ValueError, KeyError, TypeError, requests.RequestException):
                    self.errors[key] = "资金数据刷新失败；保留上次有效数据，时间与口径保持原样。"
                self.attempted[key] = time.monotonic()
            if last is None:
                raise ValueError("no verified funding observation")
            result = dict(last, refresh_error=self.errors.get(key))
            now = datetime.now(SHANGHAI)
            stamp = datetime.fromisoformat(result["asof"])
            clock = now.hour * 60 + now.minute
            active = now.weekday() < 5 and (570 <= clock <= 690 or 780 <= clock <= 900)
            result["freshness"] = (
                "DAILY"
                if key == "daily"
                else "DELAYED"
                if result["refresh_error"] or (active and now - stamp > timedelta(minutes=3))
                else "LIVE"
                if active and now.date() == stamp.date()
                else "LATEST_MINUTE"
            )
            return result
