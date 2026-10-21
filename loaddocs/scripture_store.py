"""Canonical snapshots are durable before vector commit; only active vector runs are readable."""
import json
import re
import unicodedata
import sqlite3
from contextlib import closing
from pathlib import Path

from loaddocs.scripture_data import SCRIPTURE_PROCESSING_VERSION
from loaddocs.scripture_references import Reference
from loaddocs.scripture_workflow import coordinate, label_bounds
from loaddocs.bible_people import passage_person_metadata


class ScriptureStore:
    def __init__(self, database):
        self.database = database
        self.path = Path(database.db_path) / "scripture.sqlite3"

    def write(self, run_id, document_id, edition_id, source, data, common):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("CREATE TABLE IF NOT EXISTS snapshots (run TEXT PRIMARY KEY, document TEXT, edition TEXT, source TEXT, profile TEXT, common TEXT)")
            conn.execute("CREATE TABLE IF NOT EXISTS records (run TEXT, kind TEXT, ordinal INTEGER, book TEXT, chapter INTEGER, label TEXT, payload TEXT, PRIMARY KEY(run,kind,ordinal))")
            conn.execute("CREATE INDEX IF NOT EXISTS coordinates ON records(run,kind,book,chapter)")
            conn.execute("INSERT INTO snapshots VALUES (?,?,?,?,?,?)", (run_id, document_id, edition_id, source, json.dumps(data.profile, ensure_ascii=False), json.dumps(common, ensure_ascii=False)))
            for kind, rows in (("verse", data.verses), ("passage", data.passages), ("supplementary", data.supplementary), ("quarantined", data.quarantined), ("raw", data.raw_blocks), ("relation", data.relations), ("issue", data.issues)):
                conn.executemany("INSERT INTO records VALUES (?,?,?,?,?,?,?)", ((run_id, kind, i, row.get("book_code"), row.get("chapter"), row.get("verse_label"), json.dumps(row, ensure_ascii=False)) for i, row in enumerate(rows)))

    def active(self, edition_id=None, translation=None):
        rows = self.database.get_all_documents()
        runs = {}
        counts = {}
        legacy = False
        for row in rows:
            meta = row["metadata"]
            if meta.get("document_type") not in {"scripture", "bible"}:
                continue
            if edition_id and meta.get("edition_id") != edition_id or translation and meta.get("translation") != translation:
                continue
            if meta.get("processing_version") != SCRIPTURE_PROCESSING_VERSION or not meta.get("ingest_run_id"):
                legacy = True
                continue
            if not all(meta.get(key) for key in ("document_id", "edition_id", "document_chunk_total")):
                raise ValueError("Metadata phiên nạp Kinh Thánh chưa đủ; cần nạp lại PDF.")
            runs[meta["ingest_run_id"]] = meta
            counts[meta["ingest_run_id"]] = counts.get(meta["ingest_run_id"], 0) + 1
        if not runs and legacy:
            raise ValueError("Kho Kinh Thánh dùng cách nạp cũ; hãy nạp lại PDF theo workflow mới. Không đoán số câu từ chunk cũ.")
        if runs and not self.path.is_file():
            raise ValueError("Thiếu kho bản văn gốc đi kèm vector; cần nạp lại PDF.")
        result = []
        if not runs:
            return result
        with closing(sqlite3.connect(self.path)) as conn, conn:
            for run, meta in runs.items():
                if counts[run] != meta.get("document_chunk_total"):
                    raise ValueError("Vector chưa đủ bản ghi của phiên bản đang dùng; cần nạp lại trước khi tra cứu.")
                row = conn.execute("SELECT profile,common FROM snapshots WHERE run=? AND document=? AND edition=?", (run, meta["document_id"], meta["edition_id"])).fetchone()
                if row is None:
                    raise ValueError("Vector và bản văn không cùng phiên bản; chưa thể trả lời.")
                canonical_count = conn.execute("SELECT COUNT(*) FROM records WHERE run=? AND kind='verse'", (run,)).fetchone()[0]
                if canonical_count != counts[run]:
                    raise ValueError("Bản văn gốc thiếu bản ghi so với vector; cần nạp lại PDF.")
                # A run's last vector row is not metadata for every canonical verse.
                run_meta = {key:meta[key] for key in ('source','document_type','document_id','edition_id','processing_version',
                            'ingest_run_id','document_chunk_total','excluded_record_count','ingest_schema_version','ingested_at','language') if key in meta}
                result.append((run, run_meta, json.loads(row[0]), json.loads(row[1])))
        return result

    def verify_run(self, run, document_id, expected):
        if not self.path.is_file():
            raise ValueError("Chưa có snapshot bản văn gốc; không công bố vector.")
        with closing(sqlite3.connect(self.path)) as conn, conn:
            snapshot = conn.execute("SELECT document FROM snapshots WHERE run=?", (run,)).fetchone()
            count = conn.execute("SELECT COUNT(*) FROM records WHERE run=? AND kind='verse'", (run,)).fetchone()[0]
        if snapshot is None or snapshot[0] != document_id or count != expected:
            raise ValueError("Snapshot bản văn không đủ hoặc sai phiên bản; không công bố vector.")

    def records(self, run, kind="verse", book=None, chapter=None):
        sql, args = "SELECT payload FROM records WHERE run=? AND kind=?", [run, kind]
        if book:
            sql += " AND book=?"
            args.append(book)
        if chapter is not None:
            sql += " AND chapter=?"
            args.append(chapter)
        with closing(sqlite3.connect(self.path)) as conn, conn:
            return [json.loads(r[0]) for r in conn.execute(sql + " ORDER BY ordinal", args)]

    def lookup(self, references, edition_id=None, translation=None):
        results, unavailable = [], []
        for run, meta, profile, common in self.active(edition_id, translation):
            for ref in references:
                candidates = self.records(run, book=ref.book)
                verses = [v for v in candidates if ref.includes(v)]
                quarantined = [v for v in self.records(run, "quarantined", ref.book) if ref.includes(v)]
                missing = []
                if ref.start:
                    for chapter, label in ((ref.chapter, ref.start), (ref.end_chapter or ref.chapter, ref.end or ref.start)):
                        if re.search(r"[a-z+]", label) and not any(Reference(ref.book, chapter, label, None, label).includes(v) for v in verses):
                            missing.append(f"{chapter}:{label}")
                for chapter in range(ref.chapter, (ref.end_chapter or ref.chapter) + 1):
                    rows = [v for v in verses if v["chapter"] == chapter]
                    if not rows:
                        missing.append(f"chương {chapter}")
                        continue
                    if ref.start:
                        lower = label_bounds(ref.start)[0][0] if chapter == ref.chapter else 1
                        upper = label_bounds(ref.end or ref.start)[1][0] if chapter == (ref.end_chapter or ref.chapter) else max(v.get("verse_end", v["verse"]) for v in rows)
                        covered = {n for v in rows for n in range(v["verse"], v.get("verse_end", v["verse"]) + 1)}
                        missing.extend(str(n) for n in range(lower, upper + 1) if n not in covered)
                if missing or quarantined or not verses:
                    unavailable.append(f"{meta['source']} / {ref.display()}: thiếu hoặc cần duyệt {', '.join(missing) or 'câu nguồn'}")
                    continue
                first, last = verses[0], verses[-1]
                text = "\n".join(v.get("display_text", v["text"]) for v in verses)
                result_meta = {**meta, **common, "book_code": ref.book, "book": first["book"], "chapter": first["chapter"],
                               "testament": first.get("testament", ""), "heading": first.get("heading", ""),
                               "verse_label": first["verse_label"],
                               "passage_id": first["passage_id"], "last_passage_id": last["passage_id"],
                               "chapter_end": last["chapter"], "verse_start": first["verse"], "verse_end": last.get("verse_end", last["verse"]),
                               "reference": ref.display(), "references": json.dumps([coordinate(v) for v in verses]),
                               "page": min(v["page"] for v in verses), "page_end": max(v["page_end"] for v in verses),
                               "corpus_complete": profile.get("complete", False)}
                if not ref.start and any(issue.get("book") == ref.book and ref.chapter <= (issue.get("chapter") or 0) <= (ref.end_chapter or ref.chapter)
                                         and issue.get("kind") in {"verse_gap", "empty_verse_label", "verse_order"} for issue in self.records(run, "issue")):
                    result_meta["scope_warning"] = "Chương có phát hiện bất thường về số câu trong PDF; bản văn dưới đây là các câu hiện đủ điều kiện tra cứu, cần đối chiếu nguồn trước khi coi là đầy đủ."
                result_meta["source_relations"] = json.dumps([r for r in self.records(run, "relation") if r.get("from") in {coordinate(v) for v in verses} and r.get("origin") != "proposed"], ensure_ascii=False)
                result_meta["annotations"] = json.dumps({coordinate(v): v.get("annotation", {}) for v in verses}, ensure_ascii=False)
                result_meta.update(passage_person_metadata(verses))
                results.append({"id": f"exact:{run}:{ref.display()}", "content": text, "metadata": result_meta, "rerank_score": None, "hybrid_score": 0.0})
        if unavailable:
            raise ValueError("Kho chưa đủ đoạn yêu cầu. " + "; ".join(unavailable))
        return results

    def exhaustive(self, term, edition_id=None, translation=None, references=()):
        if not term.strip():
            raise ValueError('Truy vấn tất cả/đếm cần cụm từ đặt trong dấu ngoặc kép, ví dụ: đếm "đức mến".')
        results = []
        term = unicodedata.normalize("NFC", term).casefold()
        for run, meta, profile, common in self.active(edition_id, translation):
            segments, segment = [], []
            previous = None
            for verse in self.records(run):
                selected = not references or any(ref.includes(verse) for ref in references)
                connected = previous and verse["book_code"] == previous["book_code"] and verse.get("heading_id") == previous.get("heading_id") and (
                    verse["chapter"] == previous["chapter"] and previous["verse"] <= verse["verse"] <= previous.get("verse_end", previous["verse"]) + 1
                    or verse["chapter"] == previous["chapter"] + 1 and verse["verse"] == 1)
                if segment and (not selected or not connected):
                    segments.append(segment); segment = []
                if selected:
                    segment.append(verse)
                previous = verse if selected else None
            if segment:
                segments.append(segment)
            for segment in segments:
                starts, bodies, offset = [], [], 0
                for verse in segment:
                    body = re.sub(r"^\s*\(?\d{1,3}[a-zđ+]*(?:[-–]\d{1,3}[a-zđ+]*)?\)?\s*", "", verse["text"]).casefold()
                    starts.append(offset); bodies.append(body); offset += len(body) + 1
                text = " ".join(bodies)
                hits = {}
                from bisect import bisect_right
                for match in re.finditer(re.escape(term), text):
                    start = bisect_right(starts, match.start()) - 1
                    end = bisect_right(starts, match.end() - 1) - 1
                    hits[(start, end)] = hits.get((start, end), 0) + 1
                for (start, end), occurrences in hits.items():
                    verses = segment[start:end + 1]
                    refs = [coordinate(v) for v in verses]
                    result_meta = {**meta, **common, "reference": refs[0] if len(refs) == 1 else f"{refs[0]} – {refs[-1]}",
                                   "book_code": verses[0]["book_code"], "book": verses[0]["book"], "testament": verses[0].get("testament", ""),
                                   "chapter": verses[0]["chapter"], "chapter_end": verses[-1]["chapter"],
                                   "verse_label": verses[0]["verse_label"], "verse_start": verses[0]["verse"], "verse_end": verses[-1].get("verse_end",verses[-1]["verse"]),
                                   "heading": verses[0].get("heading", ""), "passage_id": verses[0]["passage_id"], "last_passage_id": verses[-1]["passage_id"],
                                   "references": json.dumps(refs), "literal_occurrences": occurrences,
                                   "page": min(v["page"] for v in verses), "page_end": max(v["page_end"] for v in verses),
                                   "corpus_complete": profile.get("complete", False)}
                    result_meta.update(passage_person_metadata(verses))
                    results.append({"id": f"all:{run}:{refs[0]}:{refs[-1]}", "content": "\n".join(v.get("display_text", v["text"]) for v in verses),
                                    "metadata": result_meta, "rerank_score": None, "hybrid_score": 0.0})
        return results

    def expand(self, item, cache):
        meta = item["metadata"]
        run = meta.get("ingest_run_id")
        if meta.get("processing_version") != SCRIPTURE_PROCESSING_VERSION or not run:
            raise ValueError("Kết quả Kinh Thánh dùng cách nạp cũ; cần nạp lại PDF.")
        if run not in cache:
            cache[run] = (self.records(run, "passage"), {coordinate(v): v for v in self.records(run)})
        passages, verses = cache[run]
        position = next((i for i, p in enumerate(passages) if p["id"] == meta.get("passage_id")), None)
        if position is None:
            raise ValueError("Không tìm thấy đoạn gốc cùng phiên bản vector.")
        selected = [passages[position]]
        center = selected[0]
        if center["origin"] != "reviewed":
            for index in (position - 1, position + 1):
                if not 0 <= index < len(passages):
                    continue
                neighbor = passages[index]
                if (neighbor["pericope_id"] == center["pericope_id"] and neighbor["references"][0].split()[0] == meta["book_code"]
                        and sum(len(p["text"]) for p in selected) + len(neighbor["text"]) <= 10000):
                    selected.append(neighbor)
        selected.sort(key=lambda p: list(verses).index(p["references"][0]))
        refs = [r for p in selected for r in p["references"]]
        rows = [verses[r] for r in refs]
        text = "\n".join(v.get("display_text", v["text"]) for v in rows)
        first, last = rows[0], rows[-1]
        metadata = {**meta, "chapter": first["chapter"], "chapter_end": last["chapter"],
                    "reference": f"{refs[0]} – {refs[-1]}", "references": json.dumps(refs),
                    "verse_start": first["verse"], "verse_end": last.get("verse_end", last["verse"]),
                    "page": min(v["page"] for v in rows), "page_end": max(v["page_end"] for v in rows)}
        metadata["source_relations"] = json.dumps([r for r in self.records(run, "relation") if r.get("from") in refs and r.get("origin") != "proposed"], ensure_ascii=False)
        metadata["annotations"] = json.dumps({coordinate(v): v.get("annotation", {}) for v in rows}, ensure_ascii=False)
        metadata.update(passage_person_metadata(rows))
        return {**item, "content": text, "metadata": metadata}
