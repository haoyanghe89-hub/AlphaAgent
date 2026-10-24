"""Literal numeric quotations derived locally from sealed, stock-bound evidence."""

import re
from decimal import ROUND_HALF_UP, Decimal, DecimalException, localcontext

VERSION = "evidence-numbers-v1"
GUIDANCE = (
    "数字引用规则evidence-numbers-v1：仅可逐字复制numeric_quotes中列出的表达，"
    "每项判断只能使用其evidence_ids对应的表达，不得借用其他证券或其他指标的同值数字。"
    "数值必须连同指标名称和单位一起引用，不得自行计算、换算、改变正负或精度。"
    "摘要只能复述本证券判断已引用的事实；失效条件和后续检查只可引用route_numeric_quotes中的原窗口。"
    "不在列表中的数字改用定性说法，不能改写成中文数量；概率、收益承诺和新增交易参数仍禁止。"
)


def enabled(context):
    version = context.get("numeric_grounding")
    if version is not None and version != VERSION:
        raise ValueError("未知数字引用校验版本")
    return version == VERSION


def quotes(fact, windows=None):
    """A raw equal number is insufficient: values retain their metric label and unit."""
    result = set()
    key = fact.get("metric_key", "")
    if not key and fact["evidence_id"].startswith(("F-", "T-")):
        key = fact["evidence_id"].split("-", 2)[-1]
    for count in re.findall(r"(?:^|_)(\d+)(?=_|$)", key):
        result.update((count + "日", count + "个交易日"))
    window = fact.get("window")
    if isinstance(window, str) and windows:
        window = windows.get(window, window)
    if isinstance(window, dict) and window.get("benchmark_id") == "000300.SH":
        result.add("沪深300")
    if isinstance(window, dict) and re.fullmatch(r"\d{8}", str(window.get("end", ""))):
        result.add("截至" + window["end"])
    label = fact.get("label", "")
    if not label and key:
        from .research_context import LABELS

        label = LABELS.get(key, "")
    if label:
        result.update(re.findall(r"(?:RSI|ATR|MA)\d+", label))
        for count in re.findall(r"(\d+)(?:个交易日|日)", label):
            result.update((count + "日", count + "个交易日"))
    unit = fact.get("unit")
    suffix = {"CNY/share": "元", "CNY": "元", "CNY/session": "元/交易日", "multiple": "倍"}.get(
        unit
    )
    try:
        raw = str(fact.get("value", ""))
        if len(raw) > 64:
            return sorted(result)
        value = Decimal(raw)
        if not value.is_finite() or abs(value.adjusted()) > 60 or not label:
            return sorted(result)
        if unit == "ratio":
            # This display conversion is supplied by the program, never computed by the model.
            with localcontext() as precision:
                precision.prec = max(28, len(raw) + 6)
                value = (value * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            suffix = "%"
        if suffix:
            number = format(value, "f")
            if "." in number:
                number = number.rstrip("0").rstrip(".")
            result.add(label + number + suffix)
    except DecimalException:
        pass
    return sorted(result)


def route_quotes(stock):
    result = set()
    for route in stock.get("routes", []):
        texts = [route.get("invalidation", ""), *(c["label"] for c in route.get("conditions", []))]
        for text in texts:
            # Only existing route window words can be copied; prices and share amounts cannot.
            result.update(re.findall(r"\d+(?:个交易日|日)", text))
    return sorted(result)


def analyst_prompt(text):
    replacements = (
        (
            "正文只写定性判断，不出现阿拉伯数字、日期、代码、比率、金额、股数或概率；引用ID只放专用字段。",
            "正文以定性判断为主；引用ID只放专用字段。",
        ),
        (
            "不要把数值改写成中文数量。所有价格、窗口与仓位由本地工具填充，你不能计算或修改。",
            "不得自行计算或修改数值与交易参数。",
        ),
        (
            "正文不写窗口数量，使用参考窗口、均线、近期等定性说法，交易日期限由页面工具填充。",
            "窗口数字也须绑定封存表达，交易日期限由页面工具填充。",
        ),
        (
            "正文使用动量、相对强弱、波动等定性词，不写指标窗口数量或指标名称中的数字。",
            "指标及窗口数字仅可引用封存表达，其余使用定性说法。",
        ),
        (
            "简洁中文；正文不写数字、日期、代码、概率、中文数量或无条件交易指令。",
            "简洁中文；禁止无来源的数字、概率、中文数量或无条件交易指令。",
        ),
        (
            "简洁定性中文，不写数字、中文数量、日期、代码、价格、概率或无条件交易指令；引用ID只放专用字段。",
            "简洁中文；引用ID只放专用字段，禁止无来源数字、概率或无条件交易指令。",
        ),
    )
    for before, after in replacements:
        text = text.replace(before, after)
    return text + GUIDANCE
