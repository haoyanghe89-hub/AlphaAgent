"""Fixed-source, bounded transport with immutable request/response checkpoints."""

import json
import re
import time
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit

import requests

from marketpulse.infrastructure.storage.models import BlobRef

from ..contracts import digest
from ..live.securities import PROFILE, SMARTBOX, choose, financial_type, parse_candidates

HOSTS = {
    "gov.cn",
    "www.gov.cn",
    "www.miit.gov.cn",
    "www.mofcom.gov.cn",
    "kjs.mof.gov.cn",
    "wap.miit.gov.cn",
    "www.bis.gov",
    "www.ecfr.gov",
    "www.expectationsinvesting.com",
    "www.berkshirehathaway.com",
    "www.oaktreecapital.com",
    "johncbogle.com",
    "www.aqr.com",
    "get.investors.com",
    "pages.stern.nyu.edu",
    "collabfund.com",
    "mba.tuck.dartmouth.edu",
    "www.wsts.org",
    "www.semi.org",
    "qt.gtimg.cn",
    "hq.sinajs.cn",
    "www.sse.com.cn",
    "investor.szse.cn",
    "docs.static.szse.cn",
    "www.cninfo.com.cn",
    "static.cninfo.com.cn",
    "smartbox.gtimg.cn",
    "emweb.securities.eastmoney.com",
    "proxy.finance.qq.com",
    "datacenter-web.eastmoney.com",
    "push2his.eastmoney.com",
    "push2.eastmoney.com",
    "query.sse.com.cn",
    "api.tushare.pro",
    "api.fund.eastmoney.com",
}


class Stopped(ValueError):
    pass


class SourceUnavailable(ValueError):
    pass


