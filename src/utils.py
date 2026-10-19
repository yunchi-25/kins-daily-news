"""공통 유틸리티: 시간 처리, 텍스트 정규화, URL 처리, HTTP 재시도."""

from __future__ import annotations

import html
import logging
import random
import re
import time
from datetime import date, datetime, time as dtime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, urlsplit, urlunsplit, urlencode

import requests

from .config import KST

logger = logging.getLogger(__name__)

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# 시간
# ---------------------------------------------------------------------------
def now_kst() -> datetime:
    return datetime.now(KST)


def to_kst(dt: datetime) -> datetime:
    """timezone 정보가 없으면 KST 로 간주하고, 있으면 KST 로 변환한다."""
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        return dt.replace(tzinfo=KST)
    return dt.astimezone(KST)


def combine_kst(d: date, t: dtime) -> datetime:
    """날짜 + 시간 입력을 KST timezone-aware datetime 으로 만든다."""
    return datetime(d.year, d.month, d.day, t.hour, t.minute, t.second, tzinfo=KST)


def in_time_range(dt: datetime | None, start: datetime, end: datetime) -> bool:
    """start <= dt <= end (경계 포함). 비교는 모두 KST 로 통일한다."""
    if dt is None:
        return False
    return to_kst(start) <= to_kst(dt) <= to_kst(end)


def parse_pubdate(value: str | None) -> datetime | None:
    """네이버 검색 API pubDate (RFC 822, 예: 'Mon, 19 Oct 2026 08:10:00 +0900') 파싱."""
    if not value:
        return None
    try:
        return to_kst(parsedate_to_datetime(value.strip()))
    except (TypeError, ValueError, IndexError):
        return None


_KOREAN_DT_RE = re.compile(
    r"(\d{4})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})\s*일?\.?\s*"
    r"(?:\([월화수목금토일]\)\s*)?"
    r"(오전|오후|AM|PM|am|pm)?\s*(\d{1,2})\s*[:시]\s*(\d{1,2})\s*(?:[:분]\s*(\d{1,2}))?"
)
_DATE_ONLY_RE = re.compile(r"^(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})$")


def parse_datetime_string(value: str | None) -> datetime | None:
    """다양한 형식의 날짜/시간 문자열을 KST datetime 으로 변환한다.

    시간 정보가 없는 '날짜만' 문자열은 게시 시각 추정을 피하기 위해 None 을 반환한다.
    """
    if not value:
        return None
    s = value.strip()
    if not s:
        return None

    # ISO 8601
    iso = s.replace("Z", "+00:00") if s.endswith("Z") else s
    iso = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", iso)  # +0900 -> +09:00
    if "T" in iso or re.match(r"^\d{4}-\d{2}-\d{2} \d{1,2}:\d{2}", iso):
        try:
            return to_kst(datetime.fromisoformat(iso))
        except ValueError:
            pass

    # 14자리 숫자 (YYYYMMDDHHMMSS)
    if re.fullmatch(r"\d{14}", s):
        try:
            return datetime.strptime(s, "%Y%m%d%H%M%S").replace(tzinfo=KST)
        except ValueError:
            return None

    # RFC 822
    if re.match(r"^[A-Za-z]{3},", s):
        return parse_pubdate(s)

    if _DATE_ONLY_RE.match(s):
        return None

    m = _KOREAN_DT_RE.search(s)
    if m:
        y, mo, d, ampm, hh, mm, ss = m.groups()
        hour = int(hh)
        if ampm in ("오후", "PM", "pm") and hour < 12:
            hour += 12
        elif ampm in ("오전", "AM", "am") and hour == 12:
            hour = 0
        try:
            return datetime(int(y), int(mo), int(d), hour, int(mm), int(ss or 0), tzinfo=KST)
        except ValueError:
            return None
    return None


