import json
import hashlib
import logging
import os
import threading
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import fitz

from loaddocs.document_types import detect_document_type, document_metadata, iter_prose_sections, scripture_references
from loaddocs.document import get_processor
from loaddocs.embedding import get_default_embedder
from loaddocs.scripture_data import SCRIPTURE_PROCESSING_VERSION, parse_scripture_pdf
from loaddocs.scripture_workflow import load_profile, prepare_scripture, coordinate
from loaddocs.scripture_store import ScriptureStore
from loaddocs.source_documents import SUPPORTED_EXTENSIONS, open_source
from loaddocs.scripture_text import parse_scripture_text
from vectordb.vector_store import INGEST_SCHEMA_VERSION, VectorDatabase, default_db


logger = logging.getLogger(__name__)
ProgressCallback = Callable[[int, str], None]


class IngestCancelledError(Exception):
    """Raised between ingest steps so uncommitted chunks can be rolled back."""


def _check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise IngestCancelledError("Ingest cancelled.")


@dataclass(frozen=True)
class IngestResult:
    source: str
    document_id: str
    page_count: int
    chunk_count: int
    document_type: str = "generic"
    duplicate: bool = False
    replaced_chunk_count: int = 0
    warning: str | None = None
    location_unit: str = 'page'


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def get_max_document_size_mb() -> int:
    return max(1, int(os.getenv("MAX_DOCUMENT_SIZE_MB") or os.getenv("MAX_PDF_SIZE_MB", "100")))


def get_max_pdf_size_mb() -> int:
    return get_max_document_size_mb()


def _report_progress(
    progress_callback: ProgressCallback | None,
    percent: int,
    detail: str,
) -> None:
    if progress_callback is None:
        return
    try:
        progress_callback(max(0, min(int(percent), 100)), detail)
    except Exception:
        logger.exception("Unable to report PDF ingest progress")


def _validate_pdf(path: Path) -> None:
    if not path.is_file():
        raise ValueError("Không tìm thấy file PDF.")
    file_size = path.stat().st_size
    if file_size == 0:
        raise ValueError("File PDF đang rỗng.")
    max_size_mb = get_max_pdf_size_mb()
    if file_size > max_size_mb * 1024 * 1024:
        raise ValueError(f"File PDF vượt quá giới hạn {max_size_mb} MB.")
    with path.open("rb") as handle:
        if handle.read(5) != b"%PDF-":
            raise ValueError("File tải lên không có định dạng PDF hợp lệ.")


def process_uploaded_document(
    file_path: str,
    original_name: str,
    database: VectorDatabase = default_db,
    *,
    progress_callback: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
    scripture_chapters: set[tuple[str, int]] | None = None,
    export_directory: str | None = None,
    document_type: str | None = None,
    metadata: dict | None = None,
    scripture_profile: dict | str | None = None,
    reuse_database: VectorDatabase | None = None,
) -> IngestResult:
    _report_progress(progress_callback, 0, "Đang chờ bộ xử lý tài liệu...")
    _check_cancel(cancel_event)
    with database.access():
        database.recover_pending_ingest()
        return _process_uploaded_document(
            file_path,
            original_name,
            database,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
            scripture_chapters=scripture_chapters,
            export_directory=export_directory,
            document_type=document_type,
            metadata=metadata,
            scripture_profile=scripture_profile,
            reuse_database=reuse_database,
        )


def process_uploaded_pdf(pdf_file_path, original_name, database=default_db, **kwargs):
    """Compatibility entry point for existing PDF scripts."""
    return process_uploaded_document(pdf_file_path,original_name,database,**kwargs)


