"""데이터 모델."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

TEXT_OK = "확인"
TEXT_UNAVAILABLE = "본문 확인 불가"
TEXT_NOT_CHECKED = "미확인"

SOURCE_ORIGINAL = "원문"
SOURCE_NAVER = "네이버뉴스"
SOURCE_API = "검색API"


@dataclass
class RawSearchItem:
    """네이버 검색 API 결과 1건 (키워드별 원본)."""

    keyword: str
    title: str
    original_url: str | None
    naver_url: str | None
    description: str
    pub_date: datetime | None
    rank: int = 0


@dataclass
class ArticleDetail:
    """기사 페이지(원문 또는 네이버 뉴스)에서 확인한 정보."""

    url: str
    source: str  # SOURCE_ORIGINAL | SOURCE_NAVER
    ok: bool = False
    published_at: datetime | None = None
    # "structured"(메타/JSON-LD/네이버 data 속성) | "text"(본문 문자열 '입력 …' 패턴)
    published_confidence: str | None = None
    modified_at: datetime | None = None
    publisher: str | None = None
    title: str | None = None
    text: str | None = None
    error: str | None = None


@dataclass
class NewsArticle:
    """중복(URL·제목 동일) 병합 후의 기사 1건."""

    article_id: str
    title: str
    publisher: str | None = None
    published_at: datetime | None = None
    published_at_source: str | None = None
    modified_at: datetime | None = None
    api_pub_date: datetime | None = None
    original_url: str | None = None
    naver_url: str | None = None
    description: str = ""
    article_text: str | None = None
    text_status: str = TEXT_NOT_CHECKED
    matched_keywords: list[str] = field(default_factory=list)
    rejected_keywords: dict[str, str] = field(default_factory=dict)  # 동명이인 등으로 제외된 키워드 -> 사유
    topic: str | None = None
    relevance_score: float | None = None
    included: bool = False
    exclusion_reason: str | None = None
    duplicate_group: int | None = None
    is_representative: bool = True
    similar_article_ids: list[str] = field(default_factory=list)
    alternate_urls: list[str] = field(default_factory=list)  # 동일 기사로 병합된 다른 URL
    judged_by: str = "규칙"
    rule_reason: str | None = None
    llm_reason: str | None = None  # 디버깅용, 일반 화면 미표시
    llm_score: float | None = None
    llm_topic: str | None = None
    domain: str | None = None
    fetch_error: str | None = None

    @property
    def link(self) -> str | None:
        """기사 원문 링크 (원문 URL 우선, 없으면 네이버 뉴스 URL)."""
        return self.original_url or self.naver_url

    @property
    def display_publisher(self) -> str:
        if self.publisher:
            return self.publisher
        if self.domain:
            return f"언론사 미확인({self.domain})"
        return "언론사 미확인"

    @property
    def body_for_judgement(self) -> str:
        """판단용 텍스트: 본문이 있으면 본문, 없으면 검색 요약."""
        if self.article_text:
            return self.article_text
        return self.description or ""


@dataclass
class SearchRun:
    """검색 1회 실행 결과."""

    start: datetime
    end: datetime
    articles: list[NewsArticle]
    stats: dict = field(default_factory=dict)
    keyword_api_counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    llm_used: bool = False

    @property
    def included(self) -> list[NewsArticle]:
        return [a for a in self.articles if a.included]

    @property
    def representatives(self) -> list[NewsArticle]:
        return [a for a in self.articles if a.included and a.is_representative]

    @property
    def excluded(self) -> list[NewsArticle]:
        return [a for a in self.articles if not a.included]

    def by_id(self) -> dict[str, NewsArticle]:
        return {a.article_id: a for a in self.articles}
