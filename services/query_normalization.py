import re
import unicodedata
from collections import Counter
from collections.abc import Iterable


_DASHES = r"\-‐‑‒–—−"
_NAME = re.compile(rf"\b[^\W\d_]+(?:[{_DASHES}][^\W\d_]+)+\b")
_WORD = re.compile(r"\b[^\W\d_]+\b")
_SEPARATOR = re.compile(rf"[\s{_DASHES}]+")
_DASH_SEPARATOR = re.compile(rf"\s*[{_DASHES}]+\s*")


def _compact(value: str) -> str:
    return "".join(_WORD.findall(value.casefold().replace("ð", "đ")))


def _fold(value: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFD", value.replace("đ", "d"))
        if unicodedata.category(char) != "Mn"
    )


def build_name_aliases(texts: Iterable[str]) -> dict[str, tuple[str, ...]]:
    """Learn spelling variants from capitalized, hyphenated names in the corpus."""
    # shortcut: only explicit hyphenated names are learned; different transliterations need a reviewed glossary.
    counts = Counter(
        match.group() for text in texts
        for match in _NAME.finditer(unicodedata.normalize("NFC", text))
        if match.group()[0].isupper()
    )
    groups = {}
    for name in sorted(counts, key=lambda name: (-counts[name], name.isupper(), name)):
        compact = _compact(name)
        groups.setdefault(_fold(compact), {}).setdefault(compact, name)
    return {key: tuple(names.values()) for key, names in groups.items()}


def normalize_query_names(question: str, aliases: dict[str, tuple[str, ...]]) -> str:
    """Restore known names without guessing between ambiguous or conflicting accents."""
    question = unicodedata.normalize("NFC", question)
    words = list(_WORD.finditer(question))
    max_length = max(map(len, aliases), default=0)
    output = []
    cursor = index = 0
    while index < len(words):
        if index and _DASH_SEPARATOR.fullmatch(question[words[index - 1].end():words[index].start()]):
            index += 1
            continue
        compact = ""
        best = None
        for end in range(index, len(words)):
            if end > index and not _SEPARATOR.fullmatch(question[words[end - 1].end():words[end].start()]):
                break
            compact += _compact(words[end].group())
            if len(compact) > max_length:
                break
            names = aliases.get(_fold(compact), ())
            if not names:
                continue
            if end + 1 < len(words) and _DASH_SEPARATOR.fullmatch(question[words[end].end():words[end + 1].start()]):
                continue
            exact = [name for name in names if _compact(name) == compact]
            compatible = exact if exact and compact != _fold(compact) else [
                name for name in names
                if all(given == actual or given == _fold(actual)
                       for given, actual in zip(compact, _compact(name)))
            ]
            original = question[words[index].start():words[end].end()]
            best = (end, compatible[0] if len(compatible) == 1 else original)
        if best is None:
            index += 1
            continue
        end, replacement = best
        output.extend((question[cursor:words[index].start()], replacement))
        cursor = words[end].end()
        index = end + 1
    return "".join(output) + question[cursor:]
