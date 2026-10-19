import pytest

from src.classifier import classify, classify_rule, normalize_topic
from src.config import PERSON_RULES, TOPIC_DOMESTIC, TOPIC_ETC, TOPIC_FOREIGN, TOPIC_GOV, TOPIC_KINS, TOPIC_NSSC
from src.filtering import R_NOT_RELEVANT, assess, check_person, hard_exclusion, prescreen
from src.ranking import rule_score
from tests.helpers import make_article

# (제목, 본문, 키워드, 기대 주제)
FIXTURES = [
    (
        "한국원자력안전기술원, 원전 계속운전 안전성 평가 기술 고도화",
        "한국원자력안전기술원(KINS)은 원전 계속운전 심사에 활용할 안전성 평가 기술을 고도화한다고 밝혔다. KINS는 ...",
        ["한국원자력안전기술원"],
        TOPIC_KINS,
    ),
    (
        "임승철 KINS 원장, 새울 3호기 현장 안전점검",
        "임승철 한국원자력안전기술원 원장은 새울 3호기를 방문해 안전점검을 실시했다.",
        ["임승철", "KINS"],
        TOPIC_KINS,
    ),
    (
        "원안위, 고리 3호기 계속운전 승인",
        "원자력안전위원회는 회의를 열고 고리 3호기 계속운전 허가안을 의결했다. 원안위는 KINS 심사 결과를 보고받았다.",
        ["원안위", "원전", "KINS"],
        TOPIC_NSSC,
    ),
    (
        "최원호 원안위원장 \"SMR 규제체계 조기 마련\"",
        "최원호 원자력안전위원회 위원장은 소형모듈원자로 규제체계를 조기에 마련하겠다고 밝혔다.",
        ["최원호", "SMR"],
        TOPIC_NSSC,
    ),
    (
        "한빛 1호기 냉각재 누설로 수동 정지",
        "한빛원전 1호기에서 냉각재 누설이 발생해 원자로가 수동 정지됐다. 한국수력원자력은 원인을 조사 중이다.",
        ["원전"],
        TOPIC_DOMESTIC,
    ),
    (
        "경주 방폐장 처분시설 방사선 감시 강화",
        "한국원자력환경공단은 경주 중저준위 방폐물 처분시설의 방사선 감시를 강화한다고 밝혔다.",
        ["방폐물", "방사선"],
        TOPIC_DOMESTIC,
    ),
    (
        "日 후쿠시마 원전 오염수 추가 방류 시작",
        "도쿄전력은 후쿠시마 제1원전 오염수 추가 방류를 시작했다. 삼중수소 농도는 기준치 이하라고 밝혔다.",
        ["원전", "삼중수소"],
        TOPIC_FOREIGN,
    ),
    (
        "미국 NRC, 소형모듈원자로 설계인증 승인",
        "미국 원자력규제위원회(NRC)는 SMR 설계인증을 승인했다.",
        ["SMR"],
        TOPIC_FOREIGN,
    ),
    (
        "국회 산자위, 고준위 방폐물 특별법 처리",
        "국회 산업통상자원중소벤처기업위원회는 고준위 방사성폐기물 관리 특별법을 의결했다.",
        ["방폐물", "핵폐기물"],
        TOPIC_GOV,
    ),
    (
        "정부, 신규 원전 2기 건설 확정",
        "정부는 전력수급기본계획에 따라 신규 원전 2기 건설을 확정했다.",
        ["원전"],
        TOPIC_GOV,
    ),
    (
        "기재부, 공공기관 통폐합 추진…연내 기능조정안 발표",
        "기획재정부는 공공기관 통폐합과 기능조정을 추진한다. 공공기관운영위원회 심의를 거쳐 확정한다.",
        ["공공기관", "공공기관 통합/통폐합"],
        TOPIC_ETC,
    ),
    (
        "출연연 기관장 공백 장기화…과학계 우려",
        "정부출연연구기관 기관장 공모가 지연되며 출연연 기관장 공백이 길어지고 있다.",
        ["출연연"],
        TOPIC_ETC,
    ),
]


@pytest.mark.parametrize("title,text,keywords,expected", FIXTURES, ids=[f[0][:18] for f in FIXTURES])
def test_topic_classification(title, text, keywords, expected):
    a = make_article(title, text * 3, keywords)
    ok, reason = assess(a)
    assert ok, reason
    assert classify_rule(a) == expected


def test_kins_before_nssc_when_kins_first_in_title():
    a = make_article("KINS, 원안위에 고리 2호기 심사 결과 보고", "KINS는 원안위에 심사 결과를 보고했다.", ["KINS", "원안위"])
    assert classify_rule(a) == TOPIC_KINS
    b = make_article("원안위, KINS 심사 결과 토대로 고리 2호기 승인", "원안위는 KINS 심사 결과를 토대로 승인했다.", ["KINS", "원안위"])
    assert classify_rule(b) == TOPIC_NSSC


