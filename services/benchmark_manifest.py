"""Reproducible benchmark identity; never include API keys or the whole environment."""
import hashlib
import json
import os
import platform
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

from services.runtime_config import get_public_runtime_config


def validate_testset_sources(rows, catalog):
    """Reject known stale identities; absent provenance remains explicitly unverified."""
    mismatches, unverified = [], []
    for row in rows:
        metadata = row.get('metadata') or {}
        source = metadata.get('source')
        current = catalog.get(source) if source else None
        if source and current is None:
            mismatches.append({'id': row.get('id'), 'field': 'source'})
            continue
        if not current or not metadata.get('document_id'):
            unverified.append(row.get('id'))
        for field in ('document_id', 'ingest_run_id', 'edition_id', 'translation'):
            expected = row.get(field) or metadata.get(field)
            if expected and current and current.get(field) != expected:
                mismatches.append({'id': row.get('id'), 'field': field})
    return {'mismatches': mismatches, 'unverified_rows': unverified,
            'semantic_source_review': 'pending_human_review'}


def run_manifest(testset_path, database):
    root = Path(__file__).resolve().parents[1]
    code = hashlib.sha256()
    for folder in ('services', 'loaddocs', 'vectordb', 'scripts'):
        for path in sorted((root/folder).glob('*.py')):
            code.update(path.relative_to(root).as_posix().encode())
            code.update(path.read_bytes())
    for name in ('main.py', 'evaluate_bible_answers.py', 'requirements.txt'):
        code.update((root/name).read_bytes())
    metadata = getattr(getattr(database, 'collection', None), 'metadata', None)
    metadata = metadata if isinstance(metadata, dict) else {}
    catalog = database.get_source_catalog() if callable(getattr(database, 'get_source_catalog', None)) else {}
    catalog = catalog if isinstance(catalog, dict) else {}
    return {
        'started_at': datetime.now(timezone.utc).isoformat(),
        'code_sha256': code.hexdigest(),
        'testset': {'filename': Path(testset_path).name, 'sha256': hashlib.sha256(Path(testset_path).read_bytes()).hexdigest()},
        'runtime': {'python': platform.python_version(), 'platform': platform.platform(),
                    'packages': {name: version(name) for name in ('chainlit','chromadb','ragas','langchain-ollama','sentence-transformers')}},
        'configuration': get_public_runtime_config(),
        'answer_provider': os.getenv('LLM_PROVIDERS','ollama'),
        'judge_provider': 'ollama',
        'ollama_reasoning': os.getenv('OLLAMA_REASONING','false'),
        'collection_identity': {key: metadata.get(key) for key in
                                ('embedding_model','embedding_dimension','ingest_schema_version','index_revision','state')},
        'source_catalog': catalog,
        'source_catalog_sha256': hashlib.sha256(json.dumps(catalog,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
    }


def save_benchmark_report(report, output):
    output = Path(output)
    payload = {key:value for key,value in report.items() if key != 'details'}
    details = report.get('details')
    payload['metric_rows'] = json.loads(details.to_json(orient='records',force_ascii=False)) if details is not None else []
    output.parent.mkdir(parents=True,exist_ok=True)
    temporary = output.with_suffix(output.suffix+'.tmp')
    temporary.write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    temporary.replace(output)
