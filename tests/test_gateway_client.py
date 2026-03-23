from gateway_client import extract_model_names


def test_extract_model_names_supports_models_and_data_shapes():
    assert extract_model_names({"models": [{"id": "gpt-4o-mini"}, {"name": "claude-3-5"}]}) == [
        "gpt-4o-mini",
        "claude-3-5",
    ]
    assert extract_model_names({"data": ["o3-mini", {"id": "gpt-4.1"}]}) == ["o3-mini", "gpt-4.1"]


def test_extract_model_names_ignores_unsupported_items():
    payload = [{"id": "gpt-4o-mini"}, {"name": 123}, None, 42, {"other": "value"}]

    assert extract_model_names(payload) == ["gpt-4o-mini"]
