import httpx

AI_GATEWAY_AGENT_BRAIN_PATH = "/agent/brain"


class GatewayClientError(Exception):
    """Controlled gateway client error for recoverable request/response failures."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


async def post_agent_brain(
    client: httpx.AsyncClient,
    payload: dict,
    request_id: str | None = None,
) -> dict:
    headers = {"X-Request-Id": request_id} if request_id else None

    try:
        response = await client.post(
            AI_GATEWAY_AGENT_BRAIN_PATH,
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
    except httpx.TimeoutException as error:
        raise GatewayClientError("agent_brain_timeout") from error
    except httpx.ConnectError as error:
        raise GatewayClientError("agent_brain_connect_error") from error
    except httpx.RequestError as error:
        raise GatewayClientError("agent_brain_request_failed") from error
    except httpx.HTTPStatusError as error:
        raise GatewayClientError("agent_brain_request_failed") from error

    try:
        body = response.json()
    except ValueError as error:
        raise GatewayClientError("agent_brain_invalid_json") from error

    if not isinstance(body, dict):
        raise GatewayClientError("agent_brain_malformed_response")

    return body


def extract_model_names(payload) -> list[str]:
    if isinstance(payload, dict):
        if isinstance(payload.get("models"), list):
            source = payload["models"]
        elif isinstance(payload.get("data"), list):
            source = payload["data"]
        else:
            source = []
    elif isinstance(payload, list):
        source = payload
    else:
        source = []

    model_names = []
    for item in source:
        if isinstance(item, str):
            model_names.append(item)
        elif isinstance(item, dict):
            model_id = item.get("id") or item.get("name")
            if isinstance(model_id, str):
                model_names.append(model_id)

    return model_names
