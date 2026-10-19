from datetime import datetime

from src.config import KST
from src.deduplication import group_same_events, merge_exact_duplicates, numbers_compatible, title_similarity
from src.models import RawSearchItem
from src.utils import normalize_title
from tests.helpers import make_article

PUB = datetime(2026, 10, 18, 16, 42, tzinfo=KST)


def raw(keyword, title, original=None, naver=None, pub=PUB, desc=""):
    return RawSearchItem(keyword=keyword, title=title, original_url=original, naver_url=naver, description=desc, pub_date=pub)


def test_same_url_merged_and_keywords_kept():
    items = [
        raw("원전", "원안위, 고리 3호기 계속운전 승인", "https://www.yna.co.kr/view/AKR1", "https://n.news.naver.com/mnews/article/001/0000000001"),
        raw("원자력", "원안위, 고리 3호기 계속운전 승인", "https://yna.co.kr/view/AKR1/", None),
        raw("SMR", "원안위, 고리 3호기 계속운전 승인", None, "https://n.news.naver.com/article/001/0000000001?sid=101"),
    ]
    merged = merge_exact_duplicates(items)
    assert len(merged) == 1
    assert merged[0].matched_keywords == ["원자력", "원전", "SMR"]  # 설정 순서 유지
    assert merged[0].naver_url is not None


def test_same_title_merged():
    items = [
        raw("원안위", "<b>원안위</b>, 고리 3호기  계속운전 승인 ", "https://a.com/1"),
        raw("원전", "원안위, 고리 3호기 계속운전 승인", "https://b.com/2"),
    ]
    items[0].title = normalize_title(items[0].title)
    merged = merge_exact_duplicates(items)
    assert len(merged) == 1
    assert set(merged[0].matched_keywords) == {"원안위", "원전"}
    assert merged[0].alternate_urls  # 다른 URL 보존


def test_title_normalization_only_strips_markup_and_spaces():
    assert normalize_title("  <b>원안위</b>,   고리 3호기 &quot;승인&quot;  ") == '원안위, 고리 3호기 "승인"'


def test_different_articles_not_merged():
    items = [
        raw("원전", "원안위, 고리 3호기 계속운전 승인", "https://a.com/1"),
        raw("원전", "한빛 1호기 냉각재 누설로 수동 정지", "https://b.com/2"),
    ]
    assert len(merge_exact_duplicates(items)) == 2


BODY_GORI = (
    "원자력안전위원회는 18일 제200회 회의를 열고 한국수력원자력이 신청한 고리 3호기 계속운전 허가안을 의결했다. "
    "고리 3호기는 설계수명 40년이 만료돼 2024년 9월부터 운전이 정지된 상태였다. 원안위는 한국원자력안전기술원의 "
    "심사 결과를 보고받고 주기적안전성평가와 방사선환경영향평가 결과를 검토했다. 이에 따라 고리 3호기는 "
    "2034년까지 10년간 계속운전이 가능해졌다."
)
BODY_GORI_2 = (
    "고리 3호기가 10년 더 운전한다. 원자력안전위원회는 18일 회의에서 고리 3호기 계속운전 허가안을 심의해 의결했다고 밝혔다. "
    "원안위는 한국원자력안전기술원의 심사 결과와 전문위원회 검토 결과를 보고받았다. 고리 3호기는 2024년 9월 설계수명 "
    "만료로 정지됐으며 이번 허가로 2034년까지 운전할 수 있게 됐다."
)
BODY_HANBIT = (
    "한빛원전 1호기에서 냉각재 누설이 발생해 원자로가 수동 정지됐다. 한국수력원자력은 정비 중 증기발생기 배관에서 "
    "누설을 확인하고 원자로를 정지했다고 밝혔다. 외부로의 방사성물질 누출은 없는 것으로 확인됐다."
)
BODY_SMR = (
    "정부가 소형모듈원자로 i-SMR 개발 사업에 내년 예산 500억원을 편성했다. 과학기술정보통신부와 산업통상자원부는 "
    "표준설계인가 획득을 목표로 연구개발을 지속한다고 밝혔다."
)
BODY_RN = "라돈 검출 침대 매트리스 수거 작업이 진행되고 있다. 원안위는 생활방사선법에 따라 수거 명령을 내렸다."


def _corpus():
    a = make_article("원안위, 고리 3호기 계속운전 승인", BODY_GORI, ["원안위"], score=80)
    b = make_article("고리3호기 수명 연장…원안위 계속운전 승인", BODY_GORI_2, ["원전"], score=70)
    c = make_article("원안위, 고리 4호기 계속운전 승인", BODY_GORI.replace("3호기", "4호기"), ["원안위"], score=75)
    d = make_article("한빛 1호기 냉각재 누설로 수동 정지", BODY_HANBIT, ["원전"], score=60)
    e = make_article("i-SMR 내년 예산 500억원 편성", BODY_SMR, ["SMR"], score=50)
    f = make_article("라돈 침대 수거 명령", BODY_RN, ["라돈"], score=40)
    return a, b, c, d, e, f


def test_near_identical_titles_grouped():
    a, b, c, d, e, f = _corpus()
    groups = group_same_events([a, b, c, d, e, f])
    group_of = {x.article_id: x.duplicate_group for x in (a, b, c, d, e, f)}
    assert group_of[a.article_id] == group_of[b.article_id]
    assert a.is_representative and not b.is_representative  # 중요도 높은 기사가 대표
    assert b.article_id in a.similar_article_ids
    assert len(groups) == 5


def test_different_unit_numbers_not_grouped():
    a, b, c, *_ = _corpus()
    assert not numbers_compatible(a.title, c.title)
    group_same_events([a, b, c])
    assert a.duplicate_group != c.duplicate_group


def test_distinct_articles_not_grouped():
    *_, d, e, f = _corpus()
    groups = group_same_events([d, e, f])
    assert len(groups) == 3
    assert all(x.is_representative for x in (d, e, f))


def test_string_similarity_alone_does_not_merge_when_body_differs():
    # 제목이 상당히 비슷하지만 본문이 완전히 다른 기사 → 묶지 않는다.
    a = make_article("원전 안전점검 결과 발표", BODY_HANBIT, ["원전"], score=50)
    b = make_article("원전 안전점검 일정 발표", BODY_SMR, ["원전"], score=40)
    others = _corpus()
    groups = group_same_events([a, b, *others])
    assert a.duplicate_group != b.duplicate_group
    assert len(groups) == 2 + 5


def test_ambiguous_pair_uses_decider():
    # 제목 유사도 중간 + 본문 없음 → 애매한 쌍 → 판정기(LLM 등)에 위임
    a = make_article("원전 안전점검 결과 발표", None, ["원전"], score=50)
    b = make_article("원전 점검 결과 나왔다", None, ["원전"], score=40)
    calls = []

    def decider(pairs):
        calls.append(len(pairs))
        return {0: True}

    group_same_events([a, b], pair_decider=decider)
    assert calls and a.duplicate_group == b.duplicate_group


def test_title_similarity_range():
    assert title_similarity("원안위, 고리 3호기 계속운전 승인", "고리3호기 수명 연장…원안위 계속운전 승인") > 0.7
    assert title_similarity("원안위, 고리 3호기 계속운전 승인", "라돈 침대 수거 명령") < 0.15
