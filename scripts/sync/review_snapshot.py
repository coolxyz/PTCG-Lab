"""Prepare current-scope rules from a pinned snapshot; never activate a release.

Exact complete reviewed faces can reuse an existing implementation. Otherwise
the closed clause compiler must account for every printed field. The result is
still a candidate requiring directed tests, full regression and 1,000 games.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import copy
import json
from pathlib import Path
import sys
from datetime import date

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from packages.sync.common import digest, read, write  # noqa: E402
from packages.sync.normalize import normalize  # noqa: E402
from packages.sync.http_source import HTTPSource  # noqa: E402
from packages.sync.adapt import environment_status  # noqa: E402
from scripts.sync.compile_candidates import Compiler  # noqa: E402
from scripts.sync.review_support import norm  # noqa: E402


def face_key(value):
    if isinstance(value, dict):
        return {k: face_key(v) for k, v in value.items()}
    if isinstance(value, list):
        return [face_key(v) for v in value]
    return norm(value) if isinstance(value, str) else value


def reviewed_matches(face, proofs):
    key = digest(face_key(face))
    return [(rh, p) for rh, p in proofs.items() if digest(face_key(p['face'])) == key]


def prepare(root, snapshot, as_of):
    date.fromisoformat(as_of)
    root, snapshot = Path(root), Path(snapshot)
    source = read(snapshot / 'source.json')
    commit = source['commit']
    HTTPSource.validate_sha(commit)
    raw = read(snapshot / 'upstream.json')
    normalized = normalize(raw, source['tree'], read(root / 'data/sync/source-corrections.json'))
    catalog = read(root / 'data/catalog/catalog.json')
    plain = read(root / 'data/cardpool/plain-pokemon.json')
    effects = read(root / 'data/simulation/effects.json')
    implementations = read(root / 'data/sync/implementations.json')
    equivalence = read(root / 'data/sync/reviewed-equivalence.json')
    proofs = equivalence['proofs']
    index = defaultdict(list)
    for rh, proof in proofs.items():
        index[digest(face_key(proof['face']))].append((rh, proof))
    known = {e['effectKey']: e for e in effects['effects']}
    specs = {s['effectKey']: s for k in ('cards', 'trainers', 'specialEnergies') for s in plain[k]}
    accepted = defaultdict(set)
    for card in catalog['cards']:
        if card.get('sourceVerified') and card.get('effectStatus') == 'verified' and card.get('engineId') in known:
            for cid, evidence in card.get('upstreamFaces', {}).items():
                accepted[cid].add(evidence['ruleHash'])
    compiler = Compiler(
        plain, catalog, reviewed=read(root / 'data/sync/reviewed-clauses.json')['clauses'],
        species=read(root / 'data/cardpool/species-names.json')['species'],
        evolution=read(root / 'data/sync/evolution-evidence.json')['cards'],
    )
    rules = read(root / 'rulesets/cn-standard-2026-09-16.json')
    scope = read(root / 'data/cardpool/battle-scope.json')
    dates = defaultdict(list)
    for row in normalized['occurrences']:
        released_at = normalized['products'][row['productId']]['releasedAt']
        if released_at:
            dates[row['cardId']].append(released_at)
    rows, gaps = [], []
    for cid, up in normalized['cards'].items():
        f = up['face']
        basic = f['category'] == '能量' and f['energyType'] == '基本能量'
        candidate = {
            'printingId': '', 'releasedAt': min(dates[cid]) if dates[cid] else None,
            'mark': up['mark'], 'basicEnergyType': {'DARK': 'darkness', 'METAL': 'metal'}.get(f['type'], (f['type'] or '').lower()) if basic else None,
        }
        if environment_status(candidate, rules, scope, as_of) != 'eligible':
            continue
        previous = implementations['cards'].get(cid)
        if previous and previous['ruleHash'] == up['ruleHash']:
            continue
        if up['ruleHash'] in accepted[cid]:
            continue
        if up['issues']:
            gaps.append({'cardId': cid, 'name': f['name'], 'errors': up['issues']})
            continue
        matches = [(rh, p) for rh, p in index[digest(face_key(f))] if p['effectKey'] in known]
        keys = {p['effectKey'] for _, p in matches}
        if len(keys) > 1:
            # Duplicate generated effects are equivalent only when the complete
            # runtime definition (including lineage) agrees, excluding evidence.
            def executable(k):
                return {n: v for n, v in specs.get(k, {}).items() if n not in {
                    'effectKey', 'printings', 'sourceRuleHash', 'sourceEvidence', 'sourceAdapter',
                }}
            if all(k in specs for k in keys) and len({digest(executable(k)) for k in keys}) == 1:
                matches = sorted(matches, key=lambda x: x[1]['effectKey'])[:1]
            else:
                gaps.append({'cardId': cid, 'name': f['name'], 'errors': ['ambiguous-reviewed-face']})
                continue
        if matches:
            origin, original = matches[0]
            key = original['effectKey']
            proof = {**copy.deepcopy(original), 'face': f, 'cardId': cid,
                     'sourceHash': up['sourceHash'], 'commit': commit,
                     'originRuleHash': origin, 'reviewMethod': 'complete-reviewed-face-reuse'}
        elif f['category'] == '宝可梦':
            spec, errors = compiler.pokemon(up)
            if not spec:
                gaps.append({'cardId': cid, 'name': f['name'], 'errors': errors})
                continue
            key = spec['effectKey']
            if key not in specs:
                plain['cards'].append(spec)
                specs[key] = spec
            known.setdefault(key, {
                'effectKey': key, 'name': spec['name'], 'printings': [],
                'status': 'experimental', 'registered': True,
                'mechanisms': sorted({a.get('mechanic', {}).get('kind', 'attack_damage') for a in spec['attacks']}),
                'implementation': 'packages.rules.plain.PlainPokemon',
                'rulesRevision': as_of, 'scope': 'reviewed-source-clauses',
            })
            proof = {'face': f, 'cardId': cid, 'sourceHash': up['sourceHash'], 'commit': commit,
                     'effectKey': key, 'method': 'reviewed-clause-composition',
                     'clauseEvidence': spec['sourceEvidence']}
        else:
            gaps.append({'cardId': cid, 'name': f['name'], 'errors': ['complete-reviewed-face-required']})
            continue
        proofs[up['ruleHash']] = proof
        effect = known[key]
        set_name, number = key.split('-', 1)
        implementations['cards'][cid] = {
            'ruleHash': up['ruleHash'], 'effectKey': key,
            'engineLine': f"{effect['name']} {set_name} {number}",
            'testFiles': ['tests/sync/test_reviewed_support.py', 'tests/cardpool/test_upstream_october.py'],
        }
        rows.append({'cardId': cid, 'name': f['name'], 'effectKey': key, 'method': proof['method']})
    effects['effects'] = list(known.values())
    return {
        'schema': 1, 'commit': commit, 'repository': source['repository'], 'asOf': as_of,
        'preparedVariants': len(rows), 'gaps': gaps, 'rows': rows, 'activated': False,
    }, {
        'data/cardpool/plain-pokemon.json': plain, 'data/simulation/effects.json': effects,
        'data/sync/implementations.json': implementations, 'data/sync/reviewed-equivalence.json': equivalence,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', required=True, type=Path)
    parser.add_argument('--as-of', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--apply-candidates', action='store_true')
    args = parser.parse_args()
    report, files = prepare(ROOT, args.snapshot, args.as_of)
    write(args.output, report)
    if args.apply_candidates:
        if report['gaps']:
            raise SystemExit('Unresolved current-scope faces; candidates were not applied')
        for name, value in files.items():
            write(ROOT / name, value)
    print(json.dumps({k: v for k, v in report.items() if k != 'rows'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
