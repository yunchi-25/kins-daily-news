"""애플리케이션 설정.

- 검색 키워드, 주제, 인물 판별 규칙 등 업무 기준값을 한 곳에서 관리한다.
- API 키 등 민감정보는 코드에 두지 않고 .env 또는 환경변수에서만 읽는다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

try:  # python-dotenv 미설치 환경에서도 import 오류가 나지 않도록 한다.
    from dotenv import dotenv_values
except ImportError:  # pragma: no cover
    dotenv_values = None

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
ENV_FILE = BASE_DIR / ".env"

# ---------------------------------------------------------------------------
# 시간대: 모든 시각은 대한민국 표준시로 처리한다.
# ---------------------------------------------------------------------------
TIMEZONE_NAME = "Asia/Seoul"
KST = ZoneInfo(TIMEZONE_NAME)

# ---------------------------------------------------------------------------
# 고정 검색 키워드 28개 (순서 고정, 임의 추가 금지)
# ---------------------------------------------------------------------------
NEWS_KEYWORDS = [
    "한국원자력안전기술원",
    "원자력안전기술원",
    "KINS",
    "임승철",
    "원자력안전위원회",
    "원안위",
    "최원호",
    "원자력",
    "원자력발전소",
    "원전",
    "SMR",
    "방사능",
    "방사선",
    "핵폐기물",
    "사용후핵연료",
    "방폐물",
    "삼중수소",
    "라돈",
    "공기업",
    "공공기관",
    "공공기관 통합/통폐합",
    "출연연",
    "정출연",
    "양이원영",
    "이정윤",
    "한병섭",
    "이헌석",
    "석광훈",
]

# ---------------------------------------------------------------------------
# 6개 주제 (순서 고정)
# ---------------------------------------------------------------------------
TOPIC_KINS = "한국원자력안전기술원(KINS)"
TOPIC_NSSC = "원자력안전위원회"
TOPIC_DOMESTIC = "국내 원자력&방사선"
TOPIC_FOREIGN = "국외 원자력&방사선"
TOPIC_GOV = "정부&정치권"
TOPIC_ETC = "기타"

TOPICS = [TOPIC_KINS, TOPIC_NSSC, TOPIC_DOMESTIC, TOPIC_FOREIGN, TOPIC_GOV, TOPIC_ETC]


# ---------------------------------------------------------------------------
# 동명이인 판별 규칙
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PersonRule:
    name: str
    description: str
    # 기사 어디에든 등장하면 해당 인물로 인정하는 소속기관/단체명
    org_terms: tuple[str, ...] = ()
    # 이름 주변(window) 에 함께 나와야 하는 직함 (비어 있으면 생략)
    role_terms: tuple[str, ...] = ()
    # 이름 주변(window) 에 함께 나와야 하는 분야 단어
    domain_terms: tuple[str, ...] = ()
    window: int = 120


PERSON_RULES: dict[str, PersonRule] = {
    "임승철": PersonRule(
        name="임승철",
        description="한국원자력안전기술원(KINS) 원장",
        org_terms=("한국원자력안전기술원", "원자력안전기술원", "KINS"),
        role_terms=("원장",),
        domain_terms=("원자력", "원전", "원자력안전"),
    ),
    "최원호": PersonRule(
        name="최원호",
        description="원자력안전위원회 위원장",
        org_terms=("원자력안전위원회", "원안위"),
        role_terms=("위원장",),
        domain_terms=("원자력", "원전"),
    ),
    "양이원영": PersonRule(
        name="양이원영",
        description="환경·원자력·에너지 관련 활동",
        org_terms=(),
        role_terms=(),
        domain_terms=(
            "원자력", "원전", "에너지", "환경", "기후", "탈핵", "탈원전",
            "재생에너지", "핵", "방사능", "오염수", "전력",
        ),
        window=200,
    ),
    "이정윤": PersonRule(
        name="이정윤",
        description="'원자력 안전과 미래' 대표",
        org_terms=("원자력 안전과 미래", "원자력안전과미래", "원자력안전과 미래"),
        role_terms=("대표",),
        domain_terms=("원자력", "원전", "핵"),
    ),
    "한병섭": PersonRule(
        name="한병섭",
        description="원자력안전연구소 소장",
        org_terms=("원자력안전연구소",),
        role_terms=("소장",),
        domain_terms=("원자력", "원전", "방사능", "핵"),
    ),
    "이헌석": PersonRule(
        name="이헌석",
        description="에너지정의행동 정책위원",
        org_terms=("에너지정의행동",),
        role_terms=("정책위원", "활동가", "대표"),
        domain_terms=("에너지", "원자력", "원전", "핵", "기후", "탈핵"),
    ),
    "석광훈": PersonRule(
        name="석광훈",
        description="에너지전환포럼 전문위원",
        org_terms=("에너지전환포럼",),
        role_terms=("전문위원", "위원", "연구원", "박사"),
        domain_terms=("에너지", "원자력", "원전", "전력", "기후", "재생에너지"),
    ),
}

# ---------------------------------------------------------------------------
# 언론사 정보
# ---------------------------------------------------------------------------
# 도메인 -> 언론사명 (원문 페이지에서 언론사명을 찾지 못했을 때만 사용)
KNOWN_PUBLISHER_DOMAINS: dict[str, str] = {
    "yna.co.kr": "연합뉴스",
    "yonhapnewstv.co.kr": "연합뉴스TV",
    "newsis.com": "뉴시스",
    "news1.kr": "뉴스1",
    "chosun.com": "조선일보",
    "biz.chosun.com": "조선비즈",
    "joongang.co.kr": "중앙일보",
    "donga.com": "동아일보",
    "hani.co.kr": "한겨레",
    "khan.co.kr": "경향신문",
    "hankookilbo.com": "한국일보",
    "kmib.co.kr": "국민일보",
    "seoul.co.kr": "서울신문",
    "segye.com": "세계일보",
    "munhwa.com": "문화일보",
    "mk.co.kr": "매일경제",
    "hankyung.com": "한국경제",
    "sedaily.com": "서울경제",
    "mt.co.kr": "머니투데이",
    "edaily.co.kr": "이데일리",
    "fnnews.com": "파이낸셜뉴스",
    "asiae.co.kr": "아시아경제",
    "heraldcorp.com": "헤럴드경제",
    "etnews.com": "전자신문",
    "dt.co.kr": "디지털타임스",
    "zdnet.co.kr": "지디넷코리아",
    "kbs.co.kr": "KBS",
    "imbc.com": "MBC",
    "sbs.co.kr": "SBS",
    "jtbc.co.kr": "JTBC",
    "ytn.co.kr": "YTN",
    "mbn.co.kr": "MBN",
    "ichannela.com": "채널A",
    "tvchosun.com": "TV조선",
    "ohmynews.com": "오마이뉴스",
    "pressian.com": "프레시안",
    "nocutnews.co.kr": "노컷뉴스",
    "mediatoday.co.kr": "미디어오늘",
    "ajunews.com": "아주경제",
    "newspim.com": "뉴스핌",
    "inews24.com": "아이뉴스24",
    "dailian.co.kr": "데일리안",
    "electimes.com": "전기신문",
    "e2news.com": "이투뉴스",
    "ekn.kr": "에너지경제",
    "hellodd.com": "헬로디디",
    "dongascience.com": "동아사이언스",
    "busan.com": "부산일보",
    "kookje.co.kr": "국제신문",
    "imaeil.com": "매일신문",
    "yeongnam.com": "영남일보",
    "kwnews.co.kr": "강원일보",
    "ksilbo.co.kr": "경상일보",
    "kyongbuk.co.kr": "경북일보",
    "daejonilbo.com": "대전일보",
    "cctoday.co.kr": "충청투데이",
    "newdaily.co.kr": "뉴데일리",
    "businesspost.co.kr": "비즈니스포스트",
    "sisajournal.com": "시사저널",
}

# 신뢰도 가중치용 주요 언론사 (정렬 보조 지표로만 사용)
MAJOR_PUBLISHERS = {
    "연합뉴스", "연합뉴스TV", "뉴시스", "뉴스1", "KBS", "MBC", "SBS", "YTN", "JTBC", "MBN",
    "조선일보", "중앙일보", "동아일보", "한겨레", "경향신문", "한국일보", "국민일보",
    "서울신문", "세계일보", "문화일보", "매일경제", "한국경제", "서울경제", "머니투데이",
    "이데일리", "파이낸셜뉴스", "아시아경제", "헤럴드경제", "전자신문", "전기신문",
    "에너지경제", "이투뉴스", "채널A", "TV조선", "조선비즈", "동아사이언스",
}


_file_env: dict[str, str] = {}


def _reload_env_file() -> None:
    """.env 를 매번 다시 읽는다 (앱 재시작 없이 키 변경 반영). 시스템 환경변수가 우선한다."""
    global _file_env
    _file_env = {}
    if dotenv_values is not None and ENV_FILE.is_file():
        _file_env = {k: (v or "") for k, v in dotenv_values(ENV_FILE).items()}


def _getenv(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is not None and value.strip() != "":
        return value
    return _file_env.get(name, default) or default


def _env_bool(name: str, default: bool) -> bool:
    value = _getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        value = int(_getenv(name).strip() or default)
    except ValueError:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _env_float(name: str, default: float) -> float:
    try:
        return float(_getenv(name).strip() or default)
    except ValueError:
        return default


@dataclass
class Settings:
    """환경변수 기반 실행 설정."""

    app_password: str = ""  # 설정 시 접속 비밀번호 필요 (외부 공개용)
    naver_client_id: str = ""
    naver_client_secret: str = ""
    openai_api_key: str = ""
    openai_model: str = ""
    openai_base_url: str = "https://api.openai.com/v1"

    http_timeout: float = 10.0
    http_retries: int = 3
    # 네이버 검색 API: display 최대 100, start 최대 1000
    naver_display: int = 100
    naver_max_start: int = 1000
    max_results_per_keyword: int = 1000
    naver_request_interval: float = 0.12

    fetch_article_text: bool = True
    article_fetch_workers: int = 10
    max_article_text_chars: int = 6000

    llm_batch_size: int = 8
    llm_workers: int = 4
    llm_timeout: float = 60.0
    llm_max_pair_checks: int = 80

    extra: dict = field(default_factory=dict)

    @property
    def naver_configured(self) -> bool:
        return bool(self.naver_client_id and self.naver_client_secret)

    @property
    def llm_configured(self) -> bool:
        return bool(self.openai_api_key and self.openai_model)


def load_settings() -> Settings:
    """현재 환경변수(+ .env)로부터 Settings 를 만든다 (호출 시점 기준)."""
    _reload_env_file()
    return Settings(
        app_password=_getenv("APP_PASSWORD").strip(),
        naver_client_id=_getenv("NAVER_CLIENT_ID").strip(),
        naver_client_secret=_getenv("NAVER_CLIENT_SECRET").strip(),
        openai_api_key=_getenv("OPENAI_API_KEY").strip(),
        openai_model=_getenv("OPENAI_MODEL").strip(),
        openai_base_url=(_getenv("OPENAI_BASE_URL").strip() or "https://api.openai.com/v1").rstrip("/"),
        http_timeout=_env_float("HTTP_TIMEOUT", 10.0),
        http_retries=_env_int("HTTP_RETRIES", 3, 1, 6),
        max_results_per_keyword=_env_int("NAVER_MAX_RESULTS_PER_KEYWORD", 1000, 100, 1000),
        fetch_article_text=_env_bool("FETCH_ARTICLE_TEXT", True),
        article_fetch_workers=_env_int("ARTICLE_FETCH_WORKERS", 10, 1, 32),
        llm_batch_size=_env_int("LLM_BATCH_SIZE", 8, 1, 20),
        llm_workers=_env_int("LLM_WORKERS", 4, 1, 16),
        llm_timeout=_env_float("LLM_TIMEOUT", 60.0),
        llm_max_pair_checks=_env_int("LLM_MAX_PAIR_CHECKS", 80, 0, 500),
    )
