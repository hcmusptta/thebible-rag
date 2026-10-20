"""Edition data and structural passages; source facts stay separate from interpretation."""
import hashlib
import json
import re
from pathlib import Path


def load_profile(value=None):
    profile = json.loads(Path(value).read_text(encoding="utf-8")) if isinstance(value, (str, Path)) else dict(value or {})
    allowed = {"translation", "canon", "numbering", "expected_books", "expected_chapters", "complete", "reviewed_by", "passages", "annotations", "relations"}
    if set(profile) - allowed:
        raise ValueError(f"Trường hồ sơ bản dịch không hỗ trợ: {sorted(set(profile) - allowed)}")
    for key in ("translation", "canon", "numbering", "reviewed_by"):
        if key in profile and not isinstance(profile[key], str):
            raise ValueError(f"{key} phải là chuỗi.")
    if profile.get("complete") and not (profile.get("reviewed_by") and profile.get("expected_books") and profile.get("expected_chapters")):
        raise ValueError("Xác nhận trọn bộ cần người duyệt và danh mục sách/chương dự kiến của chính bản dịch.")
    for key in ("passages", "annotations", "relations", "expected_books"):
        if key in profile and not isinstance(profile[key], list):
            raise ValueError(f"{key} phải là danh sách.")
    if any(not isinstance(row, dict) for key in ("passages", "annotations", "relations") for row in profile.get(key, [])):
        raise ValueError("Đoạn/chú thích/quan hệ phải là bản ghi JSON.")
    if not isinstance(profile.get("expected_chapters", {}), dict) or any(not isinstance(chapters, list) or any(type(c) is not int or c < 1 for c in chapters)
            for chapters in profile.get("expected_chapters", {}).values()):
        raise ValueError("Danh mục chương phải ánh xạ mã sách sang danh sách số chương dương.")
    if any(not isinstance(book, str) for book in profile.get("expected_books", [])) or type(profile.get("complete", False)) is not bool:
        raise ValueError("Danh mục sách hoặc trạng thái trọn bộ không hợp lệ.")
    return profile


def coordinate(verse):
    return f"{verse['book_code']} {verse['chapter']}:{verse['verse_label']}"


