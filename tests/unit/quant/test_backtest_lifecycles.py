"""Synthetic exchange files; never used by real preview jobs."""

import json
from io import BytesIO
from xml.sax.saxutils import escape
from zipfile import ZipFile

import pytest

from marketpulse.quant.backtest.lifecycles import endpoint, extract_lifecycles


def workbook(headers, rows):
    stream = BytesIO()
    with ZipFile(stream, "w") as book:
        cells = []
        for ordinal, row in enumerate([headers, *rows], 1):
            cells.append(
                '<row r="'
                + str(ordinal)
                + '">'
                + "".join(
                    f'<c r="{chr(65 + i)}{ordinal}" t="inlineStr">'
                    f"<is><t>{escape(str(v))}</t></is></c>"
                    for i, v in enumerate(row)
                )
                + "</row>"
            )
        book.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
            + "".join(cells)
            + "</sheetData></worksheet>",
        )
    return stream.getvalue()


def document(table):
    if table.startswith("sh_"):
        code = {"sh_main": "600001", "sh_star": "688001", "sh_retired": "600003"}[table]
        rows = [
            dict(
                A_STOCK_CODE=code,
                STOCK_TYPE="8" if table == "sh_star" else "1",
                COMPANY_ABBR="TEST ONLY",
                LIST_DATE="19910403",
                DELIST_DATE="20230101" if table.endswith("retired") else "-",
            )
        ]
        return json.dumps(dict(result=rows, pageHelp=dict(total=1))).encode()
    if table == "sz_active":
        return workbook(
            ["A股代码", "A股简称", "A股上市日期"], [["000001", "TEST ONLY", "1991-04-03"]]
        )
    return workbook(
        ["证券代码", "证券简称", "上市日期", "终止上市日期"],
        [
            ["000003", "TEST ONLY", "1991-04-03", "2023-01-01"],
            ["200003", "TEST B ONLY", "1991-04-03", "2023-01-01"],
        ],
    )


@pytest.mark.parametrize("table", ["sh_main", "sh_star", "sh_retired", "sz_active", "sz_retired"])
def test_fixed_exchange_extraction(table):
    result = extract_lifecycles(document(table), dict(kind="official_lifecycle", table=table))
    assert len(result["rows"]) == 1
    assert result["rows"][0]["listed_at"] == "1991-04-03"
    assert bool(result["rows"][0]["delisted_at"]) == table.endswith("retired")
    assert result["excluded_non_a_share"] == int(table == "sz_retired")


@pytest.mark.parametrize("change", ["empty", "truncated", "duplicate", "bad_date", "wrong_active"])
def test_exchange_refuses_ambiguous_or_invalid_response(change):
    query = dict(kind="official_lifecycle", table="sh_main")
    raw = json.loads(document("sh_main"))
    if change == "empty":
        raw["result"] = None
    if change == "truncated":
        raw["pageHelp"]["total"] = 2
    if change == "duplicate":
        raw["result"] *= 2
        raw["pageHelp"]["total"] = 2
    if change == "bad_date":
        raw["result"][0]["LIST_DATE"] = "19910230"
    if change == "wrong_active":
        raw["result"][0]["DELIST_DATE"] = "20230101"
    with pytest.raises((ValueError, TypeError)):
        extract_lifecycles(json.dumps(raw).encode(), query)


def test_fixed_queries_and_xlsx_schema_cannot_be_overridden():
    with pytest.raises(ValueError):
        endpoint(dict(kind="official_lifecycle", table="sz_active", url="https://arbitrary"))
    with pytest.raises(KeyError):
        extract_lifecycles(
            workbook(["证券代码"], [["000001"]]), dict(kind="official_lifecycle", table="sz_active")
        )


def test_dual_listing_b_row_is_not_an_a_share_lifecycle():
    raw = json.loads(document("sh_retired"))
    raw["result"].append({**raw["result"][0], "STOCK_TYPE": "2", "LIST_DATE": "19920101"})
    raw["pageHelp"]["total"] = 2
    result = extract_lifecycles(
        json.dumps(raw).encode(), dict(kind="official_lifecycle", table="sh_retired")
    )
    assert len(result["rows"]) == 1 and result["excluded_non_a_share"] == 1
    assert result["rows"][0]["listed_at"] == "1991-04-03"


def test_xlsx_shared_strings_and_numeric_dates():
    stream = BytesIO()
    with ZipFile(stream, "w") as book:
        book.writestr(
            "xl/sharedStrings.xml",
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            + "".join(
                "<si><t>" + v + "</t></si>"
                for v in ["A股代码", "A股简称", "A股上市日期", "000001", "TEST ONLY"]
            )
            + "</sst>",
        )
        book.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData><row><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c>'
            '<c r="C1" t="s"><v>2</v></c></row><row><c r="A2" t="s"><v>3</v></c>'
            '<c r="B2" t="s"><v>4</v></c><c r="C2"><v>33331</v></c></row>'
            "</sheetData></worksheet>",
        )
    result = extract_lifecycles(
        stream.getvalue(), dict(kind="official_lifecycle", table="sz_active")
    )
    assert result["rows"][0]["listed_at"] == "1991-04-03"
