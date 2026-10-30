"""Current product and API core must execute the same reviewed new mechanics."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_current_rule_modules_match_after_import_relocation():
    for name in ('field_effects.py', 'modifiers.py', 'transfer_attacks.py'):
        product = (ROOT / 'packages/rules' / name).read_text()
        core = (ROOT / 'packages/battle-core/src/ptcg_core/rules' / name).read_text()
        assert ast.dump(ast.parse(product.replace('packages.rules.', 'ptcg_core.rules.'))) == ast.dump(ast.parse(core))
