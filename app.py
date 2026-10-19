"""KINS 일일 언론동향 검색 - Streamlit 앱.

실행: streamlit run app.py
"""

from __future__ import annotations

import hmac
import logging
import os
import traceback
from datetime import time as dtime, timedelta

import pandas as pd
import streamlit as st

st.set_page_config(page_title="KINS 일일 언론동향 검색", page_icon="📰", layout="wide")

from src import pipeline  # noqa: E402  (set_page_config 가 먼저 호출되어야 함)
from src.config import NEWS_KEYWORDS, TOPICS, load_settings  # noqa: E402
from src.exporter import ExportError, all_articles_df, file_stamp, keyword_df, to_csv_bytes, to_excel_bytes, to_text_report  # noqa: E402
from src.models import SearchRun  # noqa: E402
from src.ui_components import CSS, article_card, empty_message, keyword_chips, stage_line, summary_box, topic_header  # noqa: E402
from src.utils import combine_kst, format_kst, now_kst, previous_business_day  # noqa: E402
from src.views import (  # noqa: E402
    SORT_IMPORTANCE,
    SORT_LATEST,
    Filters,
    all_articles_view,
    keyword_view,
    publisher_options,
    similar_members,
    topic_view,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

STAGES = [pipeline.STAGE_SEARCH, pipeline.STAGE_DETAIL, pipeline.STAGE_FILTER, pipeline.STAGE_DEDUP, pipeline.STAGE_CLASSIFY]
DEFAULT_START_TIME = dtime(16, 0)  # 검색 시작 시간 기본값 (화면에서 변경 가능)
MISSING_NAVER_MSG = (
    "네이버 뉴스 API 인증정보가 설정되어 있지 않습니다.\n\n"
    ".env 파일(Streamlit Cloud는 앱 설정의 Secrets)에서 NAVER_CLIENT_ID와 NAVER_CLIENT_SECRET을 설정해주세요."
)


# ---------------------------------------------------------------------------
# 검색 조건
# ---------------------------------------------------------------------------
def render_header() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown('<div class="kins-title">KINS 일일 언론동향 검색</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="kins-desc">검색 시작 일시를 입력하면 해당 시각부터 현재 시각까지 '
        "지정된 28개 키워드의 뉴스 기사를 검색합니다.</div>",
        unsafe_allow_html=True,
    )


def render_search_form(settings) -> None:
    now = now_kst()
    with st.container(border=True):
        st.markdown('<div class="kins-section">검색 조건</div>', unsafe_allow_html=True)
        if not settings.naver_configured:
            st.error(MISSING_NAVER_MSG, icon="⚠️")

        with st.form("search_form", border=False):
            c1, c2, c3, c4 = st.columns([1.1, 1.0, 1.7, 0.9], vertical_alignment="bottom")
            with c1:
                start_date = st.date_input(
                    "검색 시작 날짜",
                    value=previous_business_day(now.date()),
                    max_value=now.date(),
                    format="YYYY-MM-DD",
                )
            with c2:
                start_time = st.time_input("검색 시작 시간", value=DEFAULT_START_TIME, step=timedelta(minutes=1))
            with c3:
                st.markdown(
                    '<div class="kins-end"><b>검색 종료</b>: 현재 시각 (Asia/Seoul)<br>'
                    f"<span style='color:#4D5B6B'>버튼을 누른 시각까지 검색 · 지금 {format_kst(now)}</span></div>",
                    unsafe_allow_html=True,
                )
            with c4:
                submitted = st.form_submit_button("뉴스 검색", type="primary", width="stretch")

            with st.expander("고급 옵션", expanded=False):
                o1, o2, o3 = st.columns(3)
                with o1:
                    fetch_pages = st.checkbox(
                        "기사 원문 확인 (최초 게시 시각·본문 검증)",
                        value=settings.fetch_article_text,
                        help="끄면 네이버 검색 API의 pubDate와 요약만으로 판단합니다. 속도는 빨라지지만 정확도가 낮아집니다.",
                    )
                with o2:
                    use_llm = st.checkbox(
                        "LLM 보조 판단 사용",
                        value=settings.llm_configured,
                        disabled=not settings.llm_configured,
                        help="OPENAI_API_KEY와 OPENAI_MODEL이 설정된 경우에만 사용할 수 있습니다.",
                    )
                    if settings.llm_configured:
                        st.caption(f"LLM 모델: {settings.openai_model}")
                    else:
                        st.caption("LLM 미설정 – 규칙 기반으로 판단합니다.")
                with o3:
                    max_results = st.select_slider(
                        "키워드당 최대 조회 건수",
                        options=[100, 200, 300, 500, 1000],
                        value=settings.max_results_per_keyword if settings.max_results_per_keyword in (100, 200, 300, 500, 1000) else 1000,
                        help="네이버 검색 API는 키워드당 최대 1,000건까지 조회할 수 있습니다.",
                    )

    if submitted:
        execute_search(settings, start_date, start_time, fetch_pages, use_llm, max_results)


def execute_search(settings, start_date, start_time, fetch_pages: bool, use_llm: bool, max_results: int) -> None:
    clicked_at = now_kst()  # 검색 종료 시각 = 버튼을 누른 현재 시각
    start_dt = combine_kst(start_date, start_time)

    if not settings.naver_configured:
        st.error(MISSING_NAVER_MSG, icon="⚠️")
        return
    if start_dt > clicked_at:
        st.error(f"검색 시작 시각({format_kst(start_dt)})이 현재 시각({format_kst(clicked_at)})보다 늦습니다. 시작 일시를 다시 선택해주세요.")
        return
    if clicked_at - start_dt > timedelta(days=31):
        st.error("검색 기간은 최대 31일까지 지정할 수 있습니다. 시작 날짜를 다시 선택해주세요.")
        return
    if clicked_at - start_dt > timedelta(days=7):
        st.warning("검색 기간이 7일을 넘습니다. 키워드별 API 조회 한도(1,000건)로 일부 기사가 누락될 수 있고 시간이 오래 걸릴 수 있습니다.")

    settings.max_results_per_keyword = int(max_results)
    done_stages: set[str] = set()
    with st.status("뉴스 검색 중...", expanded=True) as status:
        st.write(f"검색 범위: {format_kst(start_dt)} ~ {format_kst(clicked_at, '%Y-%m-%d %H:%M:%S')} (KST)")
        stage_box = st.empty()
        bar = st.progress(0.0, text="검색 준비 중...")
        state = {"stage": None}

        def on_progress(stage: str, frac: float, message: str) -> None:
            if state["stage"] and state["stage"] != stage:
                done_stages.add(state["stage"])
            state["stage"] = stage
            stage_box.markdown(stage_line(STAGES, stage, done_stages), unsafe_allow_html=True)
            bar.progress(min(1.0, max(0.0, frac)), text=message)

        try:
            run = pipeline.run_search(
                start_dt,
                clicked_at,
                settings,
                use_llm=use_llm,
                fetch_pages=fetch_pages,
                progress=on_progress,
            )
        except pipeline.SearchAborted as exc:
            status.update(label="검색을 진행할 수 없습니다", state="error", expanded=True)
            st.error(str(exc))
            return
        except Exception as exc:  # 앱이 죽지 않도록 사용자 메시지로 변환
            status.update(label="검색 중 오류가 발생했습니다", state="error", expanded=True)
            st.error(f"검색 중 예상하지 못한 오류가 발생했습니다: {type(exc).__name__}: {exc}")
            with st.expander("오류 상세"):
                st.code(traceback.format_exc())
            return
        stage_box.markdown(stage_line(STAGES, None, set(STAGES)), unsafe_allow_html=True)
        status.update(
            label=f"검색 완료 · 최종 후보 {run.stats.get('final', 0)}건 ({format_kst(run.end, '%H:%M:%S')} 기준)",
            state="complete",
            expanded=False,
        )
    st.session_state["run"] = run
    st.session_state["run_id"] = st.session_state.get("run_id", 0) + 1


# ---------------------------------------------------------------------------
# 결과
# ---------------------------------------------------------------------------
def render_results(run: SearchRun) -> None:
    rid = st.session_state.get("run_id", 0)
    st.markdown('<div class="kins-section" style="margin-top:.8rem">검색 결과</div>', unsafe_allow_html=True)
    st.markdown(summary_box(run), unsafe_allow_html=True)
    s = run.stats
    st.caption(
        f"API 검색 결과 {s.get('api_results', 0):,}건 중 기간 내 {s.get('collected', 0):,}건 · "
        f"원문 확인 {s.get('detail_checked', 0):,}건(본문 확인 불가 {s.get('text_unavailable', 0):,}건) · "
        f"기간 외 제외 {s.get('time_excluded', 0):,}건 · 관련성 등 제외 {s.get('relevance_excluded', 0):,}건 · "
        f"같은 사건 유사 보도 통합 {s.get('event_merged', 0):,}건 · "
        f"판단 방식: {'LLM 보조 + 규칙' if run.llm_used else '규칙 기반'}"
    )
    if run.warnings:
        with st.expander(f"확인 필요 알림 {len(run.warnings)}건", expanded=False):
            for w in run.warnings:
                st.warning(w)

    # 필터 (이미 수집된 결과에서만 필터링 - API 재호출 없음)
    with st.container(border=True):
        f1, f2, f3, f4, f5 = st.columns([1.3, 1.5, 1.3, 1.4, 0.9])
        with f1:
            topics = st.multiselect("주제 선택", TOPICS, key=f"f_topic_{rid}", placeholder="전체 주제")
        with f2:
            keywords = st.multiselect("검색 키워드 선택", NEWS_KEYWORDS, key=f"f_kw_{rid}", placeholder="전체 키워드")
        with f3:
            publishers = st.multiselect("언론사 선택", publisher_options(run), key=f"f_pub_{rid}", placeholder="전체 언론사")
        with f4:
            query = st.text_input("기사 제목 검색", key=f"f_q_{rid}", placeholder="제목에 포함된 단어")
        with f5:
            sort_mode = st.radio("정렬(키워드별·전체)", [SORT_LATEST, SORT_IMPORTANCE], key=f"f_sort_{rid}")
    filters = Filters(topics or None, keywords or None, publishers or None, query)

    render_downloads(run)

    tab_topic, tab_kw, tab_all, tab_ex = st.tabs(["주제별 보기", "키워드별 보기", "전체 기사", "제외 기사(검토용)"])
    with tab_topic:
        render_topic_view(run, filters)
    with tab_kw:
        render_keyword_view(run, filters, sort_mode, rid)
    with tab_all:
        render_all_view(run, filters, sort_mode)
    with tab_ex:
        render_excluded_view(run, rid)


def render_downloads(run: SearchRun) -> None:
    stamp = file_stamp(run)
    c1, c2, c3, c4 = st.columns(4)
    try:
        c1.download_button("CSV 다운로드 (전체기사)", to_csv_bytes(all_articles_df(run)), f"KINS_언론동향_전체기사_{stamp}.csv", "text/csv", width="stretch")
        c2.download_button("CSV 다운로드 (키워드별)", to_csv_bytes(keyword_df(run)), f"KINS_언론동향_키워드별_{stamp}.csv", "text/csv", width="stretch")
    except Exception as exc:
        st.error(f"CSV 파일 생성 중 오류가 발생했습니다: {exc}")
    try:
        c3.download_button(
            "Excel 다운로드",
            to_excel_bytes(run),
            f"KINS_언론동향_{stamp}.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch",
            type="primary",
        )
    except ExportError as exc:
        c3.error(str(exc))
    c4.download_button("보고용 텍스트(TXT)", to_text_report(run).encode("utf-8-sig"), f"KINS_언론동향_{stamp}.txt", "text/plain", width="stretch")


def render_topic_view(run: SearchRun, filters: Filters) -> None:
    groups = similar_members(run)
    view = topic_view(run, filters)
    st.caption("각 기사는 한 주제에만 표시되며, 같은 사건의 유사 보도는 대표 기사 아래에 접혀 있습니다. 주제 내 정렬: 중요도순")
    for topic in TOPICS:
        items = view.get(topic, [])
        parts = [topic_header(topic, len(items))]
        if not items:
            parts.append(empty_message())
        for a in items:
            parts.append(article_card(a, groups.get(a.duplicate_group), show_topic=False))
        st.markdown("".join(parts), unsafe_allow_html=True)


def render_keyword_view(run: SearchRun, filters: Filters, sort_mode: str, rid: int) -> None:
    c1, c2 = st.columns([1, 1])
    collapse = c1.checkbox("같은 사건 유사 보도는 1건만 표시", value=True, key=f"kw_collapse_{rid}")
    expand_all = c2.checkbox("모든 키워드 펼치기", value=False, key=f"kw_expand_{rid}")
    view = keyword_view(run, filters, collapse_similar=collapse, sort_mode=sort_mode)
    counts = [(i, kw, len(view[kw])) for i, kw in enumerate(NEWS_KEYWORDS, start=1) if kw in view]
    st.markdown(keyword_chips(counts), unsafe_allow_html=True)
    st.caption("키워드별 보기에서는 하나의 기사가 여러 키워드에서 검색되었다면 각 키워드에 모두 표시됩니다.")
    groups = similar_members(run)
    for i, kw, n in counts:
        with st.expander(f"{i}. {kw} ({n}건)", expanded=expand_all):
            if n == 0:
                st.markdown(empty_message(), unsafe_allow_html=True)
                continue
            html_parts = [article_card(a, groups.get(a.duplicate_group) if collapse and a.is_representative else None) for a in view[kw]]
            st.markdown("".join(html_parts), unsafe_allow_html=True)


def render_all_view(run: SearchRun, filters: Filters, sort_mode: str) -> None:
    items = all_articles_view(run, filters, sort_mode)
    st.caption(f"포함된 전체 기사 {len(items)}건 (같은 사건 유사 보도 포함)")
    if not items:
        st.markdown(empty_message(), unsafe_allow_html=True)
        return
    df = pd.DataFrame(
        [
            {
                "제목": a.title,
                "언론사": a.display_publisher,
                "게시시각": format_kst(a.published_at),
                "주제": a.topic,
                "검색키워드": ", ".join(a.matched_keywords),
                "대표": "●" if a.is_representative else "",
                "원문": a.link,
                "네이버": a.naver_url,
            }
            for a in items
        ]
    )
    st.dataframe(
        df,
        hide_index=True,
        width="stretch",
        height=min(720, 38 + 35 * len(df)),
        column_config={
            "제목": st.column_config.TextColumn(width="large"),
            "원문": st.column_config.LinkColumn("원문", display_text="원문 보기", width="small"),
            "네이버": st.column_config.LinkColumn("네이버", display_text="네이버", width="small"),
            "대표": st.column_config.TextColumn(width="small", help="같은 사건 유사 보도 중 대표 기사"),
        },
    )


def render_excluded_view(run: SearchRun, rid: int) -> None:
    excluded = run.excluded
    st.caption(
        f"자동 제외된 기사 {len(excluded)}건입니다. 선별 기준 점검용이며, 필요하면 원문을 직접 확인하세요."
    )
    if not excluded:
        st.markdown(empty_message(), unsafe_allow_html=True)
        return
    show_debug = st.checkbox("판단 근거(디버그) 표시", value=False, key=f"ex_debug_{rid}")
    reasons = sorted({a.exclusion_reason or "" for a in excluded})
    pick = st.multiselect("제외 사유 선택", reasons, key=f"ex_reason_{rid}", placeholder="전체 사유")
    rows = []
    for a in sorted(excluded, key=lambda x: (x.exclusion_reason or "", x.title)):
        if pick and (a.exclusion_reason or "") not in pick:
            continue
        row = {
            "제목": a.title,
            "언론사": a.display_publisher,
            "게시시각": format_kst(a.published_at),
            "검색키워드": ", ".join(list(a.matched_keywords) + [f"{k}(제외)" for k in a.rejected_keywords]),
            "제외사유": a.exclusion_reason,
            "원문": a.link,
        }
        if show_debug:
            row["판단"] = a.judged_by
            row["규칙근거"] = a.rule_reason
            row["LLM근거"] = a.llm_reason
            row["원문확인오류"] = a.fetch_error
        rows.append(row)
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        width="stretch",
        height=min(720, 38 + 35 * len(rows)),
        column_config={"원문": st.column_config.LinkColumn("원문", display_text="원문 보기", width="small")},
    )


