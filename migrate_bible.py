"""Stage and verify the Bible workflow, then switch only the two storage settings."""
import argparse
import json
import os
from pathlib import Path
import re

from services.readdocs import process_uploaded_pdf
from loaddocs.scripture_references import parse_references
from loaddocs.scripture_store import ScriptureStore
from vectordb.vector_store import VectorDatabase, default_db


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--activate", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    destination = root / "bible_workflow_data"
    target = VectorDatabase("bible_workflow_v4", db_path=str(destination))
    if target.db_path == default_db.db_path and target.collection_name == default_db.collection_name:
        print("Workflow v4 is already active; verify/reimport through the upload flow.")
        return
    previous = {"CHROMA_DB_PATH": str(default_db.db_path), "CHROMA_COLLECTION": default_db.collection_name}
    (destination / "previous_storage.json").write_text(json.dumps(previous, indent=2), encoding="utf-8")
    last = -1
    def progress(percent, message):
        nonlocal last
        if percent != last:
            print(f"{percent}% {message}", flush=True)
            last = percent
    try:
        with default_db.access():
            result = process_uploaded_pdf(str(args.pdf), args.pdf.name, target, scripture_profile=args.profile,
                                          progress_callback=progress, reuse_database=default_db)
        store = ScriptureStore(target)
        active = store.active()
        if len(active) != 1:
            raise ValueError("Kho chuyển đổi phải có đúng một phiên bản PDF được xác nhận.")
        verses = store.records(active[0][0])
        if len(verses) != target.count():
            raise ValueError("Số bản ghi gốc không trùng với số vector.")
        first = verses[0]
        checked = store.lookup(parse_references(f"{first['book_code']} {first['chapter']}:{first['verse_label']}"))
        if not checked:
            raise ValueError("Không tra được câu đã nạp từ kho gốc.")
        print(json.dumps({"chunks": target.count(), "canonical_verses": len(verses), "warning": result.warning}, ensure_ascii=False), flush=True)
        if args.activate:
            # Keep providers, credentials and every unrelated setting byte-for-byte.
            path = root / ".env"
            content = path.read_text(encoding="utf-8") if path.exists() else ""
            settings = {"CHROMA_DB_PATH": "bible_workflow_data", "CHROMA_COLLECTION": "bible_workflow_v4"}
            for key, value in settings.items():
                pattern = rf"(?m)^{key}\s*=.*$"
                content = re.sub(pattern, f"{key}={value}", content) if re.search(pattern, content) else content.rstrip() + f"\n{key}={value}\n"
            temp = root / ".env.bible-new"
            temp.write_text(content, encoding="utf-8")
            os.replace(temp, path)
            print("Activated workflow v4. Restart Chainlit to load the verified collection.", flush=True)
    finally:
        target.client.close()
        default_db.client.close()


if __name__ == "__main__":
    main()
