"""중복 기사 제거.

1단계: URL 완전 동일 (원문 URL 또는 네이버 기사 ID)
2단계: 정규화 제목 완전 동일 (HTML 태그·중복 공백·앞뒤 공백만 제거)
   → 1·2단계는 '같은 기사'로 보고 하나로 병합하며, 검색된 키워드는 모두 합친다.
3단계: 제목이 조금 다르지만 같은 사건/보도자료를 다룬 기사
   → 제목 유사도 + 본문 유사도(TF-IDF) + 숫자 일치 검사(+선택적 LLM)로 판단하여
     대표 기사 1건만 주제별 보기에 남긴다. 나머지는 '유사 보도'로 연결해 보존한다.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Callable, Iterable

from .models import NewsArticle, RawSearchItem
from .utils import get_domain, normalize_title, normalize_url, to_kst

# ---------------------------------------------------------------------------
# 1·2단계: 같은 기사 병합
# ---------------------------------------------------------------------------


class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def title_key(title: str) -> str:
    return normalize_title(title)


def _item_keys(item: RawSearchItem) -> list[str]:
    keys = []
    for url in (item.original_url, item.naver_url):
        norm = normalize_url(url)
        if norm:
            keys.append("u:" + norm)
    tk = title_key(item.title)
    if tk:
        keys.append("t:" + tk)
    return keys


def _article_id(item: RawSearchItem) -> str:
    basis = normalize_url(item.naver_url) or normalize_url(item.original_url) or title_key(item.title)
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]


def merge_exact_duplicates(items: list[RawSearchItem]) -> list[NewsArticle]:
    """URL 또는 정규화 제목이 같은 검색 결과를 하나의 기사로 합친다."""
    if not items:
        return []
    uf = _UnionFind(len(items))
    first_by_key: dict[str, int] = {}
    for idx, item in enumerate(items):
        for key in _item_keys(item):
            if key in first_by_key:
                uf.union(first_by_key[key], idx)
            else:
                first_by_key[key] = idx

    groups: dict[int, list[int]] = {}
    for idx in range(len(items)):
        groups.setdefault(uf.find(idx), []).append(idx)

    from .filtering import sort_keywords  # 순환 import 방지

    articles: list[NewsArticle] = []
    for members in groups.values():
        group_items = [items[i] for i in members]
        # 대표: 네이버 뉴스 링크가 있는 항목 우선, 그다음 가장 이른 pubDate
        rep = sorted(
            group_items,
            key=lambda it: (
                it.naver_url is None,
                to_kst(it.pub_date).timestamp() if it.pub_date else float("inf"),
            ),
        )[0]
        urls = []
        for it in group_items:
            for u in (it.original_url,):
                if u and u != rep.original_url and u not in urls:
                    urls.append(u)
        description = max((it.description for it in group_items), key=len, default="")
        articles.append(
            NewsArticle(
                article_id=_article_id(rep),
                title=rep.title,
                api_pub_date=rep.pub_date,
                original_url=rep.original_url,
                naver_url=rep.naver_url,
                description=rep.description or description,
                matched_keywords=sort_keywords([it.keyword for it in group_items]),
                alternate_urls=urls,
                domain=get_domain(rep.original_url or rep.naver_url),
            )
        )
    # 같은 article_id 충돌 방지
    seen: dict[str, int] = {}
    for a in articles:
        if a.article_id in seen:
            seen[a.article_id] += 1
            a.article_id = f"{a.article_id}-{seen[a.article_id]}"
        else:
            seen[a.article_id] = 0
    return articles


# ---------------------------------------------------------------------------
# 3단계: 같은 사건(유사 보도) 판단
# ---------------------------------------------------------------------------
_PUNCT_RE = re.compile(r"[^\w가-힣]+")
_BRACKET_PREFIX_RE = re.compile(r"^\s*(?:[\[\(【<][^\]\)】>]{1,12}[\]\)】>]\s*)+")
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


def _tokens(text: str) -> list[str]:
    text = _BRACKET_PREFIX_RE.sub(" ", text or "")
    return [t for t in _PUNCT_RE.split(text.lower()) if t]


def char_bigrams(text: str) -> set[str]:
    """단어 내부 문자 bigram 집합 (띄어쓰기 차이에 강함)."""
    grams: set[str] = set()
    for tok in _tokens(text):
        if len(tok) == 1:
            grams.add(tok)
        for i in range(len(tok) - 1):
            grams.add(tok[i: i + 2])
    return grams


def dice(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return 2 * len(a & b) / (len(a) + len(b))


def title_similarity(t1: str, t2: str) -> float:
    return dice(char_bigrams(t1), char_bigrams(t2))


def title_numbers(title: str) -> set[str]:
    return set(_NUM_RE.findall(_BRACKET_PREFIX_RE.sub(" ", title or "")))


def numbers_compatible(t1: str, t2: str) -> bool:
    """두 제목에 모두 숫자가 있는데 공통 숫자가 하나도 없으면 다른 사건으로 본다 (예: 3호기 vs 4호기)."""
    n1, n2 = title_numbers(t1), title_numbers(t2)
    if n1 and n2 and not (n1 & n2):
        return False
    return True


class TfidfIndex:
    """본문 유사도 계산용 경량 TF-IDF (문자 bigram 기반)."""

    def __init__(self, docs: dict[str, str]):
        self.vectors: dict[str, dict[str, float]] = {}
        tfs: dict[str, Counter] = {}
        df: Counter = Counter()
        for key, text in docs.items():
            grams = Counter()
            for tok in _tokens(text):
                for i in range(len(tok) - 1):
                    grams[tok[i: i + 2]] += 1
            tfs[key] = grams
            df.update(grams.keys())
        n = max(1, len(docs))
        for key, grams in tfs.items():
            vec = {g: (1 + math.log(c)) * math.log((n + 1) / (df[g] + 0.5)) for g, c in grams.items()}
            norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
            self.vectors[key] = {g: v / norm for g, v in vec.items()}

    def cosine(self, a: str, b: str) -> float | None:
        va, vb = self.vectors.get(a), self.vectors.get(b)
        if not va or not vb:
            return None
        if len(va) > len(vb):
            va, vb = vb, va
        return sum(v * vb.get(g, 0.0) for g, v in va.items())


# 판정 임계값
TITLE_STRONG = 0.72
TITLE_MID = 0.45
BODY_STRONG = 0.45
BODY_MID = 0.30

TITLE_MIN = 0.15  # 이보다 제목이 다르면 본문 비교 없이 다른 기사로 본다

# 애매한 쌍 목록을 받아 {쌍 index: 같은 사건 여부} 를 돌려주는 함수 (LLM 등)
PairDecider = Callable[[list[tuple[NewsArticle, NewsArticle]]], dict[int, bool]]


def same_event_score(a: NewsArticle, b: NewsArticle, index: TfidfIndex | None) -> tuple[str, float, float | None]:
    """('same' | 'different' | 'ambiguous', 제목유사도, 본문유사도)."""
    ts = title_similarity(a.title, b.title)
    if ts < TITLE_MIN or not numbers_compatible(a.title, b.title):
        return "different", ts, None
    bs = index.cosine(a.article_id, b.article_id) if index is not None and a.article_text and b.article_text else None
    if ts >= TITLE_STRONG:
        if bs is not None and bs < 0.12:
            return "ambiguous", ts, bs
        return "same", ts, bs
    if ts >= TITLE_MID:
        if bs is None:
            return "ambiguous", ts, bs
        if bs >= BODY_MID:
            return "same", ts, bs
        return ("ambiguous" if bs >= 0.18 else "different"), ts, bs
    if bs is not None and bs >= BODY_STRONG + 0.05:
        return "same", ts, bs
    if bs is not None and bs >= BODY_MID + 0.05:
        return "ambiguous", ts, bs
    return "different", ts, bs


def _sorted_for_grouping(articles: Iterable[NewsArticle]) -> list[NewsArticle]:
    return sorted(
        articles,
        key=lambda a: (
            -(a.relevance_score or 0),
            to_kst(a.published_at).timestamp() if a.published_at else float("inf"),
        ),
    )


def group_same_events(
    articles: Iterable[NewsArticle],
    pair_decider: PairDecider | None = None,
    max_pair_checks: int = 80,
) -> list[list[NewsArticle]]:
    """포함된 기사들을 같은 사건 단위로 묶는다.

    중요도(relevance_score) 높은 순으로 순회하며, 기존 그룹 대표와 같은 사건이면 그 그룹에 넣는다.
    (대표 기준 비교로 연쇄 병합을 막는다.) 반환되는 각 그룹의 첫 번째가 대표 기사다.
    애매한 쌍은 pair_decider(LLM 등)가 있으면 미리 한꺼번에 판단을 받는다.
    """
    items = _sorted_for_grouping(articles)
    index = TfidfIndex({a.article_id: f"{a.title}\n{a.article_text}" for a in items if a.article_text})

    verdicts: dict[tuple[str, str], str] = {}
    ambiguous: list[tuple[float, NewsArticle, NewsArticle]] = []
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            verdict, ts, _ = same_event_score(a, b, index)
            verdicts[(a.article_id, b.article_id)] = verdict
            if verdict == "ambiguous":
                ambiguous.append((ts, a, b))

    if pair_decider is not None and ambiguous and max_pair_checks > 0:
        ambiguous.sort(key=lambda x: -x[0])
        pairs = [(a, b) for _, a, b in ambiguous[:max_pair_checks]]
        try:
            decisions = pair_decider(pairs)
        except Exception:  # LLM 오류 시 규칙 판단 유지
            decisions = {}
        for idx, same in decisions.items():
            if 0 <= idx < len(pairs) and same:
                a, b = pairs[idx]
                verdicts[(a.article_id, b.article_id)] = "same"

    def verdict_of(x: NewsArticle, y: NewsArticle) -> str:
        return verdicts.get((x.article_id, y.article_id)) or verdicts.get((y.article_id, x.article_id)) or "different"

    groups: list[list[NewsArticle]] = []
    for art in items:
        for group in groups:
            if verdict_of(group[0], art) == "same":
                group.append(art)
                break
        else:
            groups.append([art])

    for gid, group in enumerate(groups, start=1):
        rep = group[0]
        for i, art in enumerate(group):
            art.duplicate_group = gid
            art.is_representative = i == 0
            art.similar_article_ids = [x.article_id for x in group if x is not art] if i == 0 else [rep.article_id]
    return groups
