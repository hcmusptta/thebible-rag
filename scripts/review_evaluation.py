"""Export a blank human review form, or apply completed reviews without rerunning a model."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.evaluation_review import CRITERIA, apply_review, calibration_summary, review_digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path)
    parser.add_argument('--reviews', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    rows = json.loads(args.results.read_text(encoding='utf-8'))
    if isinstance(rows, dict):
        rows = rows.get('answer_results') or rows['results']
    if args.reviews:
        reviews = json.loads(args.reviews.read_text(encoding='utf-8'))
        ids = [r['id'] for r in reviews]
        if len(set(ids)) != len(ids) or set(ids) - {r['id'] for r in rows}:
            parser.error('Duplicate or unknown review IDs.')
        by_id = {r['id']: r for r in reviews}
        rows = [apply_review(row, by_id[row['id']]) if row['id'] in by_id else row for row in rows]
        payload = {'summary': calibration_summary(rows), 'results': rows}
        print(json.dumps(payload['summary']))
    else:
        payload = [{'id': row['id'], 'review_digest': review_digest(row), 'reviewed_by': '',
                    **{key: None for key in CRITERIA}, 'notes': ''} for row in rows]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()
