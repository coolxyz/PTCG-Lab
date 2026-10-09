"""Generate review candidates, without changing admission or source records."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from packages.sync.common import read, write, digest
from packages.sync.equivalence import text
from collections import defaultdict, Counter
import difflib
import re


def skeleton(s):
    return re.sub(r'\d+', '#', text(s))


def main():
    plain = read('data/cardpool/plain-pokemon.json')
    clauses = defaultdict(list)
    for spec in plain['cards']:
        for a in spec['attacks']:
            if a.get('text'):
                clauses[skeleton(a['text'])].append(a)
    rows = []
    for row in read('artifacts/sync/unmatched-clauses.json'):
        shape = skeleton(row['text'])
        nearest = difflib.get_close_matches(shape, clauses, n=1, cutoff=.45)
        candidates = clauses[nearest[0]] if nearest else []
        rows.append({**row, 'exactShape': shape in clauses, 'candidates': candidates[:3]})
    write('artifacts/sync/clause-review.json', rows)
    print(Counter(r['exactShape'] for r in rows))
    for r in rows:
        if not r['exactShape']:
            print(r['example'],r['text'])
            print('  ->', [(a['text'],a.get('mechanic')) for a in r['candidates'][:1]])


if __name__ == '__main__': main()
