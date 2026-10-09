"""Server-owned admission catalog. Untrusted clients submit IDs/quantities only."""
import copy
from packages.engine_adapter.cn_format import validate


class AdmissionError(ValueError):
    pass


class Catalog:
    def __init__(self, printings):
        self.printings = copy.deepcopy(printings)

    def resolve(self, entries):
        resolved = []
        for entry in entries:
            if set(entry) != {'printingId', 'quantity'}:
                raise AdmissionError('CLIENT_METADATA_FORBIDDEN')
            card = self.printings.get(entry['printingId'])
            if card is None:
                raise AdmissionError('UNKNOWN_PRINTING')
            resolved.append({'quantity': entry['quantity'], 'printing': copy.deepcopy(card)})
        return resolved

    def admit(self, entries, as_of='2026-09-30'):
        resolved = self.resolve(entries)
        result = validate(resolved, as_of)
        if not result['playable']:
            raise AdmissionError(str(result))
        lines = []
        for entry in resolved:
            card = entry['printing']
            if not card.get('engineLine') or not card.get('effectEvidence'):
                raise AdmissionError('ENGINE_MAPPING_UNREVIEWED')
            lines.append(f"{entry['quantity']} {card['engineLine']}")
        return lines

    def create_match(self, deck1, deck2, seed=0, as_of='2026-09-30'):
        from packages.rules.adapter import Adapter
        from packages.engine_adapter.base import digest
        provenance = {'catalogVersion': digest(self.printings), 'formatAsOf': as_of,
                      'deck1': copy.deepcopy(deck1), 'deck2': copy.deepcopy(deck2)}
        return Adapter(seed, self.admit(deck1, as_of), self.admit(deck2, as_of), provenance=provenance)


def select_image(printing):
    for locale in ('zh-Hans', 'zh-Hant'):
        for image in printing.get('images', []):
            if (image.get('locale') == locale and image.get('equivalenceReviewed') is True
                    and image.get('source') and image.get('url')):
                return {**copy.deepcopy(image), 'label': '繁中卡图' if locale == 'zh-Hant' else '简中卡图'}
    return {'url': None, 'locale': None, 'label': '暂无卡图'}
