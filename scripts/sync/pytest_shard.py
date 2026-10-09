"""Partition collected cases without skipping tests; emit coverage for the runner."""
import json
import os
from pathlib import Path


def pytest_collection_modifyitems(config, items):
    count = int(os.environ["PTCG_TEST_SHARDS"])
    index = int(os.environ["PTCG_TEST_SHARD"])
    assert 0 <= index < count
    all_ids = [item.nodeid for item in items]
    selected = [item for i, item in enumerate(items) if i % count == index]
    deselected = [item for i, item in enumerate(items) if i % count != index]
    Path(os.environ["PTCG_TEST_SHARD_REPORT"]).write_text(json.dumps({
        "all": all_ids, "selected": [item.nodeid for item in selected],
    }), encoding="utf-8")
    items[:] = selected
    config.hook.pytest_deselected(items=deselected)
