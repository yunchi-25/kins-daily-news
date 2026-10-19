"""검색 → 기간 필터 → 원문 확인 → 선별 → 동명이인 → 중복 제거 → 분류 → 정렬 파이프라인."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Callable

from .article_parser import enrich_article, publisher_from_domain
from .classifier import classify
from .config import NEWS_KEYWORDS, PERSON_RULES, Settings
from .deduplication import group_same_events, merge_exact_duplicates
from .filtering import R_NOT_RELEVANT, assess, prescreen, sort_keywords, time_exclusion
from .llm import LLMClient, LLMError
from .models import SOURCE_API, TEXT_NOT_CHECKED, NewsArticle, RawSearchItem, SearchRun
from .naver_news import NaverApiError, NaverAuthError, NaverNewsClient
from .ranking import final_score
from .utils import format_kst, in_time_range, new_session, to_kst

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, float, str], None]

# 전체 진행률에서 단계별 비중
_P_SEARCH = (0.00, 0.40)
_P_DETAIL = (0.40, 0.85)
_P_JUDGE = (0.85, 0.95)
_P_FINAL = (0.95, 1.00)

STAGE_SEARCH = "기사 검색"
STAGE_DETAIL = "상세 확인"
STAGE_FILTER = "기사 선별"
STAGE_DEDUP = "중복 제거"
STAGE_CLASSIFY = "주제 분류"


def _span(span: tuple[float, float], frac: float) -> float:
    return span[0] + (span[1] - span[0]) * max(0.0, min(1.0, frac))


class SearchAborted(Exception):
    """검색 전체를 진행할 수 없는 오류 (인증 실패 등)."""


def run_search(
    start: datetime,
    end: datetime,
    settings: Settings,
    *,
    use_llm: bool = True,
    fetch_pages: bool | None = None,
    progress: ProgressCallback | None = None,
    naver_client: NaverNewsClient | None = None,
    enrich_fn: Callable[[NewsArticle, object, datetime], NewsArticle] | None = None,
    llm_client: LLMClient | None = None,
) -> SearchRun:
    start = to_kst(start)
    end = to_kst(end)
    if start > end:
        raise ValueError("검색 시작 시각이 현재 시각보다 늦습니다.")
    fetch_pages = settings.fetch_article_text if fetch_pages is None else fetch_pages
    report = progress or (lambda stage, frac, msg: None)
    warnings: list[str] = []
    stats: dict = {}

    # ------------------------------------------------------------------
    # 1. 키워드별 네이버 뉴스 검색
    # ------------------------------------------------------------------
    report(STAGE_SEARCH, 0.0, "검색 준비 중...")
    if naver_client is None:
        try:
            naver_client = NaverNewsClient(
                settings.naver_client_id,
                settings.naver_client_secret,
                timeout=settings.http_timeout,
                retries=settings.http_retries,
                display=settings.naver_display,
                max_start=settings.naver_max_start,
                request_interval=settings.naver_request_interval,
            )
        except NaverAuthError as exc:
            raise SearchAborted(str(exc)) from exc

    raw_items: list[RawSearchItem] = []
    keyword_api_counts: dict[str, int] = {}
    total_kw = len(NEWS_KEYWORDS)
    failed_keywords: list[str] = []
    for i, kw in enumerate(NEWS_KEYWORDS):
        report(STAGE_SEARCH, _span(_P_SEARCH, i / total_kw), f"[ {i + 1} / {total_kw} ] {kw} 검색 중")
        try:
            res = naver_client.search_keyword(kw, start, end, settings.max_results_per_keyword)
        except NaverAuthError as exc:
            raise SearchAborted(str(exc)) from exc
        except NaverApiError as exc:
            failed_keywords.append(kw)
            warnings.append(f"'{kw}' 검색 실패: {exc}")
            keyword_api_counts[kw] = 0
            continue
        except Exception as exc:  # 예상치 못한 오류도 해당 키워드만 건너뛴다.
            logger.exception("keyword search failed: %s", kw)
            failed_keywords.append(kw)
            warnings.append(f"'{kw}' 검색 중 오류: {type(exc).__name__}")
            keyword_api_counts[kw] = 0
            continue
        keyword_api_counts[kw] = res.total_fetched
        if res.reached_limit:
            warnings.append(
                f"'{kw}' 키워드는 네이버 API 최대 조회 한도({settings.max_results_per_keyword:,}건)에 도달하여 "
                "검색 기간 초반 일부 기사가 조회되지 않았을 수 있습니다."
            )
        raw_items.extend(res.items)
    report(STAGE_SEARCH, _P_SEARCH[1], f"키워드 검색 완료 (API 결과 {len(raw_items):,}건)")
    if failed_keywords and len(failed_keywords) == total_kw:
        raise SearchAborted("모든 키워드 검색에 실패했습니다. 네트워크 상태 또는 네이버 API 상태를 확인해주세요.")

    # ------------------------------------------------------------------
    # 2. 1차 기간 필터 (API pubDate 기준; 최초 게시 시각은 이후 원문으로 재확인)
    #    pubDate 가 검색 시작 이전이면 최초 게시 시각도 그 이전이므로 제외한다.
    #    pubDate 를 해석하지 못한 항목은 원문에서 시각을 확인하기 위해 남긴다.
    # ------------------------------------------------------------------
    in_range = [it for it in raw_items if it.pub_date is None or in_time_range(it.pub_date, start, end)]
    stats["api_results"] = len(raw_items)
    stats["collected"] = len(in_range)

    # 3. URL·제목 동일 기사 병합
    articles = merge_exact_duplicates(in_range)
    stats["unique"] = len(articles)

    # 4. 1차 선별 (제목·요약 기준 명백한 무관 기사 제외)
    report(STAGE_FILTER, _P_SEARCH[1], "1차 선별 중...")
    candidates: list[NewsArticle] = []
    for a in articles:
        reason = prescreen(a)
        if reason:
            a.included = False
            a.exclusion_reason = reason
            a.published_at = a.api_pub_date
            a.published_at_source = SOURCE_API if a.api_pub_date else None
            a.publisher = publisher_from_domain(a.original_url)
            a.text_status = TEXT_NOT_CHECKED
        else:
            candidates.append(a)
    stats["prescreen_excluded"] = len(articles) - len(candidates)

    # ------------------------------------------------------------------
    # 5. 원문 확인 (병렬)
    # ------------------------------------------------------------------
    enrich = enrich_fn or _default_enricher(settings, fetch_pages)
    total = len(candidates)
    report(STAGE_DETAIL, _P_DETAIL[0], f"기사 상세 확인 중... (0 / {total})")
    if total:
        workers = max(1, settings.article_fetch_workers if fetch_pages else 1)
        local = threading.local()

        def _task(article: NewsArticle) -> NewsArticle:
            session = getattr(local, "session", None)
            if session is None:
                session = new_session()
                local.session = session
            try:
                return enrich(article, session, end)
            except Exception as exc:  # 한 기사 실패가 전체를 멈추지 않게 한다.
                logger.warning("enrich failed %s: %s", article.link, exc)
                article.published_at = article.api_pub_date
                article.published_at_source = SOURCE_API if article.api_pub_date else None
                article.fetch_error = f"상세 확인 실패: {type(exc).__name__}"
                return article

        done = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_task, a) for a in candidates]
            for _ in as_completed(futures):
                done += 1
                if done == total or done % 5 == 0:
                    report(STAGE_DETAIL, _span(_P_DETAIL, done / total), f"기사 상세 확인 중... ({done} / {total})")
    stats["detail_checked"] = total
    stats["text_unavailable"] = sum(1 for a in candidates if a.article_text is None)

    # ------------------------------------------------------------------
    # 6. 정밀 기간 필터 + 규칙 기반 선별 + 동명이인
    # ------------------------------------------------------------------
    report(STAGE_FILTER, _P_JUDGE[0], "기간 확인 및 기사 선별 중...")
    time_excluded = 0
    passed: list[NewsArticle] = []
    for a in candidates:
        reason = time_exclusion(a, start, end)
        if reason:
            a.included = False
            a.exclusion_reason = reason
            time_excluded += 1
            continue
        ok, why = assess(a)
        a.included = ok
        a.rule_reason = why
        a.exclusion_reason = None if ok else why
        if ok:
            passed.append(a)
    stats["time_excluded"] = time_excluded

    # 7. (선택) LLM 판단
    llm_used = False
    if use_llm and settings.llm_configured:
        try:
            llm = llm_client or LLMClient(settings)
        except LLMError as exc:
            warnings.append(f"LLM 사용 불가: {exc}")
            llm = None
        if llm is not None:
            targets = [
                a for a in candidates
                if a.included
                or a.exclusion_reason == R_NOT_RELEVANT
                or (a.rejected_keywords and a.exclusion_reason and "동명이인" in a.exclusion_reason)
            ]
            llm_used = _apply_llm(llm, targets, settings, warnings, report)
    stats["llm_used"] = llm_used

    # ------------------------------------------------------------------
    # 8. 중요도 · 주제 분류
    # ------------------------------------------------------------------
    report(STAGE_CLASSIFY, _P_FINAL[0], "주제 분류 및 중요도 계산 중...")
    included = [a for a in candidates if a.included]
    for a in included:
        a.matched_keywords = sort_keywords(a.matched_keywords)
        a.relevance_score = final_score(a)
        a.topic = classify(a)

    # 9. 같은 사건 중복 보도 묶기
    report(STAGE_DEDUP, _span(_P_FINAL, 0.5), "같은 사건 중복 보도 정리 중...")
    pair_decider = None
    if llm_used and settings.llm_max_pair_checks > 0:
        pair_decider = _llm_pair_decider(llm, settings, warnings)  # type: ignore[arg-type]
    groups = group_same_events(included, pair_decider=pair_decider, max_pair_checks=settings.llm_max_pair_checks)
    for g in groups:  # 그룹 대표의 주제를 그룹 전체에 맞춘다.
        for a in g[1:]:
            a.topic = g[0].topic

    stats["included"] = len(included)
    stats["final"] = len(groups)
    stats["event_merged"] = len(included) - len(groups)
    stats["relevance_excluded"] = sum(1 for a in articles if not a.included) - time_excluded
    report(STAGE_CLASSIFY, 1.0, f"완료: 최종 후보 {len(groups)}건")

    return SearchRun(
        start=start,
        end=end,
        articles=articles,
        stats=stats,
        keyword_api_counts=keyword_api_counts,
        warnings=warnings,
        llm_used=llm_used,
    )


# ---------------------------------------------------------------------------
def _default_enricher(settings: Settings, fetch_pages: bool):
    def _enrich(article: NewsArticle, session, now: datetime) -> NewsArticle:
        return enrich_article(
            article,
            session,
            now,
            timeout=settings.http_timeout,
            retries=2,
            max_chars=settings.max_article_text_chars,
            fetch_pages=fetch_pages,
        )

    return _enrich


def _apply_llm(llm: LLMClient, targets: list[NewsArticle], settings: Settings, warnings: list[str], report: ProgressCallback) -> bool:
    if not targets:
        return True
    size = settings.llm_batch_size
    batches = [targets[i: i + size] for i in range(0, len(targets), size)]
    failures = 0
    successes = 0
    done = 0
    with ThreadPoolExecutor(max_workers=settings.llm_workers) as pool:
        futures = {pool.submit(llm.judge_articles, batch): batch for batch in batches}
        for fut in as_completed(futures):
            batch = futures[fut]
            done += 1
            report(STAGE_FILTER, _span(_P_JUDGE, done / len(batches)), f"LLM 보조 판단 중... ({done} / {len(batches)})")
            try:
                results = fut.result()
            except LLMError as exc:
                failures += 1
                if failures == 1:
                    warnings.append(f"LLM 판단 중 오류가 발생하여 해당 기사는 규칙 기반 판단을 사용했습니다. ({exc})")
                continue
            except Exception as exc:  # pragma: no cover
                failures += 1
                logger.exception("LLM batch failed: %s", exc)
                continue
            successes += 1
            for a in batch:
                j = results.get(a.article_id)
                if j is None:
                    continue
                _merge_llm(a, j)
    return successes > 0


def _merge_llm(a: NewsArticle, j) -> None:
    a.judged_by = "LLM"
    a.llm_reason = j.reason
    a.llm_score = j.relevance_score
    a.llm_topic = j.topic
    if a.rejected_keywords and j.is_person_match is True and j.include:
        # 규칙은 동명이인으로 봤지만 LLM 이 본인으로 판단한 경우 키워드 복원
        for kw in list(a.rejected_keywords):
            if kw in PERSON_RULES:
                a.matched_keywords.append(kw)
                del a.rejected_keywords[kw]
    if j.is_person_match is False:
        for kw in [k for k in a.matched_keywords if k in PERSON_RULES]:
            a.rejected_keywords[kw] = "동명이인(LLM 판단)"
        a.matched_keywords = [k for k in a.matched_keywords if k not in PERSON_RULES]
        if not a.matched_keywords:
            a.included = False
            a.exclusion_reason = f"동명이인(LLM 판단){': ' + j.exclude_reason if j.exclude_reason else ''}"
            return
    if j.include and a.matched_keywords:
        a.included = True
        a.exclusion_reason = None
    elif not j.include:
        a.included = False
        a.exclusion_reason = f"LLM 판단: {j.exclude_reason or '업무 관련성 낮음'}"


def _llm_pair_decider(llm: LLMClient, settings: Settings, warnings: list[str]):
    def _decide(pairs):
        out: dict[int, bool] = {}
        size = 10
        for offset in range(0, len(pairs), size):
            chunk = pairs[offset: offset + size]
            try:
                res = llm.judge_same_events(chunk)
            except LLMError as exc:
                warnings.append(f"LLM 중복 보도 판단 오류로 규칙 기반 판단을 사용했습니다. ({exc})")
                break
            for idx, same in res.items():
                out[offset + idx] = same
        return out

    return _decide


def describe_range(run: SearchRun) -> str:
    return f"{format_kst(run.start)} ~ {format_kst(run.end, '%Y-%m-%d %H:%M:%S')} (KST)"