def test_llm_topic_takes_priority_and_is_validated():
    a = make_article("한빛 1호기 정지", "한빛원전 1호기가 정지됐다.", ["원전"])
    a.llm_topic = "국외 원자력 및 방사선"
    assert classify(a) == TOPIC_FOREIGN
    a.llm_topic = "없는 주제"
    assert classify(a) == TOPIC_DOMESTIC
    assert normalize_topic("원자력안전위원회") == TOPIC_NSSC


# ---------------------------------------------------------------------------
# 제외 기준
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "title",
    [
        "[특징주] 원전株 강세…두산에너빌리티 5% 상승",
        "원전 관련주 급등, 목표주가 상향",
        "SK하이닉스 HBM 증설…AI 데이터센터 전력 수요 급증",
        "OO전자, 방사선 차단 신제품 출시 기념 할인",
        "OO기업-OO대학 업무협약 체결",
        "OO공사, 고객만족도 대상 수상",
        "배우 김OO, 드라마 원작 영화 개봉",
        "공공기관 사칭 보이스피싱 주의",
        "방사선 치료 효과 높인 신기술…암 환자 생존율 향상",
    ],
)
def test_hard_exclusions(title):
    assert hard_exclusion(title) is not None


def test_ai_article_with_nuclear_core_is_kept():
    title = "AI 데이터센터 전력난…SMR 건설 본격화"
    text = ("데이터센터 전력 수요 대응을 위해 SMR 건설이 추진된다. 원자력 업계는 소형모듈원자로 인허가와 원전 안전 규제를 강조했다. " * 3)
    assert hard_exclusion(title) is None
    ok, _ = assess(make_article(title, text, ["SMR"]))
    assert ok


def test_incidental_mention_excluded():
    text = "프로야구 경기 결과 기사이다. " * 20 + "경기장은 원전 인근 지역에 있다."
    a = make_article("지역 축제 성황리에 마쳐", text, ["원전"])
    ok, reason = assess(a)
    assert not ok and reason == R_NOT_RELEVANT


def test_prescreen_keeps_domain_articles():
    a = make_article("원안위 회의 결과", None, ["원안위"], description="원자력안전위원회가 회의를 열었다")
    assert prescreen(a) is None
    b = make_article("주말 날씨 맑음", None, ["원전"], description="전국이 맑겠다")
    assert prescreen(b) == R_NOT_RELEVANT


# ---------------------------------------------------------------------------
# 동명이인
# ---------------------------------------------------------------------------
def test_person_match_kins_president():
    ok, _ = check_person(PERSON_RULES["임승철"], "임승철 원장 취임", "임승철 한국원자력안전기술원 원장이 취임했다.")
    assert ok is True


def test_person_homonym_excluded():
    a = make_article("최원호 감독, 시즌 마무리 소감", "프로야구 최원호 감독이 시즌을 마무리했다. " * 5, ["최원호"])
    ok, reason = assess(a)
    assert not ok
    assert "최원호" in a.rejected_keywords


def test_person_homonym_removed_but_other_keywords_kept():
    text = "이정윤 OO구청장은 지역 행사에 참석했다. 한편 한빛 원전 1호기가 정지돼 원전 안전 문제가 제기됐다. 원전 원전 원전"
    a = make_article("한빛 원전 1호기 정지", text, ["원전", "이정윤"])
    ok, _ = assess(a)
    assert ok
    assert a.matched_keywords == ["원전"]
    assert "이정윤" in a.rejected_keywords


def test_person_matched_by_organization():
    a = make_article(
        "\"노후 원전 수명연장, 안전성 검증 부족\"",
        "한병섭 원자력안전연구소장은 노후 원전 계속운전의 안전성 검증이 부족하다고 지적했다.",
        ["한병섭"],
    )
    ok, _ = assess(a)
    assert ok and a.matched_keywords == ["한병섭"]


def test_person_unverifiable_without_text_excluded():
    a = make_article("석광훈 출판기념회", None, ["석광훈"], description="저자 사인회가 열렸다")
    ok, reason = assess(a)
    assert not ok


# ---------------------------------------------------------------------------
# 중요도
# ---------------------------------------------------------------------------
def test_kins_article_ranks_higher_than_general():
    kins = make_article("KINS, 원전 안전점검 결과 발표", "한국원자력안전기술원은 점검 결과를 발표했다. " * 10, ["KINS"])
    general = make_article("원전 지역 주민 간담회 열려", "원전 지역 주민 간담회가 열렸다. " * 10, ["원전"])
    assert rule_score(kins) > rule_score(general)
