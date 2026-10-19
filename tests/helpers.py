"""테스트용 기사 객체 생성 도우미 (테스트 fixture 전용, 실제 기사 아님)."""

from __future__ import annotations

from datetime import datetime

from src.config import KST
from src.models import TEXT_OK, TEXT_UNAVAILABLE, NewsArticle

_counter = {"n": 0}


def make_article(
    title: str,
    text: str | None = None,
    keywords: list[str] | None = None,
    published: datetime | None = None,
    url: str | None = None,
    naver_url: str | None = None,
    description: str = "",
    publisher: str | None = "테스트일보",
    score: float | None = None,
) -> NewsArticle:
    _counter["n"] += 1
    n = _counter["n"]
    return NewsArticle(
        article_id=f"t{n}",
        title=title,
        publisher=publisher,
        published_at=published or datetime(2026, 10, 18, 12, 0, tzinfo=KST),
        original_url=url or f"https://example.com/news/{n}",
        naver_url=naver_url,
        description=description,
        article_text=text,
        text_status=TEXT_OK if text else TEXT_UNAVAILABLE,
        matched_keywords=list(keywords or ["원전"]),
        relevance_score=score,
    )
