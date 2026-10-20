"""Reviewed full-deck search sites in the pinned upstream overlay.

Selection costs, top-N views and subsequent target/order choices are excluded.
The build fails if a reviewed call site disappears; no text-based runtime inference.
"""
import ast

SEARCHES = {
    'SVI/jacq.py': ['evolution_pokemon'],
    'SVI/miraidon_ex.py': ['basic_lightning_pokemon'],
    'BRS/minccino.py': ['cards'], 'BRS/lumineon_v.py': ['cards'],
    'TWM/secret_box.py': ['card_pool'], 'TWM/hyper_aroma.py': ['stage1_pokemon'],
    'SIT/forest_seal_stone.py': ['cards'], 'SIT/capturing_aroma.py': ['cards'],
    'SIT/archeops.py': ['special_energy_cards'], 'PAL/artazon.py': ['basic_pokemon'],
    'TEF/ciphermaniacs_codebreaking.py': ['player.left[:]'],
    'TEF/master_ball.py': ['getCards'], 'TEF/buddy_buddy_poffin.py': ['cards'],
    'OBF/arven.py': ['item_cards', 'tool_cards'], 'OBF/pidgeot_ex.py': ['cards'],
    'OBF/letter_of_encouragement.py': ['energy_cards'], 'OBF/town_store.py': ['tools'],
    'PAF/nest_ball.py': ['cards'], 'PAF/charizard_ex.py': ['cards'],
    'PAF/ultra_ball.py': ['getCards'], 'MEW/pidgey.py': ['cards'],
    'PAR/earthen_vessel.py': ['basic_energy'], 'PAR/jirachi.py': ['cards'],
    'PAR/technical_machine_evolution.py': ['evolutions'],
}


def apply(root):
    for name, candidates in SEARCHES.items():
        path = root / 'ptcg/cards' / name
        source = path.read_text(encoding='utf-8')
        lines = source.splitlines(keepends=True)
        offsets = [0]
        for line in lines:
            offsets.append(offsets[-1] + len(line))
        replacements = []
        found = []
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != 'choose_card_actions':
                continue
            if len(node.args) < 5 or ast.unparse(node.args[4]) not in candidates:
                continue
            found.append(ast.unparse(node.args[4]))
            node.func.id = '_full_deck_choice'
            node.keywords.append(ast.keyword(arg='deck_owner', value=ast.Name(id='player', ctx=ast.Load())))
            # AST columns are byte offsets; convert before slicing Unicode source.
            start = offsets[node.lineno - 1] + len(lines[node.lineno - 1].encode()[:node.col_offset].decode())
            end = offsets[node.end_lineno - 1] + len(lines[node.end_lineno - 1].encode()[:node.end_col_offset].decode())
            replacements.append((start, end, ast.unparse(node)))
        assert sorted(found) == sorted(candidates), (name, found, candidates)
        for start, end, replacement in sorted(replacements, reverse=True):
            source = source[:start] + replacement + source[end:]
        source += '\nfrom packages.rules.deck_search import choose_card_actions as _full_deck_choice\n'
        path.write_text(source, encoding='utf-8', newline='\n')
