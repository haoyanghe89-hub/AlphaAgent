"""Versioned self-written method notes linked to bounded original-source captures."""

import copy
import hashlib
import json
import re
from datetime import datetime
from html.parser import HTMLParser
from importlib.resources import files

from .contracts import SourceCapability
from .tools import SourceUnavailable

VERSION = "knowledge-public-notes-2026-10-13-v1"


class Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def text(raw):
    parser = Text()
    parser.feed(raw.decode("utf8", errors="replace"))
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def catalog():
    return json.loads(
        files(__package__).joinpath("knowledge_notes.json").read_text(encoding="utf8")
    )


def _locators(raw, note):
    original = text(raw)
    locators = []
    for anchor in note["source_locator"]["anchors"]:
        start = original.find(anchor)
        if start < 0:
            raise ValueError("method source no longer contains the reviewed anchor")
        locators.append(
            dict(
                path="normalized-html-text-v1", start=start, end=start + len(anchor), anchor=anchor
            )
        )
    return locators


def collect(repo, client, subject):
    result = dict(schema_version=VERSION, subject_id=subject["security_id"], responses=[], gaps=[])
    for note in catalog():
        try:
            source, _ = client.fetch(
                "knowledge_" + note["note_id"],
                note["source_url"],
                family="METHOD_KNOWLEDGE",
                encoding="utf8",
            )
            locators = _locators(repo.raw_bytes(source["blob_ref"]), note)
            result["responses"].append(dict(note=note, source=source, locators=locators))
        except (SourceUnavailable, ValueError) as error:
            result["gaps"].append(dict(note_id=note["note_id"], reason=str(error)[:180]))
    return result


def _notes(repo, data, snapshot):
    if snapshot["schema_version"] != VERSION:
        raise ValueError("unsupported method knowledge version")
    cutoff = datetime.fromisoformat(data["bundle"]["cutoff_at"])
    result, ids = [], set()
    # Store the full versioned note in each snapshot. Later catalogs cannot rewrite it.
    for entry in snapshot["responses"]:
        note, source = entry["note"], entry["source"]
        if (
            note["note_id"] in ids
            or note.get("coverage") != "PUBLIC_EXCERPT_VERIFIED"
            or note.get("review_identity") != "AI_METHOD_REVIEWED"
        ):
            raise ValueError("duplicate or unsupported method-note provenance")
        ids.add(note["note_id"])
        raw = repo.raw_bytes(source["blob_ref"])
        if (
            hashlib.sha256(raw).hexdigest() != source["sha256"]
            or datetime.fromisoformat(source["fetched_at"]) > cutoff
            or source["url"] != note["source_url"]
        ):
            raise ValueError("method note source or cutoff mismatch")
        locators = _locators(raw, note)
        if locators != entry["locators"]:
            raise ValueError("method note locator differs")
        result.append(
            dict(
                note,
                raw_ref=source["blob_ref"],
                source_hash=source["sha256"],
                fetched_at=source["fetched_at"],
                actual_locators=locators,
                read_scope="本次核对所列公开方法段落，未读取整书",
                validation_status="UNVALIDATED",
            )
        )
    return result


def attach(repo, data, snapshot):
    data = copy.deepcopy(data)
    notes = _notes(repo, data, snapshot)
    data["knowledge_snapshot"] = copy.deepcopy(snapshot)
    data["knowledge_snapshot"]["sealed_ref"] = repo.blob(snapshot)
    data["knowledge_notes"] = notes
    sources = [entry["source"] for entry in snapshot["responses"]]
    data["sources"] = list({s["blob_ref"]: s for s in data["sources"] + sources}.values())
    if sources:
        source = sources[0]
        data["bundle"]["sources"].append(
            SourceCapability(
                source_id="method-knowledge-v1",
                name="作者公开方法资料",
                url=source["url"],
                source_family="METHOD_KNOWLEDGE",
                acquisition="固定原始URL、有界取数与字符定位",
                authentication="公开读取",
                license_note="方法为自写笔记；原始资料只在本机私有归档",
                coverage="三篇公开资料的已定位方法段落，不是整书阅读",
                frequency="每次研究",
                tested_at=source["fetched_at"],
                status="PARTIAL",
                reason="方法定义不是当前公司事实或投资有效性证明",
                raw_ref=source["blob_ref"],
                content_hash=source["sha256"],
            ).model_dump(mode="json")
        )
    return data


def verify(repo, data):
    snapshot = copy.deepcopy(data["knowledge_snapshot"])
    sealed = snapshot.pop("sealed_ref")
    if snapshot != repo.value(sealed):
        raise ValueError("method knowledge snapshot differs from its sealed definition")
    subject_ids = {e["subject_id"] for e in data["bundle"]["items"]}
    if snapshot["subject_id"] not in subject_ids or data["knowledge_notes"] != _notes(
        repo, data, snapshot
    ):
        raise ValueError("method notes differ from sealed sources or subject")


def model_context(data, note_ids=None):
    notes = data.get("knowledge_notes", [])
    if note_ids is not None and not set(note_ids) <= {n["note_id"] for n in notes}:
        raise ValueError("unknown sealed method note")
    selected = [n for n in notes if note_ids is None or n["note_id"] in note_ids]
    return dict(
        version=VERSION,
        notes=[
            {
                k: n[k]
                for k in (
                    "note_id",
                    "title",
                    "authors",
                    "coverage",
                    "summary",
                    "steps",
                    "forbidden_inferences",
                    "implementation_status",
                )
            }
            for n in selected
        ],
        gaps=[g["note_id"] for g in data.get("knowledge_snapshot", {}).get("gaps", [])],
        scope="方法笔记仅为研究规则，不是当前公司事实；未启用估值求解与因子组合，不增加事实来源数。",
    )
