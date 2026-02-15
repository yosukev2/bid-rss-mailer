import textwrap

import pytest

from bid_rss_mailer.config import ConfigError, load_keyword_sets_config, load_sources_config


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


def test_load_sources_detects_duplicate_urls_after_normalization(tmp_path) -> None:
    config_path = tmp_path / "sources.yaml"
    config_path.write_text(
        textwrap.dedent(
            """
            version: 1
            sources:
              - id: source-1
                name: source-1
                organization: org
                url: https://example.com/a?x=1&utm_source=aa
                enabled: true
              - id: source-2
                name: source-2
                organization: org
                url: https://example.com/a?x=1
                enabled: true
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError):
        load_sources_config(config_path)
