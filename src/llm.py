"""선택적 LLM 판단 (OpenAI 호환 Chat Completions API).

- OPENAI_API_KEY 와 OPENAI_MODEL 이 모두 설정된 경우에만 사용한다 (모델명 하드코딩 없음).
- JSON 형식 응답을 받아 검증한 뒤 사용하며, 오류가 나면 규칙 기반 결과를 그대로 쓴다.
- LLM 은 '판단'에만 쓰며 기사 제목·언론사·URL·시각을 만들거나 바꾸지 않는다.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass

import requests

from .config import PERSON_RULES, TOPICS, Settings
from .models import NewsArticle
from .utils import HttpError, format_kst, new_session, request_with_retry

logger = logging.getLogger(__name__)


class LLMError(Exception):
    pass


@dataclass
class LLMJudgement:
    include: bool
    topic: str | None
    relevance_score: float | None
    is_person_match: bool | None
    exclude_reason: str | None
    reason: str | None


_SYSTEM_PROMPT = """당신은 한국원자력안전기술원(KINS)의 일일 언론동향 담당자입니다.
네이버 뉴스에서 수집된 기사가 KINS 구성원이 업무 참고용으로 확인할 가치가 있는지 판단합니다.
기사 내용을 지어내지 말고, 주어진 제목/본문/요약에 근거해서만 판단하세요. 반드시 JSON 으로만 답하세요.

[우선 포함]
KINS 발표·연구·점검·기술지원·조직·인사 / 임승철 KINS 원장 / 원자력안전위원회 회의·의결·허가·심사·현장점검 /
최원호 원안위원장 / 국내 원전 사고·고장·정지·재가동·안전 / 원자력시설 건설·운영·변경·해체·안전·규제 /
SMR 안전성·규제·정책·R&D·상용화 / 방사능·방사선·삼중수소·라돈 안전 / 핵폐기물·사용후핵연료·방폐물 정책·관리 /
국내외 원자력 정책·안전 동향 / 원자력 관련 정부 정책·법률·예산 / 공기업·공공기관 정책·조직·경영·인사 /
공공기관 통합·통폐합 / 출연연·정출연 정책·조직·기관장 / 지정 인물의 원자력·에너지·안전 정책 발언

[제외]
원자력 키워드가 우연히 등장했을 뿐 중심 내용이 다른 기사 / 반도체·AI·데이터센터 중심이고 원자력 비중이 낮은 기사 /
증권·주식·종목·테마주·단순 주가 기사 / 기업 홍보·제품 출시·단순 MOU·수상·단순 행사 일정 기사 /
내용이 거의 없는 사진 기사 / 근거가 부족한 추측성 기사 / 정쟁·정치적 비난 중심 기사 /
신규 정보 없이 오래된 내용을 반복한 기사 / 동명이인 기사 / 연예·스포츠·문화 등 무관 기사
단, AI·데이터센터·산업 기사라도 원전 건설, SMR, 원자력 안전·규제·전력정책이 핵심 쟁점이면 포함.

[동명이인 판별 - 아래 인물은 해당 신분일 때만 인정]
{persons}

[주제 - 반드시 아래 6개 중 정확히 하나]
1. 한국원자력안전기술원(KINS): KINS 가 주요 주체(발표·연구·점검·기술지원·조직·인사, 임승철 원장)
2. 원자력안전위원회: 원안위가 주요 주체(회의·의결·허가·심사·현장점검·규제정책, 최원호 위원장)
3. 국내 원자력&방사선: 국내 원전 건설·운영·정지·사고, 국내 SMR, 방사능·방사선·삼중수소·라돈, 방폐물·사용후핵연료, 국내 연구기관·산업·지역
4. 국외 원자력&방사선: 해외 원전·SMR·규제·방사능, IAEA, 국가 간 원자력 협력, 해외 원자력 정책
5. 정부&정치권: 정부 원자력·에너지 정책, 국회, 국정감사, 정당·정치인, 법률, 예산, 정부 간 협상, 국가 R&D 정책, 공론화
6. 기타: 공기업·공공기관(통폐합·조직개편·경영·인사), 출연연·정출연, 그 외 참고 가치가 있는 기사
원안위가 원전 계속운전을 승인한 기사는 '원자력안전위원회'. KINS 가 핵심 주체면 KINS 를 우선.

