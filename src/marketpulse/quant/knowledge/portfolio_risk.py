"""Same-calendar adjusted daily-return covariance; no price-level correlations."""

from decimal import Decimal, localcontext

from .features import number

WINDOW = 60
VERSION = "portfolio-common-returns-v1"


def correlations(stocks, sessions, threshold):
    ids = sorted(stocks)
    result = dict(
        version=VERSION,
        status="MISSING",
        sample_count=0,
        sessions=[],
        ids=ids,
        matrix=[[None for _ in ids] for _ in ids],
        pairs=[],
        groups=[],
        covariance=None,
        reason=None,
    )
    required = sorted(set(sessions))[-(WINDOW + 1) :]
    if len(required) != WINDOW + 1:
        result["reason"] = "缺少六十一个共同连续交易日的日历，不能计算六十日收益相关性。"
        return result
    values = {}
    for sid, chart in stocks.items():
        dates = [p["date"] for p in chart]
        if dates != sorted(set(dates)):
            raise ValueError("相关性输入日期重复或乱序。")
        points = {p["date"]: p["close"] for p in chart}
        if any(d not in points or points[d] is None for d in required):
            result["reason"] = sid + " 缺少共同窗口价格，未删缺点日或缩短窗口。"
            return result
        close = [number(points[d]) for d in required]
        if any(p <= 0 for p in close):
            raise ValueError("相关性价格必须为有限正数。")
        values[sid] = [close[i] / close[i - 1] - 1 for i in range(1, len(close))]
    with localcontext() as ctx:
        ctx.prec = 34
        centered = {s: [v - sum(x) / WINDOW for v in x] for s, x in values.items()}
        covariance = [
            [
                sum(a * b for a, b in zip(centered[s], centered[t], strict=True)) / (WINDOW - 1)
                for t in ids
            ]
            for s in ids
        ]
        matrix = []
        for i, _sid in enumerate(ids):
            row = []
            for j, _other in enumerate(ids):
                denom = (covariance[i][i] * covariance[j][j]).sqrt()
                row.append(
                    str(max(Decimal(-1), min(Decimal(1), covariance[i][j] / denom)))
                    if denom
                    else None
                )
            matrix.append(row)
        pairs, edges = [], {s: set() for s in ids}
        for i, s in enumerate(ids):
            for j in range(i + 1, len(ids)):
                value = matrix[i][j]
                linked = value is not None and number(value) >= threshold
                pairs.append(dict(left=s, right=ids[j], value=value, linked=linked))
                if linked:
                    edges[s].add(ids[j])
                    edges[ids[j]].add(s)
        groups, seen = [], set()
        for s in ids:
            if s in seen:
                continue
            pending, members = [s], set()
            while pending:
                v = pending.pop()
                if v in members:
                    continue
                members.add(v)
                pending.extend(edges[v] - members)
            seen.update(members)
            if len(members) > 1:
                groups.append(sorted(members))
        result.update(
            status="COMPUTED",
            sample_count=WINDOW,
            sessions=required[1:],
            matrix=matrix,
            pairs=pairs,
            groups=groups,
            covariance=[[str(v) for v in row] for row in covariance],
            reason="部分零方差序列相关性不可计算，未填零。"
            if any(matrix[i][i] is None for i in range(len(ids)))
            else None,
        )
    return result


def volatility(risk, weights):
    if risk["covariance"] is None:
        return None
    with localcontext() as ctx:
        ctx.prec = 34
        variance = sum(
            weights.get(s, Decimal(0))
            * weights.get(t, Decimal(0))
            * number(risk["covariance"][i][j])
            for i, s in enumerate(risk["ids"])
            for j, t in enumerate(risk["ids"])
        )
        return str(max(Decimal(0), variance * 252).sqrt())
