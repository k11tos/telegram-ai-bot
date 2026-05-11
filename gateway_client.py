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
