import asyncio

import httpx
import pytest

import bot


class FakeResponse:
    def __init__(self, payload=None, status_error=None, json_error=None):
        self._payload = payload
        self._status_error = status_error
        self._json_error = json_error

    def raise_for_status(self):
        if self._status_error is not None:
            raise self._status_error

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class FakeClient:
    def __init__(self, response=None, post_error=None):
        self.response = response
        self.post_error = post_error
        self.calls = []

    async def post(self, path, json=None, headers=None):
        self.calls.append({"path": path, "json": json, "headers": headers})
        if self.post_error is not None:
            raise self.post_error
        return self.response


def test_post_agent_brain_returns_dict_body():
    client = FakeClient(response=FakeResponse(payload={"summary": "ok"}))

    result = asyncio.run(bot.post_agent_brain(client, payload={"messages": []}, request_id="abc123"))

    assert result == {"summary": "ok"}
    assert client.calls == [
        {
            "path": bot.AI_GATEWAY_AGENT_BRAIN_PATH,
            "json": {"messages": []},
            "headers": {"X-Request-Id": "abc123"},
        }
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {
            "overall_status": "ok",
            "message_lines": ["ai-gateway 정상", "디스크 사용률 71.2%"],
        },
        {
            "overall_status": "ok",
            "message_lines": ["ai-gateway 정상", "디스크 사용률 71.2%"],
            "has_notable_changes": False,
            "changes": [],
        },
        {
            "overall_status": "warning",
            "message_lines": ["일부 점검 필요"],
            "has_notable_changes": True,
            "changes": [
                {
                    "kind": "restart_detected",
                    "field": "service_states.ai-gateway",
                    "previous": "healthy",
                    "current": "healthy",
                    "notable": True,
                },
                {
                    "kind": "service_state_change",
                    "field": "service_states.worker",
                    "previous": "degraded",
                    "current": "healthy",
                    "notable": True,
                },
                {
                    "kind": "docker_summary_change",
                    "field": "docker_summary",
                    "previous": {"running": 4, "restarting": 0},
                    "current": {"running": 5, "restarting": 1},
                    "notable": True,
                },
                {
                    "kind": "metric_delta",
                    "field": "memory_percent",
                    "previous": 64.1,
                    "current": 73.2,
                    "notable": True,
                },
                {"kind": "new_gateway_change_type", "details": {"foo": "bar"}, "notable": True},
            ],
        },
    ],
)
def test_post_agent_brain_accepts_additive_change_fields(payload):
    client = FakeClient(response=FakeResponse(payload=payload))

    result = asyncio.run(bot.post_agent_brain(client, payload={}))

    assert result == payload


@pytest.mark.parametrize(
    ("post_error", "expected_code"),
    [
        (httpx.ConnectTimeout("connect timeout"), "agent_brain_timeout"),
        (httpx.ReadTimeout("read timeout"), "agent_brain_timeout"),
        (httpx.ConnectError("network down"), "agent_brain_connect_error"),
        (httpx.RequestError("network down"), "agent_brain_request_failed"),
    ],
)
def test_post_agent_brain_raises_controlled_error_on_network_failures(post_error, expected_code):
    client = FakeClient(post_error=post_error)

    with pytest.raises(bot.GatewayClientError, match=expected_code):
        asyncio.run(bot.post_agent_brain(client, payload={"messages": []}))


def test_post_agent_brain_raises_controlled_error_on_http_status_error():
    request = httpx.Request("POST", "http://test/agent/brain")
    response = httpx.Response(503, request=request)
    status_error = httpx.HTTPStatusError("service unavailable", request=request, response=response)
    client = FakeClient(response=FakeResponse(status_error=status_error))

    with pytest.raises(bot.GatewayClientError, match="agent_brain_request_failed"):
        asyncio.run(bot.post_agent_brain(client, payload={"messages": []}))


def test_post_agent_brain_raises_controlled_error_on_invalid_json():
    client = FakeClient(response=FakeResponse(json_error=ValueError("invalid json")))

    with pytest.raises(bot.GatewayClientError, match="agent_brain_invalid_json"):
        asyncio.run(bot.post_agent_brain(client, payload={"messages": []}))


def test_post_agent_brain_raises_controlled_error_on_malformed_json_shape():
    client = FakeClient(response=FakeResponse(payload=["not", "an", "object"]))

    with pytest.raises(bot.GatewayClientError, match="agent_brain_malformed_response"):
        asyncio.run(bot.post_agent_brain(client, payload={"messages": []}))
