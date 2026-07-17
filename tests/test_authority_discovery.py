from src.pifactory.authority_discovery import _allowed, _decode_ddg_url


def test_authority_domain_filter():
    assert _allowed("https://ictv.global/report/example", ("ictv.global", "viralzone.expasy.org"))
    assert _allowed("https://viralzone.expasy.org/123", ("ictv.global", "viralzone.expasy.org"))
    assert not _allowed("https://example.com/ictv.global", ("ictv.global", "viralzone.expasy.org"))


def test_duckduckgo_redirect_decode():
    url = "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fictv.global%2Freport%2Fx"
    assert _decode_ddg_url(url) == "https://ictv.global/report/x"
