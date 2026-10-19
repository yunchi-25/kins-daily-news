"""기사 선별(관련성 판단), 동명이인 판별, 기간 필터.

- prescreen(): 제목·검색 요약만으로 명백한 무관 기사를 걸러 원문 요청 수를 줄인다(관대하게).
- assess():   원문 본문까지 반영해 최종 포함 여부를 판단한다(규칙 기반 fallback).
"""

from __future__ import annotations

import re
from datetime import datetime

from .config import NEWS_KEYWORDS, PERSON_RULES, PersonRule
from .models import TEXT_OK, NewsArticle
from .patterns import (
    AI_SEMI_RE,
    AWARD_RE,
    ENTERTAIN_RE,
    EVENT_RE,
    FOREIGN_RE,
    KINS_RE,
    KOREAN_GOV_STRONG_RE,
    MEDICAL_EXCEPT_RE,
    MEDICAL_RE,
    MOU_RE,
    NOISE_RE,
    NSSC_RE,
    NUCLEAR_RE,
    PHOTO_RE,
    PROMO_RE,
    PUBLIC_RE,
    SAFETY_RE,
    SCAM_RE,
    STOCK_STRONG_RE,
    STOCK_WEAK_RE,
    STRIFE_RE,
    count,
    has,
)
from .utils import in_time_range

# 제외 사유 문구
R_OUT_OF_RANGE = "검색 기간 밖 기사(최초 게시 시각 기준)"
R_NO_TIME = "게시 시각 확인 불가"
R_NOT_RELEVANT = "검색 키워드가 부수적으로 언급된 기사"
R_NO_EVIDENCE = "본문 확인 불가 · 제목/요약상 업무 관련성 확인 불가"


def clean_for_judgement(text: str | None) -> str:
    """판단용 텍스트에서 업무 무관 고유명사(예: 원자력병원)를 지운다."""
    return NOISE_RE.sub(" ", text or "")


def _hosts(article: NewsArticle) -> str:
    return " ".join(u.lower() for u in (article.original_url, article.naver_url) if u)


# ---------------------------------------------------------------------------
# 제외 기준 (공통)
# ---------------------------------------------------------------------------
def hard_exclusion(title: str, article: NewsArticle | None = None) -> str | None:
    """제목 기준 명백한 제외 대상이면 사유를, 아니면 None 을 반환한다."""
    t = clean_for_judgement(title)
    nuc_t = count(NUCLEAR_RE, t)
    kins_t = has(KINS_RE, t)
    core_t = kins_t or has(NSSC_RE, t)

    if has(SCAM_RE, t):
        return "사칭·피싱 등 업무 무관 기사"
    if has(STOCK_STRONG_RE, t) or count(STOCK_WEAK_RE, t) >= 2:
        return "증권·주가·종목 기사"
    hosts = _hosts(article) if article is not None else ""
    if ("sports" in hosts or "entertain" in hosts) and nuc_t == 0 and not core_t:
        return "연예·스포츠·문화 기사"
    if has(ENTERTAIN_RE, t) and nuc_t == 0 and not core_t:
        return "연예·스포츠·문화 기사"
    if has(EVENT_RE, t) and not core_t:
        return "단순 행사·일정 안내 기사"
    if has(AWARD_RE, t) and not kins_t:
        return "수상·시상 기사"
    if has(PROMO_RE, t) and not (core_t or has(SAFETY_RE, t)):
        return "제품 출시·기업 홍보 기사"
    if has(MOU_RE, t) and not (core_t or has(FOREIGN_RE, t) or has(KOREAN_GOV_STRONG_RE, t)):
        return "단순 MOU·협약 체결 홍보 기사"
    if has(AI_SEMI_RE, t) and nuc_t == 0 and not core_t:
        return "반도체·AI·데이터센터 중심 기사(원자력 비중 낮음)"
    if has(MEDICAL_RE, t) and not has(MEDICAL_EXCEPT_RE, t) and not core_t:
        return "의료·치료 중심 기사"
    if has(STRIFE_RE, t) and nuc_t == 0 and not has(PUBLIC_RE, t) and not core_t:
        return "정쟁·정치적 비난 중심 기사"
    return None


def _domain_hit(text: str) -> bool:
    return has(NUCLEAR_RE, text) or has(KINS_RE, text) or has(NSSC_RE, text) or has(PUBLIC_RE, text)


def person_keywords(article: NewsArticle) -> list[str]:
    return [k for k in article.matched_keywords if k in PERSON_RULES]


# ---------------------------------------------------------------------------
# 1차 선별 (원문 확인 전)
# ---------------------------------------------------------------------------
def prescreen(article: NewsArticle) -> str | None:
    """원문 요청 전 1차 선별. 제외해야 하면 사유를 반환한다."""
    reason = hard_exclusion(article.title, article)
    if reason:
        return reason
    combined = clean_for_judgement(f"{article.title}\n{article.description}")
    if _domain_hit(combined):
        return None
    # 인물 키워드로만 검색된 경우: 분야 단어가 보이면 원문에서 다시 확인
    for kw in person_keywords(article):
        rule = PERSON_RULES[kw]
        if any(term in combined for term in rule.org_terms + rule.domain_terms):
            return None
    if person_keywords(article) and len(person_keywords(article)) == len(article.matched_keywords):
        return "동명이인 가능성(관련 분야 맥락 없음)"
    return R_NOT_RELEVANT