def prepare_scripture(data, profile=None):
    profile = load_profile(profile)
    data.profile = {"translation": "", "canon": "unknown", "numbering": "source", "complete": False, **profile,
                    "observed_books": data.books, "metadata_origin": "reviewed" if profile.get("reviewed_by") else "source"}
    expected = profile.get("expected_books")
    if expected and set(expected) != set(data.books):
        data.issues.append({"severity": "error", "kind": "manifest_books", "expected": expected, "observed": data.books})
    for book, chapters in profile.get("expected_chapters", {}).items():
        observed = sorted({v["chapter"] for v in data.verses + data.quarantined if v["book_code"] == book})
        if sorted(chapters) != observed:
            data.issues.append({"severity": "error", "kind": "manifest_chapters", "book": book, "expected": chapters, "observed": observed})
    data.profile["complete"] = bool(profile.get("complete") and not data.issues and not data.quarantined)
    index = {coordinate(v): v for v in data.verses}
    for annotation in profile.get("annotations", []):
        if not profile.get("reviewed_by") or annotation.get("reference") not in index:
            raise ValueError("Chú thích cần người duyệt và tọa độ tồn tại trong bản dịch.")
        if set(annotation) - {"reference", "genre", "speaker", "audience", "entities", "source_locator"} or not annotation.get("source_locator"):
            raise ValueError("Chú thích cần vị trí nguồn; không tự suy ra ngữ nguyên hoặc đồng nhất nhân vật.")
        index[annotation["reference"]]["annotation"] = {**annotation, "origin": "reviewed"}
    for verse in data.verses:
        verse.setdefault("annotation", {"genre": None, "speaker": None, "audience": None, "entities": [], "origin": "unknown"})
    data.relations = []
    for supplementary in data.supplementary:
        if supplementary.get("type") == "cross_reference":
            anchor = next((coordinate(v) for v in data.verses if v["book_code"] == supplementary["book_code"] and v.get("heading_id") == supplementary.get("heading_id")
                           and v["chapter"] == supplementary["chapter"] and v["page"] >= supplementary["page"]), None)
            if anchor:
                data.relations.extend({"type": "cross_reference", "from": anchor, "to": target, "origin": "source", "source_locator": supplementary["provenance"]}
                                      for target in supplementary["references"])
    for relation in profile.get("relations", []):
        if relation.get("type") not in {"cross_reference", "parallel", "numbering_equivalence"} or relation.get("origin") not in {"source", "reviewed", "proposed"}:
            raise ValueError("Quan hệ Kinh Thánh chưa xác định loại/xuất xứ.")
        if relation.get("from") not in index or not relation.get("to") or not relation.get("source_locator"):
            raise ValueError("Quan hệ cần điểm nguồn tồn tại, điểm đích và bằng chứng vị trí nguồn.")
        if relation["origin"] == "reviewed" and not profile.get("reviewed_by"):
            raise ValueError("Quan hệ đã duyệt cần người duyệt.")
        data.relations.append(relation)
    groups = []
    used = set()
    for passage in profile.get("passages", []):
        refs = passage.get("references", [])
        if not refs or not profile.get("reviewed_by") or not passage.get("source_locator") or any(r not in index for r in refs):
            raise ValueError("Đoạn ý nghĩa đã duyệt cần câu tồn tại, người duyệt và vị trí nguồn.")
        if len({index[r]["book_code"] for r in refs}) != 1 or used.intersection(refs):
            raise ValueError("Đoạn phải thuộc một sách, không gán một câu vào hai đoạn chính.")
        ordered = list(index)
        positions = [ordered.index(r) for r in refs]
        if positions != list(range(positions[0], positions[-1] + 1)):
            raise ValueError("Các câu trong đoạn phải liên tiếp theo thứ tự bản nguồn.")
        groups.append(([index[r] for r in refs], "reviewed", passage.get("title", "")))
        used.update(refs)
    # shortcut: typography supplies structural boundaries, semantic passage boundaries require reviewer annotations.
    current, current_key = [], None
    for verse in data.verses:
        key = (verse["book_code"], verse.get("heading_id"), verse.get("paragraph_id"))
        if coordinate(verse) in used or (current and key != current_key):
            if current:
                groups.append((current, "source_structure", current[0].get("heading", "")))
            current = []
        if coordinate(verse) not in used:
            current.append(verse)
            current_key = key
    if current:
        groups.append((current, "source_structure", current[0].get("heading", "")))
    order = {coordinate(v): i for i, v in enumerate(data.verses)}
    groups.sort(key=lambda group: order[coordinate(group[0][0])])
    data.passages = []
    for verses, origin, title in groups:
        refs = [coordinate(v) for v in verses]
        passage_id = hashlib.sha256("|".join(refs).encode()).hexdigest()[:24]
        passage = {"id": passage_id, "references": refs, "origin": origin, "title": title,
                   "pericope_id": verses[0].get("heading_id", ""),
                   "text": "\n".join(v["text"] for v in verses), "page": min(v["page"] for v in verses),
                   "page_end": max(v["page_end"] for v in verses)}
        data.passages.append(passage)
        for verse in verses:
            verse["passage_id"] = passage_id
    return data


def label_bounds(label):
    match = re.fullmatch(r"(\d{1,3})([a-zđ+]*)(?:[-–](\d{1,3})([a-zđ+]*))?", str(label).lower())
    if not match:
        raise ValueError(f"Nhãn câu không hợp lệ: {label}")
    start, end = int(match[1]), int(match[3] or match[1])
    if start < 1 or end < start:
        raise ValueError(f"Nhãn câu đảo thứ tự: {label}")
    return (start, match[2]), (end, match[4] or match[2])
