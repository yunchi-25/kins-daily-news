"""화면 표시용 HTML 조각 (Streamlit 비의존, 테스트 가능)."""

from __future__ import annotations

import html

from .models import SOURCE_API, TEXT_OK, NewsArticle, SearchRun
from .utils import format_kst, is_http_url

CSS = """
<style>
:root {
  --kins-navy: #0B2E59;
  --kins-blue: #1F4E8C;
  --kins-line: #D9E1EA;
  --kins-soft: #F4F7FB;
  --kins-text: #1B2430;
  --kins-sub: #4D5B6B;
}
.block-container { padding-top: 3.4rem; padding-bottom: 3rem; max-width: 1280px; }
.kins-title { font-size: 1.6rem; font-weight: 800; color: var(--kins-navy); margin: 0 0 .2rem; letter-spacing: -0.02em; }
.kins-desc { color: var(--kins-sub); font-size: .93rem; margin-bottom: .2rem; line-height: 1.55; }
.kins-section { font-size: 1.02rem; font-weight: 700; color: var(--kins-navy); margin: .1rem 0 .4rem; }
.kins-end { background: var(--kins-soft); border: 1px solid var(--kins-line); border-radius: 6px;
  padding: .45rem .7rem; font-size: .88rem; color: var(--kins-text); line-height: 1.5; }
.kins-end b { color: var(--kins-navy); }
.kins-summary { display: grid; grid-template-columns: 2.4fr repeat(4, 1fr); gap: 0; border: 1px solid var(--kins-line);
  border-radius: 8px; overflow: hidden; background: #fff; margin: .2rem 0 .5rem; }
.kins-summary > div { padding: .65rem .9rem; border-right: 1px solid var(--kins-line); }
.kins-summary > div:last-child { border-right: none; }
.kins-summary .lbl { font-size: .78rem; color: var(--kins-sub); margin-bottom: .15rem; }
.kins-summary .val { font-size: 1.15rem; font-weight: 700; color: var(--kins-navy); }
.kins-summary .val.small { font-size: .95rem; }
.kins-summary .hl { background: #EEF3FA; }
@media (max-width: 900px) {
  .kins-summary { grid-template-columns: 1fr 1fr; }
  .kins-summary > div { border-bottom: 1px solid var(--kins-line); }
  .kins-summary > div:first-child { grid-column: 1 / -1; }
}
.kins-topic { font-size: 1.08rem; font-weight: 700; color: var(--kins-navy); border-bottom: 2px solid var(--kins-navy);
  padding: .2rem 0 .3rem; margin: 1.1rem 0 .55rem; }
.kins-topic .cnt { color: var(--kins-blue); font-weight: 700; }
.kins-empty { color: #6B7785; font-size: .9rem; padding: .5rem .2rem .7rem; }
.kc { border: 1px solid var(--kins-line); border-left: 4px solid var(--kins-blue); border-radius: 6px; background: #fff;
  padding: .55rem .85rem .6rem; margin: 0 0 .5rem; }
.kc-t { font-size: 1.0rem; font-weight: 700; line-height: 1.45; word-break: keep-all; overflow-wrap: anywhere; }
.kc-t a { color: var(--kins-text) !important; text-decoration: none; }
.kc-t a:hover { color: var(--kins-blue) !important; text-decoration: underline; }
.kc-m { font-size: .84rem; color: var(--kins-sub); margin-top: .18rem; }
.kc-k { font-size: .8rem; color: #5B6B7D; margin-top: .1rem; overflow-wrap: anywhere; }
.kc-tag { display: inline-block; font-size: .72rem; color: var(--kins-blue); background: #EEF3FA; border: 1px solid #D3E0F0;
  border-radius: 3px; padding: 0 .35rem; margin-left: .35rem; vertical-align: 1px; }
.kc-a { margin-top: .35rem; font-size: .8rem; }
.kc-a a { display: inline-block; color: var(--kins-blue) !important; border: 1px solid var(--kins-blue); border-radius: 4px;
  padding: .02rem .5rem; text-decoration: none; margin-right: .3rem; }
.kc-a a:hover { background: var(--kins-blue); color: #fff !important; }
.kc-a a.sub { border-color: #A9B8CA; color: var(--kins-sub) !important; }
.kc-a a.sub:hover { background: #EEF3FA; color: var(--kins-navy) !important; }
.kc-note { color: #8A5A00; font-size: .76rem; margin-left: .2rem; }
.kc details { margin-top: .35rem; font-size: .82rem; color: var(--kins-sub); }
.kc details summary { cursor: pointer; color: var(--kins-blue); }
.kc details ul { margin: .25rem 0 0 1rem; padding: 0; }
.kc details li { margin: .1rem 0; overflow-wrap: anywhere; }
.kc details li a { color: var(--kins-text) !important; }
.kw-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(165px, 1fr)); gap: .3rem; margin: .2rem 0 .6rem; }
.kw-chip { border: 1px solid var(--kins-line); border-radius: 4px; padding: .22rem .5rem; font-size: .82rem; background: #fff;
  display: flex; justify-content: space-between; gap: .4rem; }
.kw-chip .n { font-weight: 700; color: var(--kins-navy); white-space: nowrap; }
.kw-chip.zero { background: #F7F8FA; color: #8A94A0; }
.kw-chip.zero .n { color: #8A94A0; }
.kins-stages { font-size: .84rem; color: #8A94A0; margin: .1rem 0 .3rem; }
.kins-stages .on { color: var(--kins-navy); font-weight: 700; }
.kins-stages .done { color: var(--kins-blue); }
</style>
"""


