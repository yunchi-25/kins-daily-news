"""중요도(relevance_score) 계산 및 정렬.

우선순위: KINS 직접 관련 > 원자력 안전·규제 > 공식 발표 > 사고·고장·정지·허가·점검
> 정책 영향 > 새로운 사실 > 구체성 > 언론사 신뢰도 > 게시 시각(동점 처리)
"""

from __future__ import annotations

from .config import MAJOR_PUBLISHERS, TOPICS
from .filtering import clean_for_judgement
from .models import TEXT_OK, NewsArticle
from .patterns import (
    INCIDENT_RE,
    KINS_RE,
    KOREAN_GOV_RE,
    NEW_FACT_RE,
    NSSC_RE,
    NUCLEAR_RE,
    OFFICIAL_RE,
    OPINION_RE,
    POLICY_RE,
    PUBLIC_RE,
    SAFETY_RE,
    SPECULATIVE_RE,
    STRIFE_RE,
    count,
    has,
)
from .utils import to_kst


def rule_score(article: NewsArticle) -> float:
    title = clean_for_judgement(article.title)
    body = clean_for_judgement(article.body_for_judgement)
    lead = body[:600]
    score = 30.0

    # 1. KINS 업무 직접 관련
    if has(KINS_RE, title) or ("임승철" in article.matched_keywords and "임승철" in title):
        score += 35
    else:
        kb = count(KINS_RE, body)
        if kb:
            score += 12 + min(12, 3 * kb)

    # 2. 원자력 안전·규제
    if has(NSSC_RE, title) or ("최원호" in article.matched_keywords and "최원호" in title):
        score += 18
    elif has(NSSC_RE, lead):
        score += 7
    nuc_t = count(NUCLEAR_RE, title)
    score += min(8, 4 * nuc_t)
    safety_t = count(SAFETY_RE, title)
    if safety_t:
        score += 8 + min(6, 2 * (safety_t - 1))

    # 3. 공식 발표
    if has(OFFICIAL_RE, title) or (has(OFFICIAL_RE, lead[:200]) and (has(NSSC_RE, lead) or has(KINS_RE, lead) or has(KOREAN_GOV_RE, lead))):
        score += 5

    # 4. 사고·고장·정지·허가·점검
    if has(INCIDENT_RE, title):
        score += 6

    # 5. 정책적 영향
    if has(POLICY_RE, title):
        score += 5
    if has(PUBLIC_RE, title):
        score += 3

    # 6. 새로운 사실
    if has(NEW_FACT_RE, title):
        score += 3

    # 7. 구체성
    text_len = len(article.article_text or "")
    if text_len >= 1200:
        score += 4
    elif text_len >= 500:
        score += 2
    if article.text_status != TEXT_OK:
        score -= 3

    # 8. 언론사 신뢰도
    if article.publisher in MAJOR_PUBLISHERS:
        score += 3

    # 감점
    if has(OPINION_RE, title):
        score -= 6
    if has(STRIFE_RE, title):
        score -= 10
    if has(SPECULATIVE_RE, title):
        score -= 6

    return max(0.0, min(100.0, round(score, 1)))


def final_score(article: NewsArticle) -> float:
    base = rule_score(article)
    if article.llm_score is not None:
        base = 0.6 * float(article.llm_score) + 0.4 * base
    return round(max(0.0, min(100.0, base)), 1)


def sort_key(article: NewsArticle):
    """중요도 내림차순, 동점이면 최신 게시 시각 우선."""
    ts = to_kst(article.published_at).timestamp() if article.published_at else 0
    return (-(article.relevance_score or 0), -ts)


def sort_articles(articles: list[NewsArticle]) -> list[NewsArticle]:
    return sorted(articles, key=sort_key)


def group_by_topic(articles: list[NewsArticle]) -> dict[str, list[NewsArticle]]:
    """주제별 보기: 포함된 대표 기사만, 주제 순서 고정, 중요도 순 정렬."""
    grouped: dict[str, list[NewsArticle]] = {t: [] for t in TOPICS}
    for a in articles:
        if a.included and a.is_representative and a.topic in grouped:
            grouped[a.topic].append(a)
    return {t: sort_articles(v) for t, v in grouped.items()}
