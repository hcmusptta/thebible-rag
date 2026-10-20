"""Export/audit a Scripture PDF; optionally ingest validated chapters into a NEW collection."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fitz
from loaddocs.scripture_data import SCRIPTURE_PROCESSING_VERSION, parse_scripture_pdf
from loaddocs.scripture_workflow import prepare_scripture, load_profile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--source-name")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--chapter", action="append", default=[], metavar="BOOK:NUMBER")
    parser.add_argument("--collection", help="New collection only; omitted means export/audit only")
    parser.add_argument("--profile", type=Path, help="Reviewed edition profile JSON")
    parser.add_argument("--text", action="store_true", help="Also export re-importable TXT with explicit book/chapter/verse labels")
    args = parser.parse_args()
    chapters = set()
    for reference in args.chapter:
        book, number = reference.upper().split(":")
        chapters.add((book, int(number)))
    source = args.source_name or args.pdf.name
    with fitz.open(args.pdf) as pdf:
        data = parse_scripture_pdf(pdf, source)
    if data is None:
        parser.error("PDF does not identify Scripture books.")
    profile = load_profile(args.profile)
    data = prepare_scripture(data, profile)
    edition_id = hashlib.sha256(args.pdf.read_bytes()).hexdigest()
    for verse in data.verses:
        verse.update(edition_id=edition_id, processing_version=SCRIPTURE_PROCESSING_VERSION)
    data.export(args.output)
    print(f"Exported {len(data.verses)} verse records in {len(data.books)} books; {len(data.issues)} issues.", flush=True)
    selected = data.select(chapters)
    if chapters:
        selected.export(args.output / "selected")
    if args.text:
        from loaddocs.scripture_text import export_scripture_text
        text_path = args.output / 'Kinh-Thanh-Chuan-Hoa.txt'
        count = export_scripture_text(selected, text_path)
        (args.output / 'text_source.json').write_text(json.dumps({
            'source': source, 'source_sha256': edition_id,
            'text_sha256': hashlib.sha256(text_path.read_bytes()).hexdigest(),
            'verse_records': count, 'excluded_records': len(selected.quarantined),
            'complete': False, 'scope': sorted(f'{b}:{c}' for b,c in chapters),
        }, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f"TXT: {text_path}; {count} validated verses. See quarantined.jsonl for excluded source records.", flush=True)
    if args.collection:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
        if args.collection == os.getenv("CHROMA_COLLECTION", "rag_vietnamese_embedding_v2_v1"):
            parser.error("Use a new collection; the active collection is protected.")
        selected.validate()
        from services.readdocs import process_uploaded_pdf
        from vectordb.vector_store import VectorDatabase
        database = VectorDatabase(args.collection)
        try:
            result = process_uploaded_pdf(str(args.pdf), source, database,
                                          scripture_chapters=chapters or None,
                                          scripture_profile=profile,
                                          export_directory=str(args.output))
            print(f"Collection {args.collection}: {result.chunk_count} chunks; duplicate={result.duplicate}", flush=True)
        finally:
            database.client.close()
    else:
        selected.validate()
    print("Validation passed for the selected scope.")


if __name__ == "__main__":
    try:
        main()
    except ValueError as error:
        print(str(error), file=sys.stderr)
        sys.exit(2)
