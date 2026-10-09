"""Extract the explicitly supplied Chinese variant without translating rules."""

import re


def chinese_effect(p):
    text = p.get("effectZHS") or p.get("effectZHT") or p.get("ceffect", "")
    return re.sub(r"-\{zh-hans:(.*?);zh-hant:.*?\}-", lambda m: m[1], text, flags=re.S)