def format_kst(dt: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    if dt is None:
        return "게시 시각 확인 불가"
    return to_kst(dt).strftime(fmt)


def previous_business_day(today: date) -> date:
    d = today - timedelta(days=1)
    while d.weekday() >= 5:  # 토(5), 일(6)
        d -= timedelta(days=1)
    return d


# ---------------------------------------------------------------------------
# 텍스트
# ---------------------------------------------------------------------------
def strip_html(text: str | None) -> str:
    if not text:
        return ""
    return html.unescape(TAG_RE.sub("", text))


def normalize_title(title: str | None) -> str:
    """제목 정규화: HTML 태그, 중복 공백, 앞뒤 공백만 제거한다 (내용 변경 없음)."""
    return WS_RE.sub(" ", strip_html(title)).strip()


def clean_text(text: str | None) -> str:
    if not text:
        return ""
    text = text.replace("\xa0", " ")
    lines = [WS_RE.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


# ---------------------------------------------------------------------------
# URL
# ---------------------------------------------------------------------------
_TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid"}
_NAVER_PATH_RE = re.compile(r"/(?:mnews/)?article/(?:\d+/)?(\d{3})/(\d{6,})")


def get_domain(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return None
    host = host.lower()
    for prefix in ("www.", "m.", "mobile.", "news.", "n.news."):
        if host.startswith(prefix) and host.count(".") >= 2:
            host = host[len(prefix):]
    return host or None


def naver_article_key(url: str | None) -> str | None:
    """네이버 뉴스 URL 에서 언론사(oid)/기사(aid) 를 뽑아 고유 키를 만든다."""
    if not url:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if "naver.com" not in host:
        return None
    m = _NAVER_PATH_RE.search(parts.path)
    if m:
        return f"naver:{m.group(1)}/{m.group(2)}"
    qs = parse_qs(parts.query)
    oid = (qs.get("oid") or [None])[0]
    aid = (qs.get("aid") or [None])[0]
    if oid and aid:
        return f"naver:{oid}/{aid}"
    return None


def is_naver_news_url(url: str | None) -> bool:
    return naver_article_key(url) is not None


def normalize_url(url: str | None) -> str | None:
    """URL 동일성 비교용 정규화 (scheme/host 소문자, fragment·추적 파라미터·끝 슬래시 제거)."""
    if not url:
        return None
    url = url.strip()
    if not url:
        return None
    key = naver_article_key(url)
    if key:
        return key
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    query_pairs = [
        (k, v)
        for k, vs in parse_qs(parts.query, keep_blank_values=True).items()
        if k.lower() not in _TRACKING_PARAMS
        for v in vs
    ]
    query = urlencode(sorted(query_pairs))
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, query, ""))


def is_http_url(url: str | None) -> bool:
    return bool(url) and url.strip().lower().startswith(("http://", "https://"))


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
RETRY_STATUSES = {429, 500, 502, 503, 504}


class HttpError(Exception):
    def __init__(self, message: str, status: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status = status
        self.body = body


def new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.6",
        }
    )
    return s


def request_with_retry(
    session: requests.Session,
    method: str,
    url: str,
    *,
    timeout: float = 10.0,
    retries: int = 3,
    backoff: float = 0.8,
    retry_statuses: set[int] = RETRY_STATUSES,
    **kwargs,
) -> requests.Response:
    """timeout + exponential backoff 재시도. 재시도 불가 상태코드는 즉시 반환한다."""
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            resp = session.request(method, url, timeout=timeout, **kwargs)
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc
        except requests.RequestException as exc:  # 잘못된 URL 등: 재시도 의미 없음
            raise HttpError(f"요청 실패: {exc}") from exc
        else:
            if resp.status_code in retry_statuses and attempt < retries - 1:
                last_exc = HttpError(f"HTTP {resp.status_code}", resp.status_code)
            else:
                return resp
        if attempt < retries - 1:
            time.sleep(backoff * (2**attempt) + random.uniform(0, 0.2))
    if isinstance(last_exc, requests.Timeout):
        raise HttpError(f"응답 시간 초과({timeout:.0f}초)") from last_exc
    if isinstance(last_exc, requests.ConnectionError):
        raise HttpError("네트워크 연결 실패") from last_exc
    if isinstance(last_exc, HttpError):
        raise last_exc
    raise HttpError("요청 실패")
