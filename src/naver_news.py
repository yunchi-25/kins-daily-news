"""네이버 뉴스 검색 API 클라이언트.

- 공식 API: https://openapi.naver.com/v1/search/news.json
- 최신순(sort=date) 조회, display 최대 100건, start 최대 1000.
- 결과가 검색 시작 시각보다 오래된 기사로 넘어가면 다음 페이지 요청을 중단한다.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime

import requests

from .models import RawSearchItem
from .utils import HttpError, is_naver_news_url, new_session, normalize_title, parse_pubdate, request_with_retry, strip_html, to_kst

logger = logging.getLogger(__name__)

NAVER_NEWS_API_URL = "https://openapi.naver.com/v1/search/news.json"


class NaverApiError(Exception):
    """네이버 API 호출 오류 (특정 키워드 단위)."""


class NaverAuthError(NaverApiError):
    """인증 실패: 전체 검색을 중단해야 하는 오류."""


@dataclass
class KeywordSearchResult:
    keyword: str
    items: list[RawSearchItem] = field(default_factory=list)
    api_calls: int = 0
    total_fetched: int = 0  # API 가 반환한 전체 건수 (기간 필터 전)
    reached_limit: bool = False  # API 최대 조회 한도에 걸려 일부 누락 가능
    error: str | None = None


class NaverNewsClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        timeout: float = 10.0,
        retries: int = 3,
        display: int = 100,
        max_start: int = 1000,
        request_interval: float = 0.12,
        session: requests.Session | None = None,
    ):
        if not client_id or not client_secret:
            raise NaverAuthError(
                "네이버 뉴스 API 인증정보가 설정되어 있지 않습니다. "
                ".env 파일에서 NAVER_CLIENT_ID와 NAVER_CLIENT_SECRET을 설정해주세요."
            )
        self.client_id = client_id
        self.client_secret = client_secret
        self.timeout = timeout
        self.retries = retries
        self.display = max(10, min(100, display))
        self.max_start = max(1, min(1000, max_start))
        self.request_interval = request_interval
        self.session = session or new_session()

    # ------------------------------------------------------------------
    def _request_page(self, keyword: str, start: int) -> dict:
        headers = {
            "X-Naver-Client-Id": self.client_id,
            "X-Naver-Client-Secret": self.client_secret,
        }
        params = {"query": keyword, "display": self.display, "start": start, "sort": "date"}
        try:
            resp = request_with_retry(
                self.session,
                "GET",
                NAVER_NEWS_API_URL,
                headers=headers,
                params=params,
                timeout=self.timeout,
                retries=self.retries,
            )
        except HttpError as exc:
            raise NaverApiError(f"네이버 API 요청 실패: {exc}") from exc

        if resp.status_code in (401, 403):
            raise NaverAuthError(
                "네이버 API 인증에 실패했습니다. NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 값과 "
                "네이버 개발자센터 애플리케이션의 '검색' API 사용 설정을 확인해주세요."
                f" (HTTP {resp.status_code}: {_error_message(resp)})"
            )
        if resp.status_code == 429:
            raise NaverApiError("네이버 API 호출 한도를 초과했습니다. 잠시 후 다시 시도해주세요.")
        if resp.status_code != 200:
            raise NaverApiError(f"네이버 API 오류 (HTTP {resp.status_code}: {_error_message(resp)})")
        try:
            return resp.json()
        except ValueError as exc:
            raise NaverApiError("네이버 API 응답을 해석할 수 없습니다.") from exc

    # ------------------------------------------------------------------
    def search_keyword(
        self,
        keyword: str,
        start_dt: datetime,
        end_dt: datetime,
        max_results: int = 1000,
    ) -> KeywordSearchResult:
        """키워드 1개를 최신순으로 검색한다.

        반환 항목은 기간 필터 전 원본이며, 기간 필터는 파이프라인에서 적용한다.
        페이지의 가장 오래된 기사가 start_dt 보다 이전이면 더 이상 조회하지 않는다.
        """
        result = KeywordSearchResult(keyword=keyword)
        start_dt = to_kst(start_dt)
        max_results = max(self.display, min(max_results, self.max_start + self.display - 1))
        start = 1
        rank = 0
        while start <= self.max_start and start <= max_results:
            if result.api_calls > 0 and self.request_interval > 0:
                time.sleep(self.request_interval)
            data = self._request_page(keyword, start)
            result.api_calls += 1
            raw_items = data.get("items") or []
            if not raw_items:
                break

            oldest: datetime | None = None
            for raw in raw_items:
                rank += 1
                pub = parse_pubdate(raw.get("pubDate"))
                if pub is not None and (oldest is None or pub < oldest):
                    oldest = pub
                result.items.append(_to_raw_item(keyword, raw, pub, rank))
            result.total_fetched += len(raw_items)

            # 중단 조건 1: 마지막 페이지
            if len(raw_items) < self.display:
                break
            # 중단 조건 2: 이미 검색 시작 시각 이전 기사까지 내려옴 (최신순 정렬)
            if oldest is not None and oldest < start_dt:
                break
            total = int(data.get("total") or 0)
            if total and start + self.display > total:
                break
            next_start = start + self.display
            if next_start > self.max_start or next_start > max_results:
                # 기간 내 기사가 더 남아있을 수 있으나 API 한도로 조회 불가
                result.reached_limit = True
                break
            start = next_start
        return result


def _to_raw_item(keyword: str, raw: dict, pub: datetime | None, rank: int) -> RawSearchItem:
    original = (raw.get("originallink") or "").strip() or None
    link = (raw.get("link") or "").strip() or None
    naver_url = link if is_naver_news_url(link) else None
    if original is None and link and not naver_url:
        original = link
    return RawSearchItem(
        keyword=keyword,
        title=normalize_title(raw.get("title")),
        original_url=original,
        naver_url=naver_url,
        description=normalize_title(strip_html(raw.get("description"))),
        pub_date=pub,
        rank=rank,
    )


def _error_message(resp: requests.Response) -> str:
    try:
        data = resp.json()
        return str(data.get("errorMessage") or data.get("errorCode") or "")[:200]
    except ValueError:
        return (resp.text or "")[:200]
