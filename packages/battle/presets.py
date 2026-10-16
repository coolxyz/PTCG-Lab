"""Production copies of the five accepted card lists; no trainer dependency."""
import json
from pathlib import Path

PRESETS = json.loads((Path(__file__).with_name("h2-presets-v1.json")).read_text())
