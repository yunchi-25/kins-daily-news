from datetime import datetime

from src.article_parser import parse_generic_article, parse_naver_article, publisher_from_domain
from src.config import KST
from src.models import TEXT_OK, TEXT_UNAVAILABLE
from tests.helpers import make_article

BODY = "원자력안전위원회는 18일 회의를 열고 고리 3호기 계속운전 허가안을 의결했다. " * 6

NAVER_HTML = f"""
<html><head>
<meta property="og:title" content="원안위, 고리 3호기 계속운전 승인">
<meta property="og:article:author" content="연합뉴스 | 네이버">
</head><body>
<div class="media_end_head_top"><a class="media_end_head_top_logo"><img alt="연합뉴스" src="x.png"></a></div>
<h2 id="title_area"><span>원안위, 고리 3호기 계속운전 승인</span></h2>
<span class="media_end_head_info_datestamp_time _ARTICLE_DATE_TIME" data-date-time="2026-10-18 16:42:11">2026.10.18. 오후 4:42</span>
<span class="media_end_head_info_datestamp_time _ARTICLE_MODIFY_DATE_TIME" data-modify-date-time="2026-10-18 18:01:00">2026.10.18. 오후 6:01</span>
<article id="dic_area">{BODY}</article>
</body></html>
"""

GENERIC_HTML = f"""
<html><head>
<meta property="og:site_name" content="테스트뉴스">
<meta property="article:published_time" content="2026-10-18T16:40:00+09:00">
<meta property="article:modified_time" content="2026-10-18T19:00:00+09:00">
<script type="application/ld+json">{{"@type":"NewsArticle","datePublished":"2026-10-18T16:41:00+09:00","publisher":{{"name":"테스트뉴스"}}}}</script>
</head><body>
<aside>많이 본 뉴스 입력 2026.10.01 09:00</aside>
<div class="info">입력 2026.10.18 16:40 | 수정 2026.10.18 19:00</div>
<div id="article-view-content-div">{BODY}</div>
</body></html>
"""

TEXT_ONLY_HTML = f"""
<html><head><title>기사</title></head><body>
<div class="byline">기사입력 2026-10-18 16:39 최종수정 2026-10-18 20:00</div>
<div class="article_body">{BODY}</div>
</body></html>
"""


def test_parse_naver_article():
    d = parse_naver_article(NAVER_HTML, "https://n.news.naver.com/mnews/article/001/0000000001")
    assert d.ok
    assert d.published_at == datetime(2026, 10, 18, 16, 42, 11, tzinfo=KST)
    assert d.modified_at == datetime(2026, 10, 18, 18, 1, tzinfo=KST)
    assert d.publisher == "연합뉴스"
    assert d.text and "고리 3호기" in d.text


def test_parse_generic_article_uses_earliest_published_not_modified():
    d = parse_generic_article(GENERIC_HTML, "https://test.example.com/news/1")
    assert d.ok
    assert d.published_at == datetime(2026, 10, 18, 16, 40, tzinfo=KST)
    assert d.published_confidence == "structured"
    assert d.modified_at == datetime(2026, 10, 18, 19, 0, tzinfo=KST)
    assert d.publisher == "테스트뉴스"
    assert d.text and "의결" in d.text


def test_parse_generic_text_only_time_is_low_confidence():
    d = parse_generic_article(TEXT_ONLY_HTML, "https://test.example.com/news/2")
    assert d.published_at == datetime(2026, 10, 18, 16, 39, tzinfo=KST)
    assert d.published_confidence == "text"


def test_unparseable_page_does_not_invent_text():
    d = parse_generic_article("<html><body><p>짧음</p></body></html>", "https://x.example.com/1")
    assert d.ok and d.text is None and d.published_at is None


def test_publisher_from_domain():
    assert publisher_from_domain("https://www.yna.co.kr/view/AKR123") == "연합뉴스"
    assert publisher_from_domain("https://news.kbs.co.kr/news/view.do?ncd=1") == "KBS"
    assert publisher_from_domain("https://unknown-site.example/1") is None


def test_enrich_marks_unavailable_text_on_fetch_failure(monkeypatch):
    from src import article_parser
    from src.models import ArticleDetail

    def fake_fetch(session, url, **kw):
        return ArticleDetail(url=url, source="원문", ok=False, error="HTTP 403")

    monkeypatch.setattr(article_parser, "fetch_article_detail", fake_fetch)
    a = make_article("원전 기사", None, ["원전"], url="https://blocked.example.com/1")
    a.api_pub_date = datetime(2026, 10, 18, 10, 0, tzinfo=KST)
    a.publisher = None
    article_parser.enrich_article(a, session=None, now=datetime(2026, 10, 19, 8, 0, tzinfo=KST))
    assert a.text_status == TEXT_UNAVAILABLE
    assert a.article_text is None
    assert a.published_at == a.api_pub_date and a.published_at_source == "검색API"
    assert a.publisher is None  # 언론사를 지어내지 않음
    assert a.display_publisher.startswith("언론사 미확인")


def test_enrich_prefers_original_time(monkeypatch):
    from src import article_parser

    def fake_fetch(session, url, **kw):
        if "naver" in url:
            return parse_naver_article(NAVER_HTML, url)
        return parse_generic_article(GENERIC_HTML, url)

    monkeypatch.setattr(article_parser, "fetch_article_detail", fake_fetch)
    a = make_article("원안위, 고리 3호기 계속운전 승인", None, ["원안위"], url="https://test.example.com/news/1",
                     naver_url="https://n.news.naver.com/mnews/article/001/0000000001")
    a.api_pub_date = datetime(2026, 10, 18, 16, 45, tzinfo=KST)
    article_parser.enrich_article(a, session=None, now=datetime(2026, 10, 19, 8, 0, tzinfo=KST))
    assert a.published_at == datetime(2026, 10, 18, 16, 40, tzinfo=KST)
    assert a.published_at_source == "원문"
    assert a.text_status == TEXT_OK
