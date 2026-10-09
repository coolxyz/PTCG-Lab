"""Revision-bound field repairs independently evidenced by official card pages."""

import hashlib
import json
from pathlib import Path

REPAIRS = json.loads(Path(__file__).with_name("source-corrections.json").read_text(encoding="utf8"))


def apply(page, template, fields):
    evidence = []
    for repair in REPAIRS:
        if repair["page"] != page.get("title") or repair["template"] != template:
            continue
        if repair["revision"] != page.get("revision") or repair["sourceSha256"] != hashlib.sha256(page["text"].encode()).hexdigest():
            continue
        if any(fields.get(k) != value for k, value in repair["expected"].items()):
            continue
        fields.update(repair["set"])
        evidence.append(repair)
    return evidence