def _process_uploaded_document(
    file_path: str,
    original_name: str,
    database: VectorDatabase,
    *,
    progress_callback: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
    scripture_chapters: set[tuple[str, int]] | None = None,
    export_directory: str | None = None,
    document_type: str | None = None,
    metadata: dict | None = None,
    scripture_profile: dict | str | None = None,
    reuse_database: VectorDatabase | None = None,
) -> IngestResult:
    _check_cancel(cancel_event)
    path = Path(file_path)
    source_name = Path(original_name).name.strip()
    if not source_name:
        raise ValueError("Tên tài liệu không hợp lệ.")
    extension = Path(source_name).suffix.lower()
    _report_progress(progress_callback, 2, "Đang kiểm tra tài liệu...")
    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError('Chỉ hỗ trợ PDF, DOCX, DOC và TXT.')
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError('Không tìm thấy tài liệu hoặc file đang rỗng.')
    if path.stat().st_size > get_max_pdf_size_mb()*1024*1024:
        raise ValueError(f'Tài liệu vượt giới hạn {get_max_pdf_size_mb()} MB.')
    if extension == '.pdf':
        _validate_pdf(path)

    database.ensure_collection_identity()

    _report_progress(progress_callback, 5, "Đang kiểm tra tài liệu trùng lặp...")
    edition_id = _hash_file(path)
    document_id = edition_id
    if scripture_chapters:
        scope = ",".join(f"{b}:{c}" for b, c in sorted(scripture_chapters))
        document_id = hashlib.sha256(f"{document_id}:{scope}".encode()).hexdigest()
    # DOC conversion and XML extraction happen once, before any ingest transaction begins.
    with open_source(path, source_name, lambda: _check_cancel(cancel_event)) as opened:
        resolved_type = detect_document_type(opened, source_name, document_type)
        common = document_metadata(opened, source_name, resolved_type, metadata)
        text_document = opened if extension != '.pdf' else None
    location_unit = getattr(text_document,'location_unit','page')
    if text_document is not None:
        common.update(source_format=extension[1:],location_unit=location_unit,text_processing_version=1)
    if resolved_type == "catechism":
        common["prose_processing_version"] = 2
    profile = load_profile(scripture_profile)
    if resolved_type == "scripture":
        common["translation"] = profile.get("translation", common["translation"])
        common["scripture_profile_signature"] = hashlib.sha256(json.dumps(profile, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    metadata_signature = hashlib.sha256(json.dumps(common, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    common["metadata_signature"] = metadata_signature
    complete = database.is_document_complete(
        document_id,
        required_schema_version=INGEST_SCHEMA_VERSION,
        required_processing_version=SCRIPTURE_PROCESSING_VERSION,
        required_metadata={"metadata_signature": metadata_signature},
    )
    if complete and resolved_type == "scripture":
        try:
            snapshots = ScriptureStore(database).active(edition_id=edition_id)
            complete = any(meta.get("document_id") == document_id for _run, meta, _profile, _common in snapshots)
        except (ValueError, sqlite3.Error):
            complete = False
    if complete:
        _report_progress(progress_callback, 100, "Tài liệu đã có trong kho.")
        return IngestResult(
            source=database.get_document_source(document_id) or source_name,
            document_id=document_id,
            page_count=0,
            chunk_count=0,
            duplicate=True,
            document_type=resolved_type,
            location_unit=location_unit,
        )

    processor = get_processor()
    max_pages = max(1, int(os.getenv("MAX_PDF_PAGES", "3500")))
    all_chunks: list[str] = []
    all_metadatas: list[dict[str, object]] = []
    ingested_at = datetime.now(timezone.utc).isoformat()

    from contextlib import nullcontext
    with (nullcontext(text_document) if text_document is not None else fitz.open(path)) as document:
        page_count = len(document)
        if page_count == 0:
            raise ValueError("File PDF không có trang nào.")
        if text_document is None and page_count > max_pages:
            raise ValueError(f"PDF vượt quá giới hạn {max_pages} trang.")

        _report_progress(
            progress_callback,
            10,
            f"Bắt đầu đọc {page_count} {'trang' if location_unit == 'page' else 'dòng' if location_unit == 'line' else 'đoạn'}...",
        )
        if text_document is not None:
            structured = parse_scripture_text(document,source_name,check_cancel=lambda: _check_cancel(cancel_event),
                progress=lambda done,total: _report_progress(progress_callback,10+int(40*done/total),f'Đang chuẩn hóa vị trí {done}/{total}')) if resolved_type == 'scripture' else None
        else:
            structured = parse_scripture_pdf(
                document, source_name, document_type=resolved_type, check_cancel=lambda: _check_cancel(cancel_event),
                progress=lambda done, total: _report_progress(
                    progress_callback, 10 + int(40 * done / total), f"Đang chuẩn hóa trang {done}/{total}"),
            )
        if structured is not None:
            _check_cancel(cancel_event)
            _report_progress(progress_callback, 50, 'Đang kiểm tra cấu trúc sách–chương–câu...')
            structured = prepare_scripture(structured, profile)
            source_directory = database.db_path / "scripture_sources" / edition_id
            source_directory.mkdir(parents=True, exist_ok=True)
            original_pdf = source_directory / ('source'+extension)
            if not original_pdf.exists():
                temporary_pdf = source_directory / ('source.pending'+extension)
                shutil.copyfile(path, temporary_pdf)
                if _hash_file(temporary_pdf) != edition_id:
                    raise ValueError("Tài liệu đã thay đổi trong khi nạp; không công bố dữ liệu.")
                os.replace(temporary_pdf, original_pdf)
            for verse in structured.verses + structured.quarantined:
                verse["edition_id"] = edition_id
                verse["processing_version"] = SCRIPTURE_PROCESSING_VERSION
            export_path = Path(export_directory) if export_directory else database.db_path / "scripture_exports" / document_id
            _check_cancel(cancel_event)
            _report_progress(progress_callback, 54, 'Đang lưu báo cáo cấu trúc và các phần cần đối chiếu...')
            structured.export(export_path)
            structured = structured.select(scripture_chapters)
            if scripture_chapters:
                selected_refs = {coordinate(v) for v in structured.verses}
                scoped_profile = {k: v for k, v in profile.items() if k not in {"complete", "expected_books", "expected_chapters", "passages", "annotations", "relations"}}
                scoped_profile["passages"] = [p for p in profile.get("passages", []) if set(p.get("references", [])) <= selected_refs]
                scoped_profile["annotations"] = [a for a in profile.get("annotations", []) if a.get("reference") in selected_refs]
                scoped_profile["relations"] = [r for r in profile.get("relations", []) if r.get("from") in selected_refs]
                structured = prepare_scripture(structured, scoped_profile)
            if scripture_chapters:
                structured.export(export_path / "selected")
            try:
                _check_cancel(cancel_event)
                _report_progress(progress_callback, 58, 'Đang xác nhận dữ liệu trước khi tạo embedding...')
                structured.validate()
            except ValueError as error:
                report_path = export_path / "selected" / "report.json" if scripture_chapters else export_path / "report.json"
                missing_books = [i for i in structured.issues if i['kind'] == 'unknown_book_for_chapter']
                if text_document is not None and missing_books:
                    unit = 'dòng' if location_unit == 'line' else 'đoạn'
                    raise ValueError(
                        f"Đọc được văn bản nhưng {len(missing_books)} tiêu đề chương chưa có tên sách xác định "
                        f"(đầu tiên tại {unit} {missing_books[0]['page']}). "
                        "Bản chuyển từ PDF có thể đã mất bookmark hoặc nhãn câu. "
                        "Hãy dùng PDF gốc có bookmark, hoặc bổ sung tên sách và nhãn chương/câu cho Word/TXT. "
                        "Có thể tạo TXT có cấu trúc từ PDF bằng scripts/normalize_scripture.py với --text. "
                        f"Chưa tạo embedding hay thay dữ liệu đang dùng. Báo cáo: {report_path}"
                    ) from error
                raise ValueError(f"{error} Báo cáo: {report_path}") from error
            groups = {p["id"]: [] for p in structured.passages}
            for verse in structured.verses:
                groups[verse["passage_id"]].append(verse)
            for section_index, passage in enumerate(structured.passages):
                    group = groups[passage["id"]]
                    parent_content = "\n".join(v.get("display_text", v["text"]) for v in group)
                    parent_index = section_index
                    parent_id = f"{document_id}:passage:{passage['id']}"
                    for child_index, verse in enumerate(group):
                        book_code, chapter = verse["book_code"], verse["chapter"]
                        all_chunks.append(verse["text"])
                        all_metadatas.append({
                            "source": source_name, "edition_id": edition_id,
                            "document_id": document_id, "page": verse["page"], "page_end": verse["page_end"],
                            "parent_page": min(v["page"] for v in group),
                            "parent_page_end": max(v["page_end"] for v in group),
                            "section_index": section_index, "parent_id": parent_id,
                            "parent_index": parent_index, "parent_total": len(structured.passages),
                            "parent_content": parent_content, "child_index": child_index,
                            "child_total": len(group), "ingested_at": ingested_at,
                            "ingest_schema_version": INGEST_SCHEMA_VERSION,
                            "processing_version": SCRIPTURE_PROCESSING_VERSION,
                            "excluded_record_count": len(structured.quarantined),
                            "document_type": "scripture", "language": "vi", "book_code": book_code,
                            "book": verse["book"], "testament": verse["testament"], "chapter": chapter,
                            "verse_start": verse["verse"], "verse_end": verse.get("verse_end", verse["verse"]),
                            "verse_label": verse["verse_label"],
                            "passage_id": passage["id"], "passage_origin": passage["origin"],
                            "parent_chapter": group[0]["chapter"], "parent_chapter_end": group[-1]["chapter"],
                            "references": json.dumps(passage["references"]), "reference": f"{book_code} {group[0]['chapter']}:{group[0]['verse_label']}-{group[-1]['chapter']}:{group[-1]['verse_label']}",
                            "corpus_complete": structured.profile.get("complete", False),
                            "annotation": json.dumps(verse["annotation"], ensure_ascii=False),
                            "parent_verse_start": min(v["verse"] for v in group),
                            "parent_verse_end": max(v["verse"] for v in group),
                        })
        else:
            if scripture_chapters:
                raise ValueError("Không thể chọn chương cho tài liệu không có cấu trúc Kinh Thánh.")
            for section_index, section in enumerate(iter_prose_sections(document, resolved_type, lambda: _check_cancel(cancel_event))):
                for chunk in processor.chunk_parent_child_text(section["text"]):
                    all_chunks.append(str(chunk["child_content"]))
                    all_metadatas.append({
                        "source": source_name, "document_id": document_id, "edition_id": edition_id,
                        "page": section["page"], "page_end": section["page_end"],
                        "heading": section["heading"], "section_number": section["section_number"],
                        "topic": common["topic"] or section["heading"],
                        "scripture_references": scripture_references(chunk["parent_content"]) if resolved_type in {"commentary", "catechism"} else "",
                        "section_index": section_index, "parent_id": f"{document_id}:section:{section_index}:{chunk['parent_index']}",
                        "parent_index": chunk["parent_index"], "parent_total": chunk["parent_total"],
                        "parent_content": chunk["parent_content"], "child_index": chunk["child_index"],
                        "child_total": chunk["child_total"], "ingested_at": ingested_at,
                        "ingest_schema_version": INGEST_SCHEMA_VERSION,
                        "processing_version": SCRIPTURE_PROCESSING_VERSION,
                    })
                _report_progress(progress_callback, 10 + int(40 * section["page_end"] / page_count),
                                 f"Đang đọc vị trí {section['page_end']}/{page_count}")

    if not all_chunks:
        raise ValueError(
            "PDF không chứa text có thể đọc được. Tài liệu scan cần OCR trước khi nạp."
        )

    total_chunks = len(all_chunks)
    for metadata in all_metadatas:
        for key, value in common.items():
            metadata.setdefault(key, value)
        metadata["document_chunk_total"] = total_chunks
    ids = [
        (
            f"{document_id}:schema:{INGEST_SCHEMA_VERSION}:"
            f"page:{metadata['page']}:section:"
            f"{metadata['section_index']}:parent:"
            f"{metadata['parent_index']}:child:{metadata['child_index']}"
        )
        for metadata in all_metadatas
    ]

    _report_progress(
        progress_callback,
        60,
        f"Chuẩn bị tạo embedding cho {total_chunks} chunk...",
    )
    batch_size = max(1, int(os.getenv("EMBEDDING_BATCH_SIZE", "50")))
    last_embedding_percent = 59
    _check_cancel(cancel_event)
    vector_source = reuse_database or database
    if vector_source.embedding_model != database.embedding_model or vector_source.embedding_dimension != database.embedding_dimension:
        raise ValueError("Không thể tái sử dụng vector từ model hoặc kích thước khác.")
    reusable = vector_source.reusable_scripture_vectors(source_name, edition_id) if structured is not None else {}
    embedder = None
    run_id = database.begin_ingest(document_id, source_name, total_chunks)
    try:
        if structured is not None:
            ScriptureStore(database).write(run_id, document_id, edition_id, source_name, structured, common)
        for metadata in all_metadatas:
            metadata["ingest_run_id"] = run_id
        ids = [f"{chunk_id}:run:{run_id}" for chunk_id in ids]
        for start in range(0, total_chunks, batch_size):
            _check_cancel(cancel_event)
            end = min(start + batch_size, total_chunks)
            batch_chunks = all_chunks[start:end]
            batch_ids = ids[start:end]
            embedding_texts = [
                f"{meta['book']} {meta['chapter']}:{meta['verse_label']}\n{text}"
                if meta.get("document_type") == "scripture" else f"{meta['title']}\n{meta.get('heading', '')}\n{text}"
                for text, meta in zip(batch_chunks, all_metadatas[start:end])
            ]
            vectors = [reusable.get((meta.get("book_code"), meta.get("chapter"), meta.get("verse_label"), text))
                       for text, meta in zip(batch_chunks, all_metadatas[start:end])]
            missing = [i for i, vector in enumerate(vectors) if vector is None]
            if missing:
                embedder = embedder or get_default_embedder()
                generated = embedder.embed_documents([embedding_texts[i] for i in missing])
                if len(generated) != len(missing):
                    raise ValueError("Embedding trả sai số lượng bản ghi.")
                for index, vector in zip(missing, generated):
                    vectors[index] = vector
            _check_cancel(cancel_event)
            database.add_vectors(
                texts=batch_chunks,
                vectors=vectors,
                metadatas=all_metadatas[start:end],
                ids=batch_ids,
                rebuild_index=False,
            )
            embedding_percent = 60 + int(30 * end / total_chunks)
            if embedding_percent > last_embedding_percent:
                last_embedding_percent = embedding_percent
                _report_progress(
                    progress_callback,
                    embedding_percent,
                    f"Đã tạo embedding {end}/{total_chunks} chunk",
                )
        _check_cancel(cancel_event)
        _report_progress(progress_callback, 95, "Đang hoàn tất phiên bản tài liệu...")
        replaced_count = database.commit_ingest()
    except Exception:
        if database.abort_ingest():
            logger.info("Uncommitted ingest rolled back for %s", source_name)
        else:
            logger.warning("Ingest for %s may need commit recovery on restart", source_name)
        raise
    _report_progress(progress_callback, 100, "Hoàn tất nạp tài liệu.")
    return IngestResult(
        source=source_name,
        document_id=document_id,
        page_count=page_count,
        location_unit=location_unit,
        document_type=resolved_type,
        chunk_count=total_chunks,
        replaced_chunk_count=replaced_count,
        warning=(f"Đã tách riêng {len(structured.quarantined)} bản ghi cần đối chiếu, không dùng cho hỏi đáp. "
                 f"Có {len(structured.issues)} phát hiện cấu trúc; chi tiết: {export_path / 'report.json'}. "
                 f"Nội dung tách riêng: {export_path / 'quarantined.jsonl'}."
                 if structured is not None and structured.issues else None),
    )