# ---------------------------------------------------------------------------
# 동명이인 판별
# ---------------------------------------------------------------------------
def _contains(term: str, text: str) -> bool:
    if term.isascii():
        return re.search(rf"(?<![A-Za-z]){re.escape(term)}(?![A-Za-z])", text) is not None
    return term in text


def check_person(rule: PersonRule, title: str, text: str) -> tuple[bool | None, str]:
    """(True=해당 인물, False=동명이인 추정, None=확인 불가), 사유."""
    full = f"{title}\n{text}"
    if rule.name not in full:
        return None, f"기사에서 '{rule.name}' 확인 불가"
    for org in rule.org_terms:
        if _contains(org, full):
            return True, f"'{org}' 확인"
    for m in re.finditer(re.escape(rule.name), full):
        window = full[max(0, m.start() - rule.window): m.end() + rule.window]
        role_ok = not rule.role_terms or any(r in window for r in rule.role_terms)
        domain_ok = any(d in window for d in rule.domain_terms)
        if role_ok and domain_ok:
            return True, "직함·분야 맥락 확인"
    return False, f"동명이인 추정({rule.description} 관련 맥락 없음)"


def apply_person_rules(article: NewsArticle) -> str | None:
    """인물 키워드의 동명이인 여부를 확인해 matched_keywords 를 정리한다.

    남는 키워드가 없으면 제외 사유를 반환한다.
    """
    persons = person_keywords(article)
    if not persons:
        return None
    text = clean_for_judgement(article.body_for_judgement)
    last_reason = None
    for kw in persons:
        ok, reason = check_person(PERSON_RULES[kw], article.title, text)
        if ok:
            continue
        if ok is None and article.text_status != TEXT_OK:
            reason = "본문 확인 불가로 동명이인 여부 확인 불가"
        article.rejected_keywords[kw] = reason
        article.matched_keywords = [k for k in article.matched_keywords if k != kw]
        last_reason = reason
    if not article.matched_keywords:
        return last_reason or "동명이인 추정"
    return None


# ---------------------------------------------------------------------------
# 최종 선별 (원문 확인 후)
# ---------------------------------------------------------------------------
def time_exclusion(article: NewsArticle, start: datetime, end: datetime) -> str | None:
    if article.published_at is None:
        return R_NO_TIME
    if not in_time_range(article.published_at, start, end):
        return R_OUT_OF_RANGE
    return None


def assess(article: NewsArticle) -> tuple[bool, str]:
    """규칙 기반 최종 판단. (포함 여부, 사유)"""
    reason = hard_exclusion(article.title, article)
    if reason:
        return False, reason

    title = clean_for_judgement(article.title)
    text = clean_for_judgement(article.article_text) if article.article_text else ""
    desc = clean_for_judgement(article.description)

    if has(PHOTO_RE, title) and len(text) < 300:
        return False, "내용이 거의 없는 사진·영상 기사"

    person_reason = apply_person_rules(article)
    if person_reason:
        return False, person_reason
    verified_persons = person_keywords(article)

    nuc_t = count(NUCLEAR_RE, title)
    full = f"{title}\n{text or desc}"

    if has(KINS_RE, full):
        return True, "KINS 관련 언급"
    if has(AI_SEMI_RE, title) and nuc_t and text and count(NUCLEAR_RE, text) < 3:
        return False, "반도체·AI 중심 기사(원자력은 부수적 언급)"
    if nuc_t or has(NSSC_RE, title) or has(PUBLIC_RE, title):
        return True, "제목에 업무 관련 핵심어"
    if any(p in title for p in verified_persons):
        return True, "지정 인물 관련 기사(제목)"

    if text:
        lead = text[:400]
        nb = count(NUCLEAR_RE, text)
        pb = count(PUBLIC_RE, text)
        if (nb >= 3 and has(NUCLEAR_RE, lead)) or nb >= 5:
            return True, "본문 중심 내용이 원자력·방사선 관련"
        if pb >= 3 and has(PUBLIC_RE, lead):
            return True, "본문 중심 내용이 공공기관 관련"
        if count(NSSC_RE, text) >= 2:
            return True, "본문에 원안위 관련 내용"
        if verified_persons:
            return True, "지정 인물의 관련 분야 발언"
        return False, R_NOT_RELEVANT

    if _domain_hit(desc) or verified_persons:
        return True, "검색 요약상 업무 관련(본문 확인 불가)"
    return False, R_NO_EVIDENCE


def sort_keywords(keywords: list[str]) -> list[str]:
    order = {k: i for i, k in enumerate(NEWS_KEYWORDS)}
    return sorted(set(keywords), key=lambda k: order.get(k, 999))
