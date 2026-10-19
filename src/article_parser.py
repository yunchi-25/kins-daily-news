"""기사 원문 / 네이버 뉴스 페이지 파싱.

게시 시각 우선순위
1. 기사 원문 페이지의 최초 입력 시각
2. 네이버 뉴스 페이지의 최초 게시(입력) 시각
3. 네이버 검색 API pubDate

수정 시각은 게시 시각으로 사용하지 않는다. 신뢰할 수 없는 값(미래 시각 등)은 버린다.
원문을 가져오지 못하면 본문을 만들어내지 않고 '본문 확인 불가' 로 남긴다.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

from .config import KNOWN_PUBLISHER_DOMAINS
from .models import (
    SOURCE_API,
    SOURCE_NAVER,
    SOURCE_ORIGINAL,
    TEXT_OK,
    TEXT_UNAVAILABLE,
    ArticleDetail,
    NewsArticle,
)
from .utils import (
    HttpError,
    clean_text,
    get_domain,
    is_http_url,
    is_naver_news_url,
    parse_datetime_string,
    request_with_retry,
    to_kst,
)

logger = logging.getLogger(__name__)

MIN_TEXT_LEN = 100
MAX_HTML_BYTES = 3_000_000
# 원문 게시 시각이 API pubDate 보다 이 시간 이상 늦으면 수정 시각 등을 잘못 읽은 것으로 간주
MAX_LATER_THAN_API = timedelta(hours=6)
FUTURE_TOLERANCE = timedelta(minutes=5)

# ---------------------------------------------------------------------------
# 간단한 프로세스 내 캐시 (같은 URL 을 반복 검색할 때 재요청 방지)
# ---------------------------------------------------------------------------
_CACHE_TTL_SECONDS = 6 * 3600
_cache: dict[str, tuple[float, ArticleDetail]] = {}
_cache_lock = threading.Lock()


def _cache_get(url: str) -> ArticleDetail | None:
    with _cache_lock:
        hit = _cache.get(url)
        if hit and time.time() - hit[0] < _CACHE_TTL_SECONDS:
            return hit[1]
    return None


def _cache_put(url: str, detail: ArticleDetail) -> None:
    with _cache_lock:
        if len(_cache) > 20000:
            _cache.clear()
        _cache[url] = (time.time(), detail)


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


# ---------------------------------------------------------------------------
# HTML 가져오기
# ---------------------------------------------------------------------------
_CHARSET_ALIASES = {"euc-kr": "cp949", "ks_c_5601-1987": "cp949", "ksc5601": "cp949", "x-windows-949": "cp949"}


def _decode(resp: requests.Response) -> str:
    raw = resp.content[:MAX_HTML_BYTES]
    enc = None
    ctype = resp.headers.get("content-type", "")
    m = re.search(r"charset=([\w\-]+)", ctype, re.I)
    if m:
        enc = m.group(1)
    if not enc:
        m2 = re.search(rb"<meta[^>]+charset=[\"']?([\w\-]+)", raw[:8000], re.I)
        if m2:
            enc = m2.group(1).decode("ascii", "ignore")
    if not enc:
        enc = resp.apparent_encoding or "utf-8"
    enc = _CHARSET_ALIASES.get(enc.lower(), enc)
    try:
        return raw.decode(enc, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def fetch_html(session: requests.Session, url: str, timeout: float = 10.0, retries: int = 2) -> str:
    resp = request_with_retry(session, "GET", url, timeout=timeout, retries=retries, allow_redirects=True)
    if resp.status_code >= 400:
        raise HttpError(f"HTTP {resp.status_code}", resp.status_code)
    ctype = resp.headers.get("content-type", "").lower()
    if ctype and "html" not in ctype and "xml" not in ctype:
        raise HttpError(f"HTML 문서가 아님({ctype.split(';')[0]})")
    return _decode(resp)


# ---------------------------------------------------------------------------
# 공통 파싱 도우미
# ---------------------------------------------------------------------------
def _soup(html_text: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html_text, "lxml")
    except Exception:  # pragma: no cover - lxml 미설치 대비
        return BeautifulSoup(html_text, "html.parser")


def _meta(soup: BeautifulSoup, *keys: str) -> str | None:
    for key in keys:
        for attr in ("property", "name", "itemprop"):
            tag = soup.find("meta", attrs={attr: key})
            if tag and tag.get("content"):
                value = str(tag["content"]).strip()
                if value:
                    return value
    return None


def _iter_jsonld(soup: BeautifulSoup):
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text() or ""
        try:
            data = json.loads(raw.strip())
        except (ValueError, TypeError):
            continue
        stack = [data]
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                if "@graph" in item:
                    stack.append(item["@graph"])
                yield item


_NEWS_LD_TYPES = {"NewsArticle", "Article", "ReportageNewsArticle", "AnalysisNewsArticle", "BlogPosting", "WebPage"}


def _ld_type_ok(item: dict) -> bool:
    t = item.get("@type")
    if isinstance(t, list):
        return any(x in _NEWS_LD_TYPES for x in t)
    return t in _NEWS_LD_TYPES


_REMOVE_TAGS = ("script", "style", "noscript", "iframe", "header", "footer", "nav", "aside", "form", "button", "svg", "figcaption")

_BODY_SELECTORS = (
    "#dic_area",
    "#newsct_article",
    "[itemprop=articleBody]",
    "#article-view-content-div",
    "#articleBody",
    "#article_body",
    "#articleText",
    "#articletxt",
    "#news_body_area",
    "#newsEndContents",
    "#textBody",
    "#article-body",
    ".article_body",
    ".article-body",
    ".article_txt",
    ".news_body",
    ".news_cnt_detail_wrap",
    ".view_con",
    ".story-news",
    "article",
)

_FIRST_INPUT_RE = re.compile(
    r"(?:최초\s*)?(?:입력|기사\s*입력|등록|게재|발행|작성)\s*(?:일시|시간|일)?\s*[:：]?\s*"
    r"(\d{4}\s*[.\-/년]\s*\d{1,2}\s*[.\-/월]\s*\d{1,2}\s*일?\.?\s*(?:\([월화수목금토일]\)\s*)?(?:오전|오후)?\s*\d{1,2}\s*[:시]\s*\d{1,2}(?:\s*[:분]\s*\d{1,2})?)"
)
_APPROVED_RE = re.compile(
    r"승인\s*[:：]?\s*(\d{4}\s*[.\-/]\s*\d{1,2}\s*[.\-/]\s*\d{1,2}\s*\d{1,2}:\d{2}(?::\d{2})?)"
)
_MODIFIED_RE = re.compile(
    r"(?:수정|업데이트|최종\s*수정)\s*(?:일시|시간)?\s*[:：]?\s*"
    r"(\d{4}\s*[.\-/년]\s*\d{1,2}\s*[.\-/월]\s*\d{1,2}\s*일?\.?\s*(?:오전|오후)?\s*\d{1,2}\s*[:시]\s*\d{1,2}(?:\s*[:분]\s*\d{1,2})?)"
)


def _extract_body(soup: BeautifulSoup, max_chars: int) -> str | None:
    # 1) JSON-LD articleBody
    for item in _iter_jsonld(soup):
        body = item.get("articleBody")
        if isinstance(body, str):
            text = clean_text(body)
            if len(text) >= MIN_TEXT_LEN * 2:
                return text[:max_chars]
    for tag_name in _REMOVE_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()
    # 2) 대표적인 본문 영역 selector
    for selector in _BODY_SELECTORS:
        try:
            node = soup.select_one(selector)
        except Exception:
            continue
        if node is None:
            continue
        text = clean_text(node.get_text("\n"))
        if len(text) >= MIN_TEXT_LEN * 2:
            return text[:max_chars]
    # 3) 문단(<p>) 모음
    paragraphs = [clean_text(p.get_text(" ")) for p in soup.find_all("p")]
    paragraphs = [p for p in paragraphs if len(p) >= 30]
    text = "\n".join(paragraphs)
    if len(text) >= MIN_TEXT_LEN:
        return text[:max_chars]
    return None


def publisher_from_domain(url: str | None) -> str | None:
    domain = get_domain(url)
    if not domain:
        return None
    parts = domain.split(".")
    for i in range(len(parts) - 1):
        candidate = ".".join(parts[i:])
        if candidate in KNOWN_PUBLISHER_DOMAINS:
            return KNOWN_PUBLISHER_DOMAINS[candidate]
    return None


def _clean_publisher(name: str | None) -> str | None:
    if not name:
        return None
    name = re.split(r"\s+[|\-:]\s+", name.strip())[0].strip()
    name = re.sub(r"\s*(네이버\s*뉴스|NAVER)\s*$", "", name).strip()
    if not name or len(name) > 30 or name.lower().startswith("http"):
        return None
    if name.isascii() and "." in name and " " not in name:  # 도메인 문자열
        return None
    return name


# ---------------------------------------------------------------------------
# 네이버 뉴스 페이지
# ---------------------------------------------------------------------------
def parse_naver_article(html_text: str, url: str, max_chars: int = 6000) -> ArticleDetail:
    soup = _soup(html_text)
    detail = ArticleDetail(url=url, source=SOURCE_NAVER)

    published = soup.select_one("._ARTICLE_DATE_TIME[data-date-time]")
    if published:
        detail.published_at = parse_datetime_string(published.get("data-date-time"))
        detail.published_confidence = "structured" if detail.published_at else None
    modified = soup.select_one("._ARTICLE_MODIFY_DATE_TIME")
    if modified:
        detail.modified_at = parse_datetime_string(
            modified.get("data-modify-date-time") or modified.get("data-date-time")
        )
    if detail.published_at is None:
        # 구형/기타 레이아웃: "입력 2026.10.18. 오후 4:42"
        text_head = soup.get_text(" ")[:5000]
        m = _FIRST_INPUT_RE.search(text_head)
        if m:
            detail.published_at = parse_datetime_string(m.group(1))
            detail.published_confidence = "text" if detail.published_at else None

    publisher = None
    logo = soup.select_one(".media_end_head_top_logo img[alt], img.media_end_head_top_logo_img[alt]")
    if logo is not None:
        publisher = logo.get("alt")
    if not publisher:
        publisher = _meta(soup, "og:article:author", "twitter:creator")
    detail.publisher = _clean_publisher(publisher)

    title_node = soup.select_one("#title_area, h2.media_end_head_headline")
    detail.title = clean_text(title_node.get_text(" ")) if title_node else _meta(soup, "og:title")

    body_node = soup.select_one("#dic_area") or soup.select_one("#newsct_article")
    text = None
    if body_node is not None:
        for tag_name in ("script", "style", "em.img_desc", "span.end_photo_org"):
            for tag in body_node.select(tag_name):
                tag.decompose()
        text = clean_text(body_node.get_text("\n"))
    if not text or len(text) < MIN_TEXT_LEN:
        text = _extract_body(soup, max_chars)
    detail.text = text[:max_chars] if text and len(text) >= MIN_TEXT_LEN else None
    detail.ok = True
    return detail


# ---------------------------------------------------------------------------
# 언론사 원문 페이지
# ---------------------------------------------------------------------------
def parse_generic_article(html_text: str, url: str, max_chars: int = 6000) -> ArticleDetail:
    soup = _soup(html_text)
    detail = ArticleDetail(url=url, source=SOURCE_ORIGINAL)

    candidates: list[datetime] = []
    modified_candidates: list[datetime] = []
    for item in _iter_jsonld(soup):
        if not _ld_type_ok(item):
            continue
        dp = parse_datetime_string(str(item.get("datePublished") or "")) if item.get("datePublished") else None
        if dp:
            candidates.append(dp)
        dm = parse_datetime_string(str(item.get("dateModified") or "")) if item.get("dateModified") else None
        if dm:
            modified_candidates.append(dm)
        if not detail.publisher:
            pub = item.get("publisher")
            if isinstance(pub, dict) and isinstance(pub.get("name"), str):
                detail.publisher = _clean_publisher(pub["name"])

    meta_pub = _meta(
        soup,
        "article:published_time",
        "og:article:published_time",
        "datePublished",
        "pubdate",
        "publishdate",
        "publish-date",
        "article.published",
        "dcterms.created",
    )
    dt = parse_datetime_string(meta_pub)
    if dt:
        candidates.append(dt)
    meta_mod = _meta(soup, "article:modified_time", "og:updated_time", "dateModified", "lastmod")
    dt = parse_datetime_string(meta_mod)
    if dt:
        modified_candidates.append(dt)

    time_tag = soup.find("time", attrs={"datetime": True})
    if time_tag is not None:
        dt = parse_datetime_string(time_tag.get("datetime"))
        if dt and not candidates:
            candidates.append(dt)

    page_text = soup.get_text(" ")
    page_text = re.sub(r"\s+", " ", page_text)[:20000]
    text_dt = None
    m = _FIRST_INPUT_RE.search(page_text)
    if m:
        text_dt = parse_datetime_string(m.group(1))
    if text_dt is None and not candidates:
        m = _APPROVED_RE.search(page_text)
        if m:
            text_dt = parse_datetime_string(m.group(1))
    m = _MODIFIED_RE.search(page_text)
    if m:
        dt = parse_datetime_string(m.group(1))
        if dt:
            modified_candidates.append(dt)

    # 최초 게시 시각
    # - 메타데이터(구조화) 값이 있으면 그중 가장 이른 시각을 사용한다.
    #   본문 '입력' 문자열이 그보다 이르면서 24시간 이내이면 최초 입력 시각으로 본다.
    # - 메타데이터가 없을 때만 본문 문자열 시각을 낮은 신뢰도로 사용한다.
    #   (사이드바의 다른 기사 날짜를 잘못 읽을 위험 때문)
    if candidates:
        published = min(candidates)
        if text_dt is not None and published - timedelta(hours=24) <= text_dt < published:
            published = text_dt
        detail.published_at = published
        detail.published_confidence = "structured"
    elif text_dt is not None:
        detail.published_at = text_dt
        detail.published_confidence = "text"
    detail.modified_at = max(modified_candidates) if modified_candidates else None

    if not detail.publisher:
        detail.publisher = _clean_publisher(_meta(soup, "og:site_name", "application-name"))
    detail.title = _meta(soup, "og:title") or (clean_text(soup.title.get_text()) if soup.title else None)
    detail.text = _extract_body(soup, max_chars)
    detail.ok = True
    return detail


# ---------------------------------------------------------------------------
# 페이지 조회 (캐시 포함)
# ---------------------------------------------------------------------------
def fetch_article_detail(
    session: requests.Session, url: str, *, timeout: float = 10.0, retries: int = 2, max_chars: int = 6000
) -> ArticleDetail:
    is_naver = is_naver_news_url(url)
    source = SOURCE_NAVER if is_naver else SOURCE_ORIGINAL
    if not is_http_url(url):
        return ArticleDetail(url=url, source=source, ok=False, error="유효하지 않은 URL")
    cached = _cache_get(url)
    if cached is not None:
        return cached
    try:
        html_text = fetch_html(session, url, timeout=timeout, retries=retries)
        detail = parse_naver_article(html_text, url, max_chars) if is_naver else parse_generic_article(html_text, url, max_chars)
    except HttpError as exc:
        detail = ArticleDetail(url=url, source=source, ok=False, error=str(exc))
    except Exception as exc:  # 파싱 오류가 전체 검색을 중단시키지 않도록 한다.
        logger.warning("기사 파싱 실패 %s: %s", url, exc)
        detail = ArticleDetail(url=url, source=source, ok=False, error=f"본문 파싱 실패: {type(exc).__name__}")
    # 일시적 네트워크 오류는 캐시하지 않는다.
    if detail.ok or (detail.error and "HTTP 4" in detail.error):
        _cache_put(url, detail)
    return detail


TEXT_TIME_MAX_EARLIER = timedelta(days=2)


def _plausible(dt: datetime | None, api_pub: datetime | None, now: datetime, confidence: str | None = "structured") -> bool:
    if dt is None:
        return False
    dt = to_kst(dt)
    if dt > to_kst(now) + FUTURE_TOLERANCE:
        return False
    if api_pub is not None and dt > to_kst(api_pub) + MAX_LATER_THAN_API:
        return False
    if confidence == "text" and api_pub is not None and dt < to_kst(api_pub) - TEXT_TIME_MAX_EARLIER:
        # 본문 문자열에서 읽은 시각이 API 시각보다 크게 이르면 다른 기사 날짜일 가능성이 높다.
        return False
    return True


def _detail_time_ok(detail: ArticleDetail | None, api_pub: datetime | None, now: datetime) -> bool:
    return detail is not None and detail.ok and _plausible(detail.published_at, api_pub, now, detail.published_confidence)


def choose_published_at(
    original: ArticleDetail | None,
    naver: ArticleDetail | None,
    api_pub: datetime | None,
    now: datetime,
) -> tuple[datetime | None, str | None]:
    """게시 시각 우선순위(원문 > 네이버 > API)에 따라 최초 게시 시각을 고른다."""
    if _detail_time_ok(original, api_pub, now):
        return to_kst(original.published_at), SOURCE_ORIGINAL
    if _detail_time_ok(naver, api_pub, now):
        return to_kst(naver.published_at), SOURCE_NAVER
    if api_pub is not None and to_kst(api_pub) <= to_kst(now) + FUTURE_TOLERANCE:
        return to_kst(api_pub), SOURCE_API
    return None, None


def enrich_article(
    article: NewsArticle,
    session: requests.Session,
    now: datetime,
    *,
    timeout: float = 10.0,
    retries: int = 2,
    max_chars: int = 6000,
    fetch_pages: bool = True,
) -> NewsArticle:
    """원문/네이버 페이지를 확인해 게시 시각, 언론사, 본문을 채운다."""
    original_detail: ArticleDetail | None = None
    naver_detail: ArticleDetail | None = None
    errors: list[str] = []

    if fetch_pages:
        if article.original_url and not is_naver_news_url(article.original_url):
            original_detail = fetch_article_detail(session, article.original_url, timeout=timeout, retries=retries, max_chars=max_chars)
            if not original_detail.ok:
                errors.append(f"원문: {original_detail.error}")

        need_naver = article.naver_url and (
            original_detail is None
            or not original_detail.ok
            or not _detail_time_ok(original_detail, article.api_pub_date, now)
            or not original_detail.text
            or not (original_detail.publisher or publisher_from_domain(article.original_url))
        )
        if need_naver:
            naver_detail = fetch_article_detail(session, article.naver_url, timeout=timeout, retries=retries, max_chars=max_chars)
            if not naver_detail.ok:
                errors.append(f"네이버: {naver_detail.error}")

    published, source = choose_published_at(original_detail, naver_detail, article.api_pub_date, now)
    article.published_at = published
    article.published_at_source = source
    for d in (original_detail, naver_detail):
        if d is not None and d.ok and d.modified_at is not None:
            article.modified_at = to_kst(d.modified_at)
            break

    publisher = None
    if naver_detail is not None and naver_detail.ok and naver_detail.publisher:
        publisher = naver_detail.publisher
    publisher = publisher or publisher_from_domain(article.original_url)
    if not publisher and original_detail is not None and original_detail.ok:
        publisher = original_detail.publisher
    article.publisher = publisher

    text = None
    for d in (original_detail, naver_detail):
        if d is not None and d.ok and d.text and len(d.text) >= MIN_TEXT_LEN:
            text = d.text
            break
    article.article_text = text
    article.text_status = TEXT_OK if text else TEXT_UNAVAILABLE
    article.fetch_error = "; ".join(errors) if errors else None
    if not fetch_pages:
        article.fetch_error = "원문 확인 생략(옵션)"
    return article