[중요도 relevance_score 0~100]
KINS 직접 관련 > 원자력 안전·규제 중요성 > KINS/원안위/정부 공식 발표 > 사고·고장·정지·허가·점검 > 정책 영향 > 새로운 사실 > 구체성
"""

_ARTICLE_INSTRUCTIONS = """아래 기사 각각에 대해 판단하세요.
응답 형식(JSON):
{"results": [{"id": "기사 id", "include": true/false, "exclude_reason": "제외 시 짧은 사유 또는 빈 문자열",
"topic": "6개 주제 중 하나", "relevance_score": 0~100 정수, "is_person_match": true/false/null(인물 키워드가 없으면 null),
"reason": "판단 근거 한 문장"}]}
"""

_PAIR_INSTRUCTIONS = """아래 기사 쌍이 '같은 사건/같은 보도자료'를 다룬 사실상 중복 보도인지 판단하세요.
같은 주제라도 다른 사건(다른 호기, 다른 날짜의 회의, 다른 기관)이면 false 입니다.
응답 형식(JSON): {"results": [{"pair_id": "쌍 id", "same_event": true/false}]}
"""


def _person_text() -> str:
    return "\n".join(f"- {r.name}: {r.description}" for r in PERSON_RULES.values())


class LLMClient:
    def __init__(self, settings: Settings):
        if not settings.llm_configured:
            raise LLMError("OPENAI_API_KEY 또는 OPENAI_MODEL 이 설정되지 않았습니다.")
        self.api_key = settings.openai_api_key
        self.model = settings.openai_model
        self.base_url = settings.openai_base_url.rstrip("/")
        self.timeout = settings.llm_timeout
        self._local = threading.local()
        self.system_prompt = _SYSTEM_PROMPT.format(persons=_person_text())

    def _session(self) -> requests.Session:
        s = getattr(self._local, "session", None)
        if s is None:
            s = new_session()
            self._local.session = s
        return s

    def chat_json(self, system: str, user: str) -> dict:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            resp = request_with_retry(
                self._session(),
                "POST",
                f"{self.base_url}/chat/completions",
                headers=headers,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                timeout=self.timeout,
                retries=2,
            )
        except HttpError as exc:
            raise LLMError(f"LLM API 요청 실패: {exc}") from exc
        if resp.status_code in (401, 403):
            raise LLMError("LLM API 인증 실패(OPENAI_API_KEY 확인 필요)")
        if resp.status_code != 200:
            raise LLMError(f"LLM API 오류(HTTP {resp.status_code}): {resp.text[:200]}")
        try:
            content = resp.json()["choices"][0]["message"]["content"]
            return json.loads(content)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMError("LLM 응답(JSON)을 해석할 수 없습니다.") from exc

    # ------------------------------------------------------------------
    def judge_articles(self, articles: list[NewsArticle]) -> dict[str, LLMJudgement]:
        blocks = []
        for a in articles:
            body = (a.article_text or a.description or "")[:1500]
            persons = [k for k in a.matched_keywords if k in PERSON_RULES]
            blocks.append(
                json.dumps(
                    {
                        "id": a.article_id,
                        "title": a.title,
                        "publisher": a.publisher or "",
                        "published_at": format_kst(a.published_at),
                        "matched_keywords": a.matched_keywords,
                        "person_keywords": persons,
                        "body_status": a.text_status,
                        "body_or_summary": body,
                    },
                    ensure_ascii=False,
                )
            )
        user = _ARTICLE_INSTRUCTIONS + "\n기사 목록:\n" + "\n".join(blocks)
        data = self.chat_json(self.system_prompt, user)
        results = data.get("results")
        if not isinstance(results, list):
            raise LLMError("LLM 응답에 results 배열이 없습니다.")
        out: dict[str, LLMJudgement] = {}
        valid_ids = {a.article_id for a in articles}
        for r in results:
            if not isinstance(r, dict) or r.get("id") not in valid_ids:
                continue
            include = r.get("include")
            if not isinstance(include, bool):
                continue
            topic = r.get("topic") if isinstance(r.get("topic"), str) else None
            score = r.get("relevance_score")
            try:
                score = max(0.0, min(100.0, float(score))) if score is not None else None
            except (TypeError, ValueError):
                score = None
            pm = r.get("is_person_match")
            out[r["id"]] = LLMJudgement(
                include=include,
                topic=topic,
                relevance_score=score,
                is_person_match=pm if isinstance(pm, bool) else None,
                exclude_reason=(str(r.get("exclude_reason") or "")[:200] or None),
                reason=(str(r.get("reason") or "")[:300] or None),
            )
        return out

    def judge_same_events(self, pairs: list[tuple[NewsArticle, NewsArticle]]) -> dict[int, bool]:
        lines = []
        for i, (a, b) in enumerate(pairs):
            lines.append(
                json.dumps(
                    {
                        "pair_id": str(i),
                        "a": {"title": a.title, "body": (a.article_text or a.description or "")[:600]},
                        "b": {"title": b.title, "body": (b.article_text or b.description or "")[:600]},
                    },
                    ensure_ascii=False,
                )
            )
        data = self.chat_json(self.system_prompt, _PAIR_INSTRUCTIONS + "\n" + "\n".join(lines))
        out: dict[int, bool] = {}
        for r in data.get("results") or []:
            try:
                idx = int(r.get("pair_id"))
            except (TypeError, ValueError):
                continue
            if isinstance(r.get("same_event"), bool):
                out[idx] = r["same_event"]
        return out


def topics_valid(topic: str | None) -> bool:
    return topic in TOPICS
