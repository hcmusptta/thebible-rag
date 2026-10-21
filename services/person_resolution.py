"""Resolve ambiguous names before search; never use retrieval popularity as identity."""
import re
import logging

from loaddocs.bible_people import mentioned_groups, name_pattern, person_catalog, person_matches_testament
from loaddocs.scripture import normalize_scripture_text
from loaddocs.scripture_references import parse_references


class AmbiguousPersonError(ValueError):
    pass


def has_person_detail(text, groups):
    text = normalize_scripture_text(text)
    for group in groups:
        for alias in group.get('aliases', [group['name']]):
            text = re.sub(name_pattern(alias), ' ', text)
    generic = {"ai", "la", "ong", "ba", "thanh", "nguoi", "nao", "trong", "cuu", "tan", "uoc", "kinh", "co", "biet", "ve", "hay", "cho", "toi", "ban", "noi", "gi", "vai", "tro", "cua", "the", "va", "nhan", "vat"}
    return bool(set(text.split()) - generic)


def _explicit_ids(text, people):
    if re.search(r'\b(?:khong phai|chang phai|co phai|phai chang|ngoai tru)\b', text):
        return []
    return [person['id'] for person in people if any(
        re.search(name_pattern(alias), text) for alias in (person['name'], *person.get('aliases', []))
    )]


def _clarification(report):
    unresolved = next(group for group in report["groups"] if not group["selected_ids"])
    by_id = {person["id"]: person for group in person_catalog()["groups"] for person in group["people"]}
    report["options"] = unresolved["candidate_ids"]
    choices = [f"{index}. {by_id[key]['name']} ({'Cựu Ước' if by_id[key]['testament'] == 'old' else 'Tân Ước'}; {', '.join(by_id[key]['references'])})"
               for index, key in enumerate(report["options"], 1)]
    report["status"] = "clarify"
    report["message"] = (f"Tên {unresolved['name']} có thể chỉ nhiều nhân vật. Bạn muốn nói đến ai?\n\n"
                         + "\n".join(choices)
                         + "\n\nBạn có thể chọn số, nêu quan hệ/sự kiện hoặc chỉ rõ đoạn Kinh Thánh. "
                         "Nếu là người khác chưa có trong danh sách, hãy cho biết thêm ngữ cảnh.")
    return report


