"""Detect substantial literal source reuse in generated author files."""

def source_overlap(files, observations, *, window=32):
    def normalize(text):
        return "".join(char for char in text if char.isalnum())

    source_windows = set()
    for observation in observations:
        for evidence in observation.get("evidence", []):
            text = normalize(str(evidence.get("excerpt") or ""))
            source_windows.update(text[i:i + window] for i in range(len(text) - window + 1))
    hits = []
    for path, content in files.items():
        # Keep line numbers in the author file, but match across wrapped lines.
        characters = [(char, line) for line, text in enumerate(content.splitlines(), 1)
                      for char in text if char.isalnum()]
        text = "".join(char for char, _ in characters)
        for i in range(len(text) - window + 1):
            if text[i:i + window] in source_windows:
                hits.append({"path": path, "line": characters[i][1], "match": text[i:i + window]})
                break
    return hits
