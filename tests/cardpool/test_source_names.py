from scripts.cardpool.source_names import evolution
from scripts.cardpool.compile_plain import compile_rule
from tests.wiki_fixtures import load_articles


def test_species_reference_disambiguates_polluted_alias_without_inventing_evolution():
    assert evolution({"evo":"催眠貘","evonumber":"096"},{"催眠貘":{"Ninetales","Drowzee"}})=={"Drowzee"}
    assert evolution({"evo":"盐石垒","evonumber":"933"},{"盐石垒":{"Toxicroak"}})=={"Naclstack"}
    assert evolution({"evo":"","evonumber":"852"},{})=={"Clobbopus"}
    assert evolution({"evo":"","evonumber":""},{})==set()


def test_printed_species_header_and_dex_correct_mismatched_intro_translation():
    pages=load_articles()
    for title,name in [("麒麟奇（SV2D）","Girafarig"),("盐石垒（SV2D）","Naclstack"),("下石鸟（SV2D）","Bombirdier")]:
        row=compile_rule(pages[title],{"盐石宝":{"Nacli"}})
        assert row and row["name"]==name


def test_form_names_are_not_replaced_with_base_species():
    row=compile_rule(load_articles()["加熱洛托姆（SV10）"])
    assert row and row["name"]=="Heat Rotom"
