"""6개 주제 분류 (규칙 기반 fallback).

우선순위
1. KINS 가 주요 주체 → 한국원자력안전기술원(KINS)
2. 원안위가 주요 주체 → 원자력안전위원회  (제목에 둘 다 있으면 먼저 등장한 주체)
3. 공공기관·출연연 중심(원자력 핵심어 없음) → 기타
4. 원자력·방사선 중심
   - 국회·정당·부처·법안·예산 등 국내 정부/정치 주체 → 정부&정치권
   - 해외 국가·기관·사건 → 국외 원자력&방사선
   - 그 외 → 국내 원자력&방사선
5. 정부/정치 맥락 → 정부&정치권, 나머지 → 기타
"""

from __future__ import annotations

from .config import (
    TOPIC_DOMESTIC,
    TOPIC_ETC,
    TOPIC_FOREIGN,
    TOPIC_GOV,
    TOPIC_KINS,
    TOPIC_NSSC,
    TOPICS,
)
from .filtering import clean_for_judgement
from .models import NewsArticle
from .patterns import (
    DOMESTIC_HINT_RE,
    FOREIGN_RE,
    KINS_RE,
    KOREAN_GOV_RE,
    KOREAN_GOV_STRONG_RE,
    NSSC_RE,
    NUCLEAR_RE,
    PLANT_RE,
    PUBLIC_RE,
    count,
    first_pos,
    has,
)


def _min_pos(*values: int | None) -> int | None:
    vals = [v for v in values if v is not None and v >= 0]
    return min(vals) if vals else None


def classify_rule(article: NewsArticle) -> str:
    title = clean_for_judgement(article.title)
    body = clean_for_judgement(article.body_for_judgement)
    lead = body[:500]
    keywords = set(article.matched_keywords)

    # 1·2) KINS / 원안위 주체 판단 (제목 우선)
    kins_pos = _min_pos(first_pos(KINS_RE, title), title.find("임승철") if "임승철" in keywords else None)
    nssc_pos = _min_pos(first_pos(NSSC_RE, title), title.find("최원호") if "최원호" in keywords else None)
    if kins_pos is not None or nssc_pos is not None:
        if nssc_pos is None or (kins_pos is not None and kins_pos <= nssc_pos):
            return TOPIC_KINS
        return TOPIC_NSSC

    kins_b = count(KINS_RE, body) + (body.count("임승철") if "임승철" in keywords else 0)
    nssc_b = count(NSSC_RE, body) + (body.count("최원호") if "최원호" in keywords else 0)
    if kins_b >= 3 and kins_b >= nssc_b:
        return TOPIC_KINS
    if nssc_b >= 3 and has(NSSC_RE, lead) and nssc_b > kins_b:
        return TOPIC_NSSC

    nuc_title = count(NUCLEAR_RE, title)
    nuclear_main = nuc_title > 0 or count(NUCLEAR_RE, body) >= 3
    public_title = has(PUBLIC_RE, title)
    public_main = public_title or (count(PUBLIC_RE, body) >= 3 and not nuclear_main)

    # 3) 공공기관·출연연 중심
    if public_main and nuc_title == 0:
        return TOPIC_ETC

    # 4) 원자력·방사선 중심
    if nuclear_main:
        if has(KOREAN_GOV_STRONG_RE, title):
            return TOPIC_GOV
        foreign_t = has(FOREIGN_RE, title)
        if foreign_t:
            if has(PLANT_RE, title):
                return TOPIC_DOMESTIC
            return TOPIC_FOREIGN
        if has(KOREAN_GOV_RE, title):
            return TOPIC_GOV
        if not has(DOMESTIC_HINT_RE, title):
            f_lead = count(FOREIGN_RE, lead)
            d_lead = count(DOMESTIC_HINT_RE, lead)
            if f_lead >= 2 and f_lead > d_lead:
                return TOPIC_FOREIGN
            if count(KOREAN_GOV_STRONG_RE, lead) >= 2:
                return TOPIC_GOV
        return TOPIC_DOMESTIC

    # 5) 그 외
    if has(KOREAN_GOV_RE, title) or count(KOREAN_GOV_STRONG_RE, lead) >= 2:
        return TOPIC_GOV
    return TOPIC_ETC


def normalize_topic(value: str | None) -> str | None:
    """LLM 응답 등의 주제 문자열을 6개 주제 중 하나로 맞춘다."""
    if not value:
        return None
    v = value.strip()
    if v in TOPICS:
        return v
    compact = v.replace(" ", "").upper()
    aliases = {
        "KINS": TOPIC_KINS,
        "원자력안전기술원": TOPIC_KINS,
        "원안위": TOPIC_NSSC,
        "원자력안전위원회": TOPIC_NSSC,
        "국내": TOPIC_DOMESTIC,
        "국외": TOPIC_FOREIGN,
        "해외": TOPIC_FOREIGN,
        "정부": TOPIC_GOV,
        "정치": TOPIC_GOV,
        "기타": TOPIC_ETC,
    }
    for key, topic in aliases.items():
        if key.upper() in compact:
            return topic
    return None


def classify(article: NewsArticle) -> str:
    """LLM 판단이 있으면 우선 사용하고, 없으면 규칙 기반으로 분류한다."""
    llm_topic = normalize_topic(article.llm_topic)
    return llm_topic or classify_rule(article)
