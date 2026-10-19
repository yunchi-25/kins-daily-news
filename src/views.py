"""화면/내보내기에서 공통으로 쓰는 결과 가공 함수 (Streamlit 비의존)."""

from __future__ import annotations

from dataclasses import dataclass

from .config import NEWS_KEYWORDS, TOPICS
from .models import NewsArticle, SearchRun
from .ranking import sort_articles
from .utils import to_kst

SORT_IMPORTANCE = "중요도순"
SORT_LATEST = "최신순"


@dataclass
class Filters:
    topics: list[str] | None = None
    keywords: list[str] | None = None
    publishers: list[str] | None = None
    query: str = ""

    def match(self, a: NewsArticle) -> bool:
        if self.topics and a.topic not in self.topics:
            return False
        if self.keywords and not (set(self.keywords) & set(a.matched_keywords)):
            return False
        if self.publishers and a.display_publisher not in self.publishers:
            return False
        q = (self.query or "").strip()
        if q and q.lower() not in a.title.lower():
            return False
        return True


def sort_by(articles: list[NewsArticle], mode: str) -> list[NewsArticle]:
    if mode == SORT_LATEST:
        return sorted(
            articles,
            key=lambda a: (-(to_kst(a.published_at).timestamp() if a.published_at else 0), -(a.relevance_score or 0)),
        )
    return sort_articles(articles)


def similar_members(run: SearchRun) -> dict[int, list[NewsArticle]]:
    """중복 그룹 번호 -> 그룹 기사 목록 (대표 포함)."""
    groups: dict[int, list[NewsArticle]] = {}
    for a in run.included:
        if a.duplicate_group is not None:
            groups.setdefault(a.duplicate_group, []).append(a)
    for g in groups.values():
        g.sort(key=lambda x: (not x.is_representative, -(x.relevance_score or 0)))
    return groups


def topic_view(run: SearchRun, filters: Filters | None = None) -> dict[str, list[NewsArticle]]:
    """주제별 보기: 대표 기사만, 6개 주제 고정 순서, 중요도순."""
    filters = filters or Filters()
    out: dict[str, list[NewsArticle]] = {t: [] for t in TOPICS}
    for a in run.representatives:
        if a.topic in out and filters.match(a):
            out[a.topic].append(a)
    return {t: sort_articles(v) for t, v in out.items()}


def keyword_view(
    run: SearchRun,
    filters: Filters | None = None,
    collapse_similar: bool = True,
    sort_mode: str = SORT_LATEST,
) -> dict[str, list[NewsArticle]]:
    """키워드별 보기: 28개 키워드 모두(0건 포함), 같은 기사의 키워드 간 중복 표시 허용.

    collapse_similar=True 이면 같은 키워드 안에서 같은 사건 유사 보도는 1건만 남긴다
    (남기는 기사는 반드시 그 키워드로 실제 검색된 기사).
    """
    filters = filters or Filters()
    selected = filters.keywords or NEWS_KEYWORDS
    out: dict[str, list[NewsArticle]] = {}
    base = [a for a in run.included if Filters(filters.topics, None, filters.publishers, filters.query).match(a)]
    for kw in NEWS_KEYWORDS:
        if kw not in selected:
            continue
        items = [a for a in base if kw in a.matched_keywords]
        if collapse_similar:
            best: dict[object, NewsArticle] = {}
            for a in sort_articles(items):  # 중요도 높은 순 → 그룹 대표 우선
                key = a.duplicate_group if a.duplicate_group is not None else a.article_id
                cur = best.get(key)
                if cur is None or (a.is_representative and not cur.is_representative):
                    best[key] = a
            items = list(best.values())
        out[kw] = sort_by(items, sort_mode)
    return out


def all_articles_view(run: SearchRun, filters: Filters | None = None, sort_mode: str = SORT_LATEST) -> list[NewsArticle]:
    filters = filters or Filters()
    return sort_by([a for a in run.included if filters.match(a)], sort_mode)


def publisher_options(run: SearchRun) -> list[str]:
    return sorted({a.display_publisher for a in run.included})
