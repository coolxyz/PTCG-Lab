"""Cosmetic finish labels and deterministic normal-face cover selection."""


def label(card, variant):
    if variant.get('finishResolution', {}).get('status') == 'needs-review':
        return '工艺待核验'
    code = variant.get('finishCode')
    if str(card.get('productCode', '')).startswith('151C'):
        return {0: '普通版', 1: '精灵球版', 2: '大师球版'}.get(code, '工艺待核验')
    return '原始卡面' if code in (None, 0) else f'特殊工艺 {code}'


def refresh(card):
    """Keep IDs/inventory stable; put the ordinary source face first if present."""
    variants = card.get('variants', [])
    for variant in variants:
        variant['finishLabel'] = label(card, variant)
    candidates = [v for v in variants if not v.get('removed') and v.get('image', {}).get('url')]
    if not candidates:
        return card
    def rank(v):
        code = v.get('finishCode')
        return (0 if code == 0 else 1 if code is None else 2, str(v.get('upstreamId', '')).zfill(12))
    chosen = min(candidates, key=rank)
    current = next((i.get('variantId') for i in card.get('images', []) if i.get('matchMethod') == 'source-record-product-number-name'), None)
    existing = next((v for v in candidates if v['variantId'] == current), None)
    if existing and rank(existing)[0] == rank(chosen)[0]:
        chosen = existing
    # Uploaded artwork is applied separately by CardMedia and remains authoritative.
    card['images'] = [chosen['image']] + [i for i in card.get('images', []) if i.get('variantId') != chosen['variantId']]
    return card
