from bid_rss_mailer.config import load_keyword_sets_config, load_sources_config


def test_load_sources_config_has_initial_17_sources() -> None:
    sources = load_sources_config("data/sources.yaml")
    assert len(sources) == 17
    assert sources[0].id == "gsi-nyusatu-1"


def test_load_keyword_sets_has_three_sets() -> None:
    keyword_sets = load_keyword_sets_config("data/keyword_sets.yaml")
    assert len(keyword_sets) == 3
    assert {keyword_set.id for keyword_set in keyword_sets} == {
        "set-a-it-ops-cloud",
        "set-b-survey-gis",
        "set-c-research-study",
    }

