"""Human acceptance is tied to the exact question, answer, and source snapshot."""
import hashlib
import json

CRITERIA = ("correct", "supported", "complete", "scope_preserved")


def review_digest(result):
    payload = {key: result.get(key) for key in ("id", "question", "answer", "sources")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def apply_review(result, review):
    result = dict(result)
    if not isinstance(review, dict) or not str(review.get("reviewed_by") or "").strip():
        raise ValueError("Review needs a named human reviewer.")
    if review.get("review_digest") != review_digest(result):
        raise ValueError("Review does not match the current question, answer and sources.")
    if any(type(review.get(key)) is not bool for key in CRITERIA):
        raise ValueError("Every review criterion must be true or false.")
    result["human_review"] = review
    result["passed"] = bool(result.get("regression_passed")) and all(review[key] for key in CRITERIA)
    result["review_status"] = "human_reviewed"
    return result


def calibration_summary(results):
    reviewed = [row for row in results if row.get("human_review")]
    automatic_accepts = [row for row in reviewed if
                         (row.get('diagnostics') or {}).get('answer_validation') == 'citation_and_quote_checked']
    false_accept = sum(any(row['human_review'][criterion] is False for criterion in CRITERIA)
                       for row in automatic_accepts)
    return {"human_reviewed": len(reviewed), "accepted": sum(row.get("passed") is True for row in reviewed),
            "judge_false_accepts": false_accept,
            "judge_false_accepts_by_criterion": {criterion:sum(row['human_review'][criterion] is False for row in automatic_accepts)
                                                  for criterion in CRITERIA},
            "calibration_scope": "answer_grounding_verifier; RAGAS numeric metrics require separate human comparison",
            "pending": sum(row.get("passed") is None for row in results)}
