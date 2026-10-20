"""Read-only corpus audit; export source evidence and a human review queue, never approve it automatically."""
import argparse
import hashlib
import json
import os
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path

from dotenv import load_dotenv


def audit(db_path,collection):
    chroma=db_path/'chroma.sqlite3'
    canonical=db_path/'scripture.sqlite3'
    with closing(sqlite3.connect(chroma.resolve().as_uri()+'?mode=ro',uri=True)) as c:
        sql="""SELECT src.string_value,doc.string_value,run.string_value,COUNT(*)
        FROM embeddings e JOIN segments s ON s.id=e.segment_id JOIN collections col ON col.id=s.collection
        JOIN embedding_metadata src ON src.id=e.id AND src.key='source'
        JOIN embedding_metadata doc ON doc.id=e.id AND doc.key='document_id'
        JOIN embedding_metadata run ON run.id=e.id AND run.key='ingest_run_id'
        JOIN embedding_metadata kind ON kind.id=e.id AND kind.key='document_type'
        WHERE col.name=? AND kind.string_value IN ('scripture','bible') GROUP BY 1,2,3"""
        groups=list(c.execute(sql,(collection,)))
    sources=[]
    if groups:
        with closing(sqlite3.connect(canonical.resolve().as_uri()+'?mode=ro',uri=True)) as c:
            for source,document,run,count in groups:
                row=c.execute('SELECT edition,profile,common FROM snapshots WHERE run=? AND document=?',(run,document)).fetchone()
                if row is None: raise ValueError('An active vector run has no canonical snapshot.')
                edition,profile,common=row; profile=json.loads(profile)
                issues=[json.loads(r[0]) for r in c.execute("SELECT payload FROM records WHERE run=? AND kind='issue' ORDER BY ordinal",(run,))]
                quarantine=[json.loads(r[0]) for r in c.execute("SELECT payload FROM records WHERE run=? AND kind='quarantined' ORDER BY ordinal",(run,))]
                verses=c.execute("SELECT COUNT(*) FROM records WHERE run=? AND kind='verse'",(run,)).fetchone()[0]
                originals=list((db_path/'scripture_sources'/edition).glob('source.*'))
                original=next((p for p in originals if '.pending' not in p.name),None)
                digest=None
                if original:
                    hasher=hashlib.sha256()
                    with original.open('rb') as handle:
                        for block in iter(lambda:handle.read(1024*1024),b''): hasher.update(block)
                    digest=hasher.hexdigest()
                sources.append({'source':source,'document_id':document,'ingest_run_id':run,
                    'profile':profile,'vector_records':count,'canonical_records':verses,
                    'snapshot_counts_match':count==verses,'source_sha256_matches':digest==edition,
                    'original_path':str(original) if original else None,
                    'issue_counts':dict(Counter(i['kind'] for i in issues)),
                    'quarantined_records':len(quarantine),'quarantined':quarantine,
                    'review_queue':[{'id':i,'issue':issue,'decision':'pending','reviewed_by':'','notes':''} for i,issue in enumerate(issues)],
                    'provenance_review':{'translation':profile.get('translation',''),'canon':profile.get('canon','unknown'),
                                         'source_url':'','reviewed_by':'','status':'pending'}})
    return {'created_at':datetime.now(timezone.utc).isoformat(),'collection':collection,
            'human_acceptance':'pending','sources':sources}


def main():
    root=Path(__file__).resolve().parents[1]
    load_dotenv(root/'.env')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    configured=Path(os.getenv('CHROMA_DB_PATH','chroma_db'))
    db_path=configured if configured.is_absolute() else root/configured
    report=audit(db_path,os.getenv('CHROMA_COLLECTION','rag_vietnamese_embedding_v2_v1'))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'sources':len(report['sources']),'quarantined':sum(s['quarantined_records'] for s in report['sources']),
                      'issues':sum(len(s['review_queue']) for s in report['sources']),'human_acceptance':'pending'}))


if __name__=='__main__': main()