def check_access(settings) -> bool:
    """APP_PASSWORD 가 설정된 경우 접속 비밀번호를 확인한다 (미설정 시 통과)."""
    if not settings.app_password or st.session_state.get("authenticated"):
        return True
    with st.container(border=True):
        st.markdown('<div class="kins-section">접속 확인</div>', unsafe_allow_html=True)
        with st.form("login_form", border=False):
            pw = st.text_input("접속 비밀번호", type="password")
            ok = st.form_submit_button("확인", type="primary")
        if ok:
            if hmac.compare_digest(pw.encode("utf-8"), settings.app_password.encode("utf-8")):
                st.session_state["authenticated"] = True
                st.rerun()
            st.error("비밀번호가 올바르지 않습니다.")
    return False


def apply_streamlit_secrets() -> None:
    """Streamlit Cloud 의 Secrets(최상위 문자열 값)를 환경변수로 넘긴다. 로컬에 secrets 가 없으면 무시."""
    try:
        items = dict(st.secrets)
    except Exception:
        return
    for key, value in items.items():
        if isinstance(value, (str, int, float, bool)) and not os.environ.get(key):
            os.environ[key] = str(value)


def main() -> None:
    apply_streamlit_secrets()
    settings = load_settings()
    render_header()
    if not check_access(settings):
        return
    render_search_form(settings)
    run = st.session_state.get("run")
    if run is not None:
        render_results(run)


main()
