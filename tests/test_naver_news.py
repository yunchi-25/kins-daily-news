"""NAVER API HUB 요청 형식 테스트 (실제 호출 없음)."""

from datetime import datetime

import pytest

from src.config import KST
from src.naver_news import NAVER_NEWS_API_URL, NaverAuthError, NaverNewsClient, auth_headers


class FakeResp:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._data = data if data is not None else {"total": 0, "items": []}
        self.text = str(self._data)

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self.resp


def test_endpoint_and_headers_are_api_hub():
    assert NAVER_NEWS_API_URL == "https://naverapihub.apigw.ntruss.com/search/v1/news"
    assert auth_headers("ID", "SECRET") == {"X-NCP-APIGW-API-KEY-ID": "ID", "X-NCP-APIGW-API-KEY": "SECRET"}


def test_request_uses_get_api_hub_headers_and_params():
    sess = FakeSession(FakeResp())
    client = NaverNewsClient("my-id", "my-secret", session=sess, request_interval=0)
    client.search_keyword("원자력", datetime(2026, 10, 16, 16, 0, tzinfo=KST), datetime(2026, 10, 19, 9, 0, tzinfo=KST))
    method, url, kw = sess.calls[0]
    assert method == "GET" and url == NAVER_NEWS_API_URL
    assert kw["headers"] == {"X-NCP-APIGW-API-KEY-ID": "my-id", "X-NCP-APIGW-API-KEY": "my-secret"}
    assert not any(h.lower().startswith("x-naver-client") for h in kw["headers"])
    assert kw["params"] == {"query": "원자력", "display": 100, "start": 1, "sort": "date"}


def test_auth_error_message_points_to_api_hub():
    sess = FakeSession(FakeResp(401, {"error": {"errorCode": "200", "message": "Authentication Failed"}}))
    client = NaverNewsClient("bad", "bad", session=sess, request_interval=0)
    with pytest.raises(NaverAuthError) as exc:
        client.search_keyword("원자력", datetime(2026, 10, 16, 16, 0, tzinfo=KST), datetime(2026, 10, 19, 9, 0, tzinfo=KST))
    msg = str(exc.value)
    assert "NAVER Cloud Platform > NAVER API HUB > Application" in msg
    assert "'검색 API'" in msg and "Authentication Failed" in msg
    assert "개발자센터" not in msg
