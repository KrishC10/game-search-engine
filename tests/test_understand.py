from datetime import date

import api.understand as understand_module
from api.understand import ClueExtraction, sanitize, understand


def extraction(**overrides):
    fields = dict(
        year_from=2015, year_to=2015, genres=["Adventure"], keywords=["fox", "ruined city"],
        description="A fox explores a ruined black and white city.", platform_hints=[],
    )
    fields.update(overrides)
    return ClueExtraction(**fields)


def test_sanitize_keeps_valid_clues():
    clues = sanitize(extraction(), "memory")
    assert clues.year_from == 2015
    assert clues.genres == ["Adventure"]
    assert clues.keywords == ["fox", "ruined city"]


def test_sanitize_drops_unknown_genres_and_normalises_case():
    clues = sanitize(extraction(genres=["indie", "Soulslike", "rpg", "Indie"]), "memory")
    assert clues.genres == ["Indie", "RPG"]


def test_sanitize_rejects_impossible_years_and_swaps_reversed_ranges():
    assert sanitize(extraction(year_from=1850, year_to=date.today().year + 5), "m").year_from is None
    clues = sanitize(extraction(year_from=2012, year_to=2008), "m")
    assert (clues.year_from, clues.year_to) == (2008, 2012)


def test_sanitize_falls_back_to_memory_when_description_is_empty():
    assert sanitize(extraction(description="  "), "the raw memory").description == "the raw memory"


def test_sanitize_caps_and_dedupes_keywords():
    clues = sanitize(extraction(keywords=["fox", "fox", " "] + [f"k{i}" for i in range(20)]), "m")
    assert clues.keywords[0] == "fox"
    assert len(clues.keywords) == understand_module.MAX_KEYWORDS


def test_provider_none_skips_the_llm(monkeypatch):
    monkeypatch.setattr(understand_module, "LLM_PROVIDER", "none")
    assert understand("a fox in a ruined city") is None


def test_llm_failure_falls_back_to_none(monkeypatch):
    def broken(memory):
        raise RuntimeError("network down")

    monkeypatch.setattr(understand_module, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(understand_module, "_ask_claude", broken)
    assert understand("a fox in a ruined city") is None


def test_prompt_lists_every_steam_genre():
    for genre in understand_module.STEAM_GENRES:
        assert genre in understand_module.SYSTEM_PROMPT
