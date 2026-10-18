"""CJK bigram / English token projection; source character locators stay unchanged."""

import re


def tokens(value: str) -> list[str]:
    result = []
    for word in re.findall(r"[\u3400-\u9fff]+|[a-z0-9]+", value.lower()):
        result.extend(
            [word]
            if len(word) == 1 or word.isascii()
            else [word[i : i + 2] for i in range(len(word) - 1)]
        )
    return list(dict.fromkeys(result))


def searchable(document: dict, normalized: str) -> str:
    definition = document["source"]
    return " ".join(
        tokens(
            " ".join(
                [
                    definition["title"],
                    *definition["authors"],
                    *definition["tags"],
                    definition["category"],
                    definition["reading_note"],
                    normalized,
                ]
            )
        )
    )
