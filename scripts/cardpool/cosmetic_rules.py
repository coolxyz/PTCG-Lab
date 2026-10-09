"""Match formatting variants against reviewed bilingual clauses, never new text.

Only presentation markup, type-symbol aliases, whitespace and punctuation are
normalized. Full clauses and printed damage operators remain authoritative.
"""
from functools import lru_cache
from importlib import import_module
from pathlib import Path
import json
import re
import unicodedata


@lru_cache(maxsize=16000)
def key(text):
    text=unicodedata.normalize("NFKC",text).replace("’","'")
    def template(match):
        parts=match[1].split("|")
        name=parts[0].strip().lower()
        if name in ("tcg","tcgpm","dl") and len(parts)>1:return parts[-1].strip()
        if name=="ex" and len(parts)==1:return "ex"
        if name=="e" and len(parts)==2:
            aliases={"草":"grass","火":"fire","水":"water","雷":"lightning","超":"psychic","斗":"fighting","鬥":"fighting","闘":"fighting","恶":"darkness","悪":"darkness","钢":"metal","鋼":"metal","无":"colorless","無":"colorless","无色":"colorless","無色":"colorless"}
            value=parts[1].strip().lower()
            return "{energy:"+aliases.get(value,value)+"}"
        return match[0]
    for _ in range(5):
        new=re.sub(r"\{\{([^{}]+)\}\}",template,text)
        if new==text:break
        text=new
    text=re.sub(r"\[\[([^\[\]]+)\]\]",lambda m:m[1].split("|")[-1],text)
    text=re.sub(r"'{2,}","",text)
    text=re.sub(r"</?nowiki>","",text,flags=re.I)
    # Quotes/brackets and commas carry no conditions or quantities here.
    text=re.sub(r"[\s,，、\[\]【】「」『』“”\"()]", "",text)
    return text.replace("。",".").casefold()


SOURCES={
    "attack":[
        ("attack-additional-clauses.json","attack_additional_rules","compile_additional"),
        ("attack-field-clauses.json","attack_field_rules","compile_field"),
        ("coin-branch-clauses.json","coin_branch_rules","compile_branch"),
        ("conditional-attack-clauses.json","conditional_attack_rules","compile_conditional"),
        ("math-clauses.json","math_clause_rules","compile_math_clause"),
        ("staged-attack-clauses.json","staged_attack_rules","compile_staged"),
    ],
    "ability":[
        ("ability-additional-clauses.json",None,None),
        ("activated-clauses.json","activated_rules","compile_activated"),
        ("entry-clauses.json","entry_rules","compile_entry"),
        ("reactive-clauses.json","reactive_rules","compile_reactive"),
        ("suppression-clauses.json","suppression_rules","compile_suppression"),
    ],
    "tool":[("tool-additional-clauses.json","tool_rules","compile_tool")],
    "stadium":[("stadium-clauses.json","stadium_rules","compile_stadium")],
}


@lru_cache(maxsize=None)
def index(domain):
    result={}
    for filename,module,function in SOURCES[domain]:
        for c in json.loads(Path(__file__).with_name(filename).read_text(encoding="utf8")):
            result.setdefault((key(c["english"]),key(c["chinese"])),[]).append((c,module,function))
    return result


def compile_cosmetic(p,domain="attack"):
    matches=index(domain).get((key(p.get("eeffect","")),key(p.get("effectZHS",""))),[])
    results=[]
    for c,module,function in matches:
        if module is None:
            result=dict(c["rule"])
        else:
            compile_=getattr(import_module("scripts.cardpool."+module),function)
            result=compile_({**p,"eeffect":c["english"],"effectZHS":c["chinese"]}) if domain=="attack" else compile_(c["english"],c["chinese"])
        if result is not None:results.append(result)
    return results[0] if results and all(r==results[0] for r in results) else None
