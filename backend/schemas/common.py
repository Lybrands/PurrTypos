def normalize_locale_tag(value: object) -> str:
    parts = [
        part
        for part in str(value or "").strip().replace("_", "-").split("-")
        if part
    ]
    if not parts or any(not part.isalnum() for part in parts):
        raise ValueError("locale must be a valid language tag")
    return "-".join(parts)
