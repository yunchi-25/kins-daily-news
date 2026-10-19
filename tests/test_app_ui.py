"""Streamlit 화면 동작 테스트 (mock 검색 결과 사용, 실제 API 호출 없음)."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src import pipeline
from src.config import NEWS_KEYWORDS, TOPICS
from tests.test_pipeline import END, START, FakeNaver, fake_enrich, _settings

APP = str(Path(__file__).resolve().parent.parent / "app.py")
ORIGINAL_RUN_SEARCH = pipeline.run_search
ENV_KEYS = ["APP_PASSWORD", "NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET", "OPENAI_API_KEY", "OPENAI_MODEL"]


@pytest.fixture()
def clean_env(monkeypatch):
    for k in ENV_KEYS:
        monkeypatch.setenv(k, "")
    return monkeypatch


def _all_markdown(at: AppTest) -> str:
    return "\n".join(m.value for m in at.markdown)


def test_missing_credentials_shows_message(clean_env):
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert not at.exception
    errors = " ".join(e.value for e in at.error)
    assert "네이버 뉴스 API 인증정보가 설정되어 있지 않습니다." in errors
    assert "NAVER_CLIENT_ID" in errors and "NAVER_CLIENT_SECRET" in errors
    assert "KINS 일일 언론동향 검색" in _all_markdown(at)
    assert "검색 종료</b>: 현재 시각 (Asia/Seoul)" in _all_markdown(at)
    # 검색 버튼을 눌러도 앱이 죽지 않고 안내만 표시
    at.button[0].click().run()
    assert not at.exception
    assert "run" not in at.session_state


def test_search_flow_with_mock(clean_env):
    clean_env.setenv("NAVER_CLIENT_ID", "dummy")
    clean_env.setenv("NAVER_CLIENT_SECRET", "dummy")
    captured = {"calls": 0}

    def fake_run_search(start, end, settings, **kwargs):
        captured["calls"] += 1
        captured["start"], captured["end"] = start, end
        return ORIGINAL_RUN_SEARCH(
            START, END, _settings(), naver_client=FakeNaver(), enrich_fn=fake_enrich, progress=kwargs.get("progress")
        )

    clean_env.setattr(pipeline, "run_search", fake_run_search)

    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert not at.error
    submit = next(b for b in at.button if b.label == "뉴스 검색")
    submit.click().run()
    assert not at.exception, at.exception

    # 검색 종료 시각 = 클릭 시점 (KST, timezone-aware)
    assert captured["end"].tzinfo is not None and str(captured["end"].tzinfo) == "Asia/Seoul"
    assert captured["start"] < captured["end"]

    md = _all_markdown(at)
    # 요약
    assert "수집 기사" in md and "중복 제거 후" in md and "최종 후보" in md
    # 주제별 보기: 6개 주제 모두 + 0건 표시
    for topic in TOPICS:
        assert topic.replace("&", "&amp;") in md
    assert "확인된 기사 없음 (0건)" in md
    assert "기사 원문 보기" in md
    # 탭
    labels = [t.label for t in at.tabs]
    assert labels == ["주제별 보기", "키워드별 보기", "전체 기사", "제외 기사(검토용)"]
    # 키워드별 보기: 28개 키워드 모두 (0건 포함)
    exp_labels = [e.label for e in at.expander]
    for i, kw in enumerate(NEWS_KEYWORDS, start=1):
        assert any(lbl.startswith(f"{i}. {kw} (") for lbl in exp_labels), kw
    assert any(lbl == "24. 양이원영 (0건)" for lbl in exp_labels)
    # 전체 기사 / 제외 기사 표
    assert len(at.dataframe) >= 2

    # 필터 변경 시 API 재호출 없이 결과만 필터링
    assert captured["calls"] == 1
    at.multiselect[0].select(TOPICS[0]).run()
    at.text_input[0].input("고리").run()
    assert not at.exception
    assert captured["calls"] == 1
    assert at.session_state["run"] is not None


def test_default_start_time_is_1600_and_editable(clean_env):
    from datetime import time as dtime

    clean_env.setenv("NAVER_CLIENT_ID", "dummy")
    clean_env.setenv("NAVER_CLIENT_SECRET", "dummy")
    captured = {}

    def fake_run_search(start, end, settings, **kwargs):
        captured["start"] = start
        return ORIGINAL_RUN_SEARCH(START, END, _settings(), naver_client=FakeNaver(), enrich_fn=fake_enrich)

    clean_env.setattr(pipeline, "run_search", fake_run_search)
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert at.time_input[0].value == dtime(16, 0)  # 기본값 16:00
    # 사용자가 시간을 바꾸고 검색하면 바꾼 시간이 검색 시작 시각이 된다.
    at.time_input[0].set_value(dtime(11, 30))
    next(b for b in at.button if b.label == "뉴스 검색").click().run()
    assert not at.exception
    assert captured["start"].time() == dtime(11, 30)
    assert str(captured["start"].tzinfo) == "Asia/Seoul"


def test_password_gate(clean_env):
    clean_env.setenv("APP_PASSWORD", "kins-test")
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert not any(b.label == "뉴스 검색" for b in at.button)  # 비밀번호 전에는 검색 화면 없음
    at.text_input[0].input("wrong")
    next(b for b in at.button if b.label == "확인").click().run()
    assert any("비밀번호가 올바르지 않습니다" in e.value for e in at.error)
    at.text_input[0].input("kins-test")
    next(b for b in at.button if b.label == "확인").click().run()
    assert not at.exception
    assert any(b.label == "뉴스 검색" for b in at.button)
