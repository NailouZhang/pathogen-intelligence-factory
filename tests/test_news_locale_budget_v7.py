from datetime import date

from src.pifactory.news import search_google_news


class Response:
    text = "<rss><channel></channel></rss>"


class FakeHttp:
    def __init__(self):
        self.urls = []
    def get_text(self, url):
        self.urls.append(url)
        return Response.text


def test_google_news_uses_one_locale_per_language_query():
    http = FakeHttp()
    search_google_news(http, ["hantavirus", "汉坦病毒"], date(2026, 7, 1), date(2026, 7, 7))
    assert len(http.urls) == 2
    assert any("hl=en-US" in x for x in http.urls)
    assert any("hl=zh-CN" in x for x in http.urls)
