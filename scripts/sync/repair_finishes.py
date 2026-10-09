"""Audit cosmetic source corrections; --apply publishes an immutable data release."""
import argparse
import copy
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from packages.sync.common import read, write, digest
from packages.sync.normalize import normalize, difference
from packages.sync.finishes import refresh
from packages.sync.service import SyncService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    service = SyncService(root=ROOT)
    baseline = service.releases.current()
    manifest = service.releases.verify(baseline)
    catalog = read(service.releases.path(baseline) / 'data/catalog/catalog.json')
    commit = catalog['upstream']['commit']
    snapshot = service.home / 'snapshots' / commit
    normalized = normalize(read(snapshot / 'upstream.json'), read(snapshot / 'source.json')['tree'], service.source_corrections())
    delta = difference(read(snapshot / 'normalized.json'), normalized)
    assert not delta['ruleChanged'] and not delta['imageChanged'] and not delta['relationshipsChanged'], 'Non-cosmetic changes require separate review'
    corrected = copy.deepcopy(catalog)
    report = {'commit': commit, 'baseline': baseline, 'variants': [], 'covers': [], 'ruleChanges': 0}
    for card in corrected['cards']:
        before = copy.deepcopy(card)
        for variant in card.get('variants', []):
            source = normalized['cards'].get(variant['upstreamId'])
            if not source:
                continue
            if (variant.get('finishCode'), variant.get('rarity')) != (source['finishCode'], source['rarity']):
                report['variants'].append({'printingId': card['printingId'], 'name': card['cnName'], 'upstreamId': variant['upstreamId'], 'before': variant.get('finishCode'), 'after': source['finishCode']})
            variant.update(finishCode=source['finishCode'], rarity=source['rarity'])
            if source.get('finishResolution'):
                variant['finishResolution'] = source['finishResolution']
        refresh(card)
        if card.get('images') != before.get('images'):
            report['covers'].append({'printingId': card['printingId'], 'name': card['cnName'], 'before': before.get('images', [])[:1], 'after': card.get('images', [])[:1]})
        assert {k: v for k, v in card.items() if k not in ('variants', 'images')} == {k: v for k, v in before.items() if k not in ('variants', 'images')}
        assert [v['variantId'] for v in card.get('variants', [])] == [v['variantId'] for v in before.get('variants', [])]
    corrected['version'] = digest({k: v for k, v in corrected.items() if k != 'version'})
    report['unresolved'] = [cid for cid, c in normalized['cards'].items() if c.get('finishResolution', {}).get('status') == 'needs-review']
    report['variantCount'] = sum(len(c.get('variants', [])) for c in corrected['cards'])
    report['catalogVersion'] = corrected['version']
    if args.apply and corrected != catalog:
        assert not report['unresolved'], 'Unresolved finish conflicts require review'
        with service.jobs.lease():
            rid = service.releases.archive(catalog=corrected, metadata={'commit': commit, 'kind': 'cosmetic-finish-repair', 'baseline': baseline})
            candidate = service.releases.verify(rid)
            assert candidate['engineVersion'] == manifest['engineVersion']
            assert candidate['effectVersion'] == manifest['effectVersion']
            job = service.jobs.create('finish-repair-' + rid, {'kind': 'cosmetic-finish-repair', 'baseline': baseline, 'commit': commit, 'candidate': rid, 'statuses': {}})
            service.publish(job['id'])
            write(ROOT / 'data/catalog/catalog.json', corrected)
            shutil.copy2(service.releases.path(rid) / 'data/catalog/catalog.sqlite', ROOT / 'data/catalog/catalog.sqlite')
            write(snapshot / 'normalized.json', normalized)
            release = read(ROOT / 'RELEASE.json')
            release['battleReleaseId'] = rid
            write(ROOT / 'RELEASE.json', release)
            report['releaseId'] = rid
    write(ROOT / 'artifacts/sync/finish-repair.json', report)
    service.close()
    print({k: len(v) if isinstance(v, list) else v for k, v in report.items()})


if __name__ == '__main__':
    main()
