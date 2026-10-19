from datetime import date, datetime, time, timedelta, timezone

import pytest

from src.article_parser import choose_published_at
from src.config import KST
from src.filtering import R_NO_TIME, R_OUT_OF_RANGE, time_exclusion
from src.models import SOURCE_API, SOURCE_NAVER, SOURCE_ORIGINAL, ArticleDetail
from src.utils import combine_kst, in_time_range, parse_datetime_string, parse_pubdate, previous_business_day, to_kst
from tests.helpers import make_article

START = datetime(2026, 10, 16, 11, 0, 0, tzinfo=KST)
END = datetime(2026, 10, 19, 8, 13, 27, tzinfo=KST)


def test_before_start_excluded():
    assert not in_time_range(START - timedelta(seconds=1), START, END)


def test_exact_start_included():
    assert in_time_range(START, START, END)


def test_exact_end_included():
    assert in_time_range(END, START, END)


def test_after_end_excluded():
    assert not in_time_range(END + timedelta(seconds=1), START, END)


def test_none_excluded():
    assert not in_time_range(None, START, END)


def test_timezone_conversion_utc():
    # 02:00 UTC == 11:00 KST → 시작 시각과 정확히 일치 → 포함
    utc_dt = datetime(2026, 10, 16, 2, 0, 0, tzinfo=timezone.utc)
    assert to_kst(utc_dt) == START
    assert in_time_range(utc_dt, START, END)
    # 01:59:59 UTC == 10:59:59 KST → 제외
    assert not in_time_range(utc_dt - timedelta(seconds=1), START, END)


def test_naive_datetime_treated_as_kst():
    naive = datetime(2026, 10, 16, 11, 0, 0)
    assert to_kst(naive).tzinfo is not None
    assert in_time_range(naive, START, END)


def test_parse_pubdate_kst_and_utc():
    dt = parse_pubdate("Fri, 16 Oct 2026 11:00:00 +0900")
    assert dt == START and dt.utcoffset() == timedelta(hours=9)
    dt2 = parse_pubdate("Fri, 16 Oct 2026 02:00:00 +0000")
    assert dt2 == START
    assert dt2.tzinfo is not None and dt2.utcoffset() == timedelta(hours=9)
    assert parse_pubdate("잘못된 값") is None
    assert parse_pubdate(None) is None


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2026-10-18T16:42:11+09:00", datetime(2026, 10, 18, 16, 42, 11, tzinfo=KST)),
        ("2026-10-18T07:42:11Z", datetime(2026, 10, 18, 16, 42, 11, tzinfo=KST)),
        ("2026-10-18T16:42:11+0900", datetime(2026, 10, 18, 16, 42, 11, tzinfo=KST)),
        ("2026-10-18 16:42:11", datetime(2026, 10, 18, 16, 42, 11, tzinfo=KST)),
        ("2026.10.18 16:42", datetime(2026, 10, 18, 16, 42, tzinfo=KST)),
        ("2026.10.18. 오후 4:42", datetime(2026, 10, 18, 16, 42, tzinfo=KST)),
        ("2026년 10월 18일 오전 12:05", datetime(2026, 10, 18, 0, 5, tzinfo=KST)),
        ("20261018164211", datetime(2026, 10, 18, 16, 42, 11, tzinfo=KST)),
    ],
)
def test_parse_datetime_string(value, expected):
    assert parse_datetime_string(value) == expected


def test_date_only_is_not_guessed():
    # 시간 정보가 없는 날짜는 게시 시각으로 추정하지 않는다.
    assert parse_datetime_string("2026-10-18") is None
    assert parse_datetime_string("") is None


def test_combine_kst():
    dt = combine_kst(date(2026, 10, 16), time(11, 0))
    assert dt == START and dt.tzinfo is not None


def test_time_exclusion_uses_first_published():
    # 최초 게시가 범위 이전이고 수정 시각만 범위 안 → 제외
    a = make_article("원전 기사", published=START - timedelta(hours=1))
    a.modified_at = START + timedelta(hours=2)
    assert time_exclusion(a, START, END) == R_OUT_OF_RANGE
    b = make_article("원전 기사", published=START)
    assert time_exclusion(b, START, END) is None
    c = make_article("원전 기사")
    c.published_at = None
    assert time_exclusion(c, START, END) == R_NO_TIME


def _detail(source, dt, confidence="structured"):
    return ArticleDetail(url="https://x", source=source, ok=True, published_at=dt, published_confidence=confidence)


def test_choose_published_priority():
    api = datetime(2026, 10, 18, 10, 0, tzinfo=KST)
    original = _detail(SOURCE_ORIGINAL, datetime(2026, 10, 18, 9, 30, tzinfo=KST))
    naver = _detail(SOURCE_NAVER, datetime(2026, 10, 18, 9, 50, tzinfo=KST))
    assert choose_published_at(original, naver, api, END) == (original.published_at, SOURCE_ORIGINAL)
    assert choose_published_at(None, naver, api, END) == (naver.published_at, SOURCE_NAVER)
    assert choose_published_at(None, None, api, END) == (api, SOURCE_API)
    assert choose_published_at(None, None, None, END) == (None, None)


def test_choose_published_rejects_implausible_values():
    api = datetime(2026, 10, 18, 10, 0, tzinfo=KST)
    # 미래 시각
    future = _detail(SOURCE_ORIGINAL, END + timedelta(hours=1))
    assert choose_published_at(future, None, api, END)[1] == SOURCE_API
    # API 시각보다 크게 늦음(수정 시각 오인 가능)
    late = _detail(SOURCE_ORIGINAL, api + timedelta(hours=12))
    assert choose_published_at(late, None, api, END)[1] == SOURCE_API
    # 본문 문자열 기반 시각이 API 보다 며칠 이른 경우(다른 기사 날짜 오인 가능)
    text_old = _detail(SOURCE_ORIGINAL, api - timedelta(days=5), confidence="text")
    assert choose_published_at(text_old, None, api, END)[1] == SOURCE_API
    # 구조화 메타데이터 기반이면 이른 시각도 그대로 신뢰 → 이후 기간 필터에서 제외됨
    meta_old = _detail(SOURCE_ORIGINAL, api - timedelta(days=5))
    assert choose_published_at(meta_old, None, api, END) == (meta_old.published_at, SOURCE_ORIGINAL)


def test_previous_business_day():
    assert previous_business_day(date(2026, 10, 19)) == date(2026, 10, 16)  # 월 → 금
    assert previous_business_day(date(2026, 10, 21)) == date(2026, 10, 20)
