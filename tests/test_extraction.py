import pytest

from synthsift.modules.runner import analyze
from synthsift.nlp.pipeline import looks_like_code, resolve_wordnet
from synthsift.nlp.regex_extractors import REGEX_DEFS, find_all


def mentions(text, cfg=None):
    return analyze([("p", text, "user", False)], cfg)["p"].mentions


def cats(text):
    return {m.text: m.category for m in find_all(text, REGEX_DEFS)}


@pytest.mark.parametrize("text,expected", [
    ("open /etc/nginx/nginx.conf now", {"/etc/nginx/nginx.conf": "file_path"}),
    ("edit src/app/main.py please", {"src/app/main.py": "file_path"}),
    (r"see C:\Users\bob\notes.txt", {r"C:\Users\bob\notes.txt": "file_path"}),
    ("upstream 10.0.4.35:8080 failed.", {"10.0.4.35:8080": "ip"}),
    ("ping fe80::1ff:fe23:4567:890a", {"fe80::1ff:fe23:4567:890a": "ip"}),
    ("mail bob@acme.io today", {"bob@acme.io": "email"}),
    ("read https://example.com/a?b=1.", {"https://example.com/a?b=1": "url"}),
    ("CVE-2021-44228 is bad", {"CVE-2021-44228": "cve"}),
    ("sha 3f79bb7b435b05321651daefd374cdc681dc06faa65e374e38337b88ca046dea",
     {"3f79bb7b435b05321651daefd374cdc681dc06faa65e374e38337b88ca046dea": "hash"}),
    ("set DATABASE_URL and $HOME", {"DATABASE_URL": "env_var", "$HOME": "env_var"}),
    ("mac 00:1A:2B:3C:4D:5E", {"00:1A:2B:3C:4D:5E": "mac"}),
    ("region us-east-1", {"us-east-1": "cloud"}),
    ("colour #1A73E8", {"#1A73E8": "color"}),
    ("raised KeyError again", {"KeyError": "error"}),
])
def test_regex_extractors(text, expected):
    assert cats(text) == expected


def test_regex_avoids_code_false_positives():
    assert cats("</span></div> p.c f()") == {"f()": "code"}
    assert "@dataclass" not in cats("@dataclass\nclass A: pass")
    assert cats("ping @oncall") == {"@oncall": "mention"}


def test_wordnet_resolution_prefers_food_for_ingredients():
    assert resolve_wordnet(("plant", "food"), 3) == "food"
    assert resolve_wordnet(("", "device", "place"), 3) == ""
    assert resolve_wordnet(("vehicle",), 3) == "vehicle"


def test_pipeline_finds_domain_objects_and_verbs():
    ms = {m.key: m for m in mentions(
        "I want to bake garlic bread with olive oil for Maria. My Toyota Corolla needs new brake pads.")}
    assert ms["garlic bread"].category == "food"
    assert ms["olive oil"].category == "food"
    assert ms["maria"].category == "person"
    assert ms["toyota corolla"].category == "vehicle"
    assert ms["garlic bread"].verb == "bake"


def test_relations_link_subject_and_object():
    res = analyze([("p", "Alice deployed the Kubernetes cluster to us-east-1.", "user", False)])["p"]
    assert any(r.subj == "alice" and r.obj == "kubernetes" or r.obj == "us-east-1" for r in res.relations)


def test_tool_argument_key_is_not_an_entity():
    res = analyze([("p", "command: ls -la /etc/hosts", "tool_call", False)])["p"]
    assert [m.key for m in res.mentions] == ["/etc/hosts"]


def test_code_heuristic():
    assert looks_like_code("def foo(x):\n    return x + 1")
    assert looks_like_code("const a = {b: 1};")
    assert not looks_like_code("The quick brown fox jumps over the lazy dog.")


def test_custom_vocabulary_and_patterns():
    cfg = {"custom_gazetteer": "spell: expelliarmus, lumos", "custom_regex": r"ticket: \bJIRA-\d+\b"}
    found = {m.key: m.category for m in mentions("Cast Lumos then file JIRA-42.", cfg)}
    assert found["lumos"] == "spell"
    assert found["JIRA-42"] == "ticket"


def test_ignore_list():
    found = {m.key for m in mentions("Thanks for the example about the database.", {"ignore_terms": "database\nexample"})}
    assert "database" not in found and "example" not in found
