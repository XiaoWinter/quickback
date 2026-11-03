def _kind(ch: str) -> str | None:
    if "\u3400" <= ch <= "\u9fff" or "\uf900" <= ch <= "\ufaff":
        return "zh"
    if ch.isascii() and (ch.isalpha() or ch.isdigit()):
        return "en"
    return None


def split_transcript(text: str) -> list[dict]:
    """Split a mixed transcript into English and Chinese runs for coloring."""
    segments: list[dict] = []
    buf: list[str] = []
    lang: str | None = None

    def flush() -> None:
        nonlocal buf, lang
        if buf:
            segments.append({"lang": lang or "other", "text": "".join(buf)})
        buf = []
        lang = None

    for ch in text or "":
        kind = _kind(ch)
        if kind is None or lang is None or kind == lang:
            if kind is not None:
                lang = kind
            buf.append(ch)
            continue
        neutrals: list[str] = []
        while buf and _kind(buf[-1]) is None:
            neutrals.append(buf.pop())
        neutrals.reverse()
        flush()
        lang = kind
        buf = neutrals + [ch]
    flush()
    return [item for item in segments if item["text"]]
