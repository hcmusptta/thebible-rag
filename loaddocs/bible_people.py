"""Person identities are independent of spelling, testament and source edition."""
import json
import re
from functools import lru_cache
from pathlib import Path

from loaddocs.scripture import BOOKS, normalize_scripture_text
from loaddocs.scripture_references import parse_references


@lru_cache(maxsize=1)
def person_catalog():
    catalog = json.loads(Path(__file__).with_name("bible_people.json").read_text(encoding="utf-8"))
    ids = set()
    for group in catalog["groups"]:
        if not group["aliases"] or len(group["people"]) < 2:
            raise ValueError("A person-name group needs aliases and distinct identities.")
        for person in group["people"]:
            if person["id"] in ids or person["testament"] not in {"old", "new"}:
                raise ValueError("Invalid or duplicate biblical person identity.")
            ids.add(person["id"])
            if not person["description"] or not person["references"] or any(
                len(parse_references(ref)) != 1 for ref in person["references"]
            ):
                raise ValueError("A person identity needs source references and distinguishing context.")
    return catalog


def name_pattern(name):
    return r"\b" + r"\s*".join(map(re.escape, normalize_scripture_text(name).replace(' ', ''))) + r"\b"


def person_matches_testament(person, scopes):
    books = {book.code: book.testament for book in BOOKS}
    return person['testament'] in scopes or any(
        books[parse_references(ref)[0].book] in scopes for ref in person['references']
    )


def mentioned_groups(text):
    text = normalize_scripture_text(text)
    return [group for group in person_catalog()["groups"] if any(
        re.search(name_pattern(alias), text) and not all(
            re.search(r"(?:\bsach|\bthu|\btin mung(?: theo thanh)?)\s*$", text[:match.start()])
            for match in re.finditer(name_pattern(alias), text)
        ) for alias in group["aliases"]
    )]


def person_metadata(text, metadata):
    """Record candidates separately; only explicit reference anchors assign identities."""
    candidates, identified = {}, []
    groups = mentioned_groups(text)
    if not groups:
        return {}
    # shortcut: anchor coverage is deliberately sparse; add reviewed references rather than guessing from a book.
    book, chapter = metadata.get("book_code"), metadata.get("chapter")
    label = str(metadata.get("verse_label") or metadata.get("verse_start") or "")
    for group in groups:
        # A New Testament passage can mention an Old Testament person.
        people = group['people']
        candidates[group["name"]] = [person["id"] for person in people]
        for person in people:
            if book and chapter and label and any(parse_references(ref)[0].includes(
                {"book_code": book, "chapter": chapter, "verse_label": label}
            ) for ref in person["references"]):
                identified.append(person["id"])
    return {"person_catalog_version": person_catalog()["version"],
            "person_candidates": json.dumps(candidates, ensure_ascii=False),
            "person_ids": json.dumps(identified)}


def passage_person_metadata(verses):
    candidates, identified = {}, []
    for verse in verses:
        tags = person_metadata(verse['text'], verse)
        for name, ids in json.loads(tags.get('person_candidates', '{}')).items():
            candidates.setdefault(name, set()).update(ids)
        identified.extend(json.loads(tags.get('person_ids', '[]')))
    return {'person_catalog_version': person_catalog()['version'],
            'person_candidates': json.dumps({name: sorted(ids) for name, ids in candidates.items()}, ensure_ascii=False),
            'person_ids': json.dumps(list(dict.fromkeys(identified)))}