class SourceClient:
    def __init__(self, repository, state, save, stop, *, max_requests=20, budget_profile=None):
        self.repo, self.state, self.save, self.stop = repository, state, save, stop
        self.max_requests = max_requests
        self.deadline = time.monotonic() + max(
            0,
            (budget_profile or state["request"]["budget_profile"])["max_seconds"]
            - state.get("elapsed_seconds", 0),
        )

    def check(self):
        if self.stop.is_set():
            raise Stopped("取证已停止")
        if time.monotonic() >= self.deadline:
            raise Stopped("取证达到时间上限")

    def fetch(self, key, url, *, params=None, data=None, family, encoding=None, binary=False):
        for attempt in range(2):
            try:
                return self._fetch_once(
                    key,
                    url,
                    params=params,
                    data=data,
                    family=family,
                    encoding=encoding,
                    binary=binary,
                )
            except SourceUnavailable as error:
                # These fixed endpoints only read data, including the catalog POST.
                # Keep each failed attempt and charge it to the same task budget.
                if attempt or not isinstance(
                    error.__cause__, (requests.Timeout, requests.ConnectionError)
                ):
                    raise

    def _fetch_once(self, key, url, *, params=None, data=None, family, encoding=None, binary=False):
        self.check()
        definition = dict(tool=key, url=url, params=params, data=data, family=family)
        cache_key = digest(definition)
        for previous in self.state["calls"]:
            if previous.get("cache_key") == cache_key and previous["status"] == "COMPLETED":
                raw = self.repo.raw_bytes(previous["response_ref"])
                return previous["source"], self.decode(raw, encoding, binary)
        if len(self.state["calls"]) >= self.max_requests:
            raise SourceUnavailable(f"达到本任务{self.max_requests}次来源请求上限（含失败）")
        request_ref = self.repo.blob(definition)
        call = dict(
            tool=key,
            cache_key=cache_key,
            status="RUNNING",
            request_ref=request_ref,
            started_at=datetime.now(UTC).isoformat(),
        )
        self.state["calls"].append(call)
        self.save()
        try:
            is_tushare = url == "https://api.tushare.pro"
            if is_tushare:
                from . import tushare

                if hit := tushare.cached(self.repo, definition):
                    source, raw = hit
                    self.check()
                    call.update(
                        status="COMPLETED",
                        response_ref=source["blob_ref"],
                        source=source,
                        cache_hit=True,
                        finished_at=datetime.now(UTC).isoformat(),
                    )
                    self.save()
                    return source, self.decode(raw, encoding, binary)
                private_body = tushare.request_body(definition)
            target = url
            for _ in range(4):
                self.check()
                parsed = urlsplit(target)
                if (
                    parsed.scheme != "https"
                    or parsed.hostname not in HOSTS
                    or parsed.username
                    or parsed.port not in (None, 443)
                ):
                    raise SourceUnavailable("未批准的来源 URL")
                with requests.request(
                    "POST" if data is not None else "GET",
                    target,
                    params=params,
                    **({"json": private_body} if is_tushare else {"data": data}),
                    headers={
                        "User-Agent": "Mozilla/5.0",
                        "Referer": "https://finance.sina.com.cn/"
                        if parsed.hostname == "hq.sinajs.cn"
                        else "https://www.sse.com.cn/"
                        if parsed.hostname == "query.sse.com.cn"
                        else "https://fundf10.eastmoney.com/"
                        if parsed.hostname == "api.fund.eastmoney.com"
                        else "https://quote.eastmoney.com/"
                        if parsed.hostname in {"push2his.eastmoney.com", "push2.eastmoney.com"}
                        else "https://www.cninfo.com.cn/",
                    },
                    timeout=(5, min(15, max(1, self.deadline - time.monotonic()))),
                    stream=True,
                    allow_redirects=False,
                ) as response:
                    if response.is_redirect:
                        if is_tushare:
                            raise ValueError("Tushare credential requests cannot redirect")
                        target, params, data = (
                            urljoin(response.url, response.headers["Location"]),
                            None,
                            None,
                        )
                        continue
                    response.raise_for_status()
                    body = bytearray()
                    for chunk in response.iter_content(65536):
                        self.check()
                        body.extend(chunk)
                        if len(body) > (24 if binary else 5) * 1024 * 1024:
                            raise SourceUnavailable("来源响应超过大小上限")
                    raw = bytes(body)
                    final_url = response.url
                    break
            else:
                raise SourceUnavailable("来源重定向过多")
            self.check()
            if is_tushare:
                tushare.validate_response(raw, private_body["token"])
            raw_ref = self.repo.raw(raw)
            source = dict(
                key=key,
                url=final_url,
                source_family=family,
                blob_ref=raw_ref,
                sha256=BlobRef.from_uri(raw_ref).sha256,
                fetched_at=datetime.now(UTC).isoformat(),
                byte_count=len(raw),
            )
            if is_tushare:
                source.update(
                    request_ref=request_ref, api_name=data["api_name"], parameters=data["params"]
                )
            value = self.decode(raw, encoding, binary)
            if is_tushare:
                tushare.remember(self.repo, definition, source)
            call.update(
                status="COMPLETED",
                response_ref=raw_ref,
                source=source,
                finished_at=source["fetched_at"],
            )
            self.save()
            return source, value
        except Stopped:
            raise
        except (requests.RequestException, ValueError, KeyError, UnicodeError) as error:
            call.update(
                status="FAILED",
                error=type(error).__name__,
                finished_at=datetime.now(UTC).isoformat(),
            )
            self.save()
            raise SourceUnavailable(
                f"{key} 本次未取得有效响应（{type(error).__name__}）"
            ) from error

    @staticmethod
    def decode(raw, encoding, binary):
        return raw if binary else raw.decode(encoding) if encoding else json.loads(raw)


def resolve(client, entity):
    query = entity.strip()
    match = re.fullmatch(r"(\d{6})\.(SH|SZ)", query.upper())
    if match:
        query = match[1]
    _, text = client.fetch(
        "security_search",
        SMARTBOX,
        params={"q": query, "t": "all"},
        family="TENCENT",
        encoding="gb18030",
    )
    subject = choose(parse_candidates(text), query)
    _, profile = client.fetch(
        "profile", PROFILE, params={"code": subject["symbol"].upper()}, family="EASTMONEY_PROFILE"
    )
    rows = profile.get("jbzl") or []
    if len(rows) != 1 or rows[0].get("SECURITY_CODE") != subject["code"]:
        raise ValueError("公司资料与证券身份不一致")
    row = rows[0]
    industry = row.get("EM2016") or row.get("INDUSTRYCSRC1")
    if not industry or "A股" not in row.get("SECURITY_TYPE", ""):
        raise ValueError("缺少可核对的沪深A股身份与行业")
    subject.update(
        name=row["SECURITY_NAME_ABBR"],
        industry=industry,
        company_type=financial_type(industry),
        description=str(row.get("ORG_PROFILE") or "")[:1500],
        security_id=subject["code"] + (".SH" if subject["exchange"] == "SSE" else ".SZ"),
    )
    return subject
