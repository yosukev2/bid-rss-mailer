from bid_rss_mailer.normalize import extract_deadline, normalize_text, stable_url_key


def test_normalize_text_nfkc_lower_and_spaces() -> None:
    assert normalize_text("  ＡＷＳ　運用  ") == "aws 運用"


def test_stable_url_key_ignores_tracking_query() -> None:
    first = stable_url_key("https://example.com/path?a=1&utm_source=x")
    second = stable_url_key("https://example.com/path?a=1")
    assert first == second


def test_extract_deadline() -> None:
    assert extract_deadline("入札締切 2026年3月15日") == "2026-03-15"