def resolve_people(question, history=None, *, select=None):
    report = {"status": "none", "input_question": question, "original_question": question, "question": question,
              "groups": [], "selected_ids": []}
    previous = list(history or [])
    if previous and previous[-1].get("role") == "user" and previous[-1].get("content") == question:
        previous.pop()
    last = previous[-1] if previous and previous[-1].get("role") == "assistant" else {}
    prior = last.get("person_resolution") or {}
    groups = mentioned_groups(question)
    normalized = normalize_scripture_text(question)
    choice = re.fullmatch(r"(?:(?:toi )?chon )?(?:(?:nguoi|nhan vat) (?:thu )?|so )?([1-9]\d*)", normalized)
    references = parse_references(question) if prior.get('status') == 'clarify' else []
    reference_reply = bool(references and not re.search(
        r'\b(?:doc|trich|giai thich|ai|gi|nao|sao|bao nhieu|so sanh|liet ke|lam)\b', normalized
    ))
    pending_reply = not groups and bool(re.match(
        r"^(?:nguoi\b|ong\b|ba\b|vi\b|nhan vat\b|y toi\b|toi muon\b|cuu uoc\b|tan uoc\b)", normalized
    ))
    pending_reply = pending_reply or bool(
        groups and any(group['name'] in {entry['name'] for entry in prior.get('groups', [])} for group in groups)
        and not re.search(r'\b(?:ai|gi|nao|sao|bao nhieu|khi|o dau|lam|co phai|giai thich|so sanh|liet ke|ke ten)\b', normalized)
    )
    if prior.get("status") == "clarify" and prior.get('groups') and (choice or pending_reply or reference_reply):
        report["original_question"] = prior["original_question"]
        report["question"] = prior["original_question"]
        report["groups"] = [{**group, "selected_ids": list(group["selected_ids"])} for group in prior["groups"]]
        if choice and 1 <= int(choice[1]) <= len(prior.get("options", [])):
            key = prior["options"][int(choice[1]) - 1]
            next(group for group in report["groups"] if key in group["candidate_ids"])["selected_ids"] = [key]
        elif choice:
            return _clarification(report)
        else:
            people = {person['id']: person for group in person_catalog()['groups'] for person in group['people']}
            scopes = {scope for phrase, scope in (("cuu uoc", "old"), ("tan uoc", "new")) if phrase in normalized}
            if reference_reply:
                report['question'] += f' (Theo {question})'
                report['reference_question'] = report['question']
            for group in report['groups']:
                if not group['selected_ids']:
                    if len(scopes) == 1:
                        scoped = [key for key in group['candidate_ids'] if person_matches_testament(people[key], scopes)]
                        if scoped:
                            group['candidate_ids'] = scoped
                    group['selected_ids'] = _explicit_ids(normalized, [people[key] for key in group['candidate_ids']])
                    if reference_reply:
                        anchored = [key for key in group['candidate_ids'] if any(
                            ref.start and (not ref.end or ref.end == ref.start) and ref.end_chapter in (None, ref.chapter) and
                            parse_references(anchor)[0].includes({'book_code': ref.book, 'chapter': ref.chapter, 'verse_label': ref.start})
                            for ref in references for anchor in people[key]['references']
                        )]
                        group['selected_ids'] = anchored if len(anchored) == 1 else []
    elif not groups and prior.get("status") == "resolved" and re.search(
        r"\b(?:ong ay|ba ay|nguoi ay|ong do|ba do|nhan vat nay|ong|ba)\b", normalized
    ):
        report["groups"] = [{**group, "selected_ids": list(group["selected_ids"])} for group in prior["groups"]]
        if len(prior.get("selected_ids", [])) != 1:
            for group in report["groups"]:
                group["selected_ids"] = []
    elif groups:
        for group in groups:
            candidates = list(group["people"])
            explicit = _explicit_ids(normalized, candidates)
            scopes = {scope for phrase, scope in (("cuu uoc", "old"), ("tan uoc", "new")) if phrase in normalized}
            if not explicit and len(scopes) == 1 and len(groups) == 1:
                candidates = [person for person in candidates if person_matches_testament(person, scopes)]
            if not candidates:
                # Catalog coverage is incomplete; an unknown person must never be replaced with a familiar one.
                return {**report, "status": "clarify", "options": [],
                        "message": f"Danh mục hiện chưa đủ để xác định {group['name']} trong phạm vi bạn nêu. Bạn có thể cho biết sách, chương/câu hoặc một quan hệ, sự kiện liên quan không?"}
            report["groups"].append({"name": group["name"], "candidate_ids": [p["id"] for p in candidates],
                                     "all_ids": [p['id'] for p in group['people']], "selected_ids": explicit})
    else:
        return report

    multiple_people = bool(re.search(
        r'\b(?:(?:nhung|cac|tat ca|bao nhieu|may) (?:nhan vat|nguoi|ai).*ten|trung ten)\b', normalized
    )) or any(re.search(r'\b(?:cac|nhung)\s+' + name_pattern(alias), normalized)
              for group in groups for alias in group['aliases'])
    if multiple_people:
        report["status"] = "multiple"
        return report
    unresolved = [group for group in report["groups"] if not group["selected_ids"]]
    detailed = has_person_detail(question, groups)
    if not detailed and prior.get('status') == 'resolved':
        for group in unresolved:
            known = next((entry['selected_ids'] for entry in prior.get('groups', []) if entry['name'] == group['name']), [])
            if known and all(key in group['candidate_ids'] for key in known):
                group['selected_ids'] = known
        unresolved = [group for group in report['groups'] if not group['selected_ids']]
    if unresolved and select is not None and detailed:
        people = {person["id"]: person for group in person_catalog()["groups"] for person in group["people"]}
        try:
            selections = select(question, [{"name": group["name"], "people": [people[key] for key in group["candidate_ids"]]} for group in unresolved])
        except Exception:
            logging.getLogger(__name__).exception('Person resolver unavailable; requesting clarification')
            selections = {}
        if isinstance(selections, dict):
            for group in unresolved:
                ids = selections.get(group["name"])
                if isinstance(ids, list) and ids and all(isinstance(key, str) and key in group["candidate_ids"] for key in ids) and len(set(ids)) == len(ids):
                    group["selected_ids"] = ids
    if any(not group["selected_ids"] for group in report["groups"]):
        return _clarification(report)
    report["status"] = "resolved"
    report["selected_ids"] = list(dict.fromkeys(key for group in report["groups"] for key in group["selected_ids"]))
    people = {person["id"]: person for group in person_catalog()["groups"] for person in group["people"]}
    report['identity_hint'] = "Nhân vật được xác định: " + "; ".join(people[key]["name"] for key in report["selected_ids"])
    report["question"] += " (" + report['identity_hint'] + ")"
    return report
