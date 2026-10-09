"""Pinned historical compiler inputs; independent of the live source cache."""
import json
from pathlib import Path


def load_articles():
    return json.loads((Path(__file__).parent / 'fixtures/wiki-rule-pages.json').read_text(encoding='utf-8'))['pages']