def esc(text: str | None) -> str:
    """HTML 이스케이프 + Streamlit markdown 특수문자(수식 $, 취소선 ~, 이모지 :) 무력화."""
    s = html.escape(text or "", quote=True)
    return s.replace("$", "&#36;").replace("~", "&#126;").replace(":", "&#58;").replace("[", "&#91;").replace("]", "&#93;").replace("*", "&#42;").replace("_", "&#95;")


def safe_url(url: str | None) -> str | None:
    if not is_http_url(url):
        return None
    return html.escape(url.strip(), quote=True)


def _link(url: str | None, label: str, cls: str = "") -> str:
    u = safe_url(url)
    if not u:
        return ""
    c = f' class="{cls}"' if cls else ""
    return f'<a{c} href="{u}" target="_blank" rel="noopener noreferrer">{esc(label)}</a>'


def article_card(a: NewsArticle, similar: list[NewsArticle] | None = None, show_topic: bool = True) -> str:
    url = safe_url(a.link)
    title = esc(a.title)
    title_html = f'<a href="{url}" target="_blank" rel="noopener noreferrer">{title}</a>' if url else title
    time_text = format_kst(a.published_at)
    if a.published_at_source == SOURCE_API:
        time_text += " (검색API 기준)"
    meta = f"{esc(a.display_publisher)} | {esc(time_text)}"
    if show_topic and a.topic:
        meta += f'<span class="kc-tag">{esc(a.topic)}</span>'
    keywords = esc(", ".join(a.matched_keywords)) or "-"
    actions = _link(a.link, "기사 원문 보기")
    if a.naver_url and a.naver_url != a.link:
        actions += _link(a.naver_url, "네이버 뉴스", "sub")
    if a.text_status != TEXT_OK:
        actions += f'<span class="kc-note">본문 확인 불가</span>'
    extra = ""
    others = [s for s in (similar or []) if s.article_id != a.article_id]
    if others:
        items = "".join(
            f"<li>{_link(s.link, s.title) or esc(s.title)} · {esc(s.display_publisher)} · {esc(format_kst(s.published_at, '%m-%d %H:%M'))}</li>"
            for s in others
        )
        extra = f"<details><summary>같은 사건 유사 보도 {len(others)}건</summary><ul>{items}</ul></details>"
    return (
        f'<div class="kc"><div class="kc-t">{title_html}</div>'
        f'<div class="kc-m">{meta}</div>'
        f'<div class="kc-k">검색 키워드: {keywords}</div>'
        f'<div class="kc-a">{actions}</div>{extra}</div>'
    )


def topic_header(topic: str, n: int) -> str:
    return f'<div class="kins-topic">■ {esc(topic)} <span class="cnt">({n}건)</span></div>'


def empty_message() -> str:
    return '<div class="kins-empty">확인된 기사 없음 (0건)</div>'


def summary_box(run: SearchRun) -> str:
    s = run.stats
    rng = f"{format_kst(run.start)} ~ {format_kst(run.end)}"
    cells = [
        ("검색 범위 (KST)", rng, "val small"),
        ("검색 키워드", "28개", "val"),
        ("수집 기사", f"{s.get('collected', 0):,}건", "val"),
        ("중복 제거 후", f"{s.get('unique', 0):,}건", "val"),
        ("최종 후보", f"{s.get('final', 0):,}건", "val"),
    ]
    html_cells = "".join(
        f'<div class="{"hl" if i == 4 else ""}"><div class="lbl">{esc(lbl)}</div><div class="{cls}">{esc(val)}</div></div>'
        for i, (lbl, val, cls) in enumerate(cells)
    )
    return f'<div class="kins-summary">{html_cells}</div>'


def keyword_chips(counts: list[tuple[int, str, int]]) -> str:
    chips = "".join(
        f'<div class="kw-chip{" zero" if n == 0 else ""}"><span>{i}. {esc(kw)}</span><span class="n">{n}건</span></div>'
        for i, kw, n in counts
    )
    return f'<div class="kw-grid">{chips}</div>'


def stage_line(stages: list[str], current: str | None, done: set[str]) -> str:
    parts = []
    for st in stages:
        cls = "on" if st == current else ("done" if st in done else "")
        mark = "✓ " if st in done and st != current else ""
        parts.append(f'<span class="{cls}">{mark}{esc(st)}</span>')
    return '<div class="kins-stages">' + " → ".join(parts) + "</div>"
