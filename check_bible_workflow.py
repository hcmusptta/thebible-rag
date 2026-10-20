"""Offline functional checks against the selected real Bible corpus; never call an LLM."""
import json
from unittest.mock import patch
from loaddocs.scripture_store import ScriptureStore
from services import ans


def main():
    store = ScriptureStore(ans.default_db)
    active = store.active()
    assert active, "No active Bible corpus"
    references = ("St 1:1", "Tv 23:1", "Mt 13:1-3", "1Cr 13:1-3", "Is 9:5", "Ga3:16", "Đn 3:24+")
    with patch.object(ans, "get_llm_service", side_effect=AssertionError("Exact reading must not call LLM")), patch.object(ans, "_collect_hybrid_candidates", side_effect=AssertionError("Exact references must not use semantic search")):
        for reference in references:
            answer, rows = ans.answer_question("Đọc " + reference)
            assert rows, (reference, answer)
            assert "[Nguồn 1]" in answer, reference
        rows = ans.retrieve_ranked_contexts("St 1:1 và Ga 3:16", top_k=1)
        assert len(rows) >= 2, "Top-k dropped requested references"
        answer, rows = ans.answer_question("Đọc Ga 99:1")
        assert not rows and "thiếu" in answer, answer
        for run, _meta, _profile, _common in active:
            held = store.records(run, "quarantined")
            if held:
                verse = held[0]
                reference = f"{verse['book_code']} {verse['chapter']}:{verse['verse_label']}"
                answer, rows = ans.answer_question("Đọc " + reference)
                assert not rows, "Quarantined text was released"
        answer, rows = ans.answer_question('Đếm "thương xót"', top_k=1)
        assert len(rows) > 1, "Exhaustive query was truncated to top-k"
    print(json.dumps({"collection": ans.default_db.collection_name, "canonical_verses": sum(len(store.records(r[0])) for r in active),
                      "exact_cases": len(references), "multi_reference": "passed", "missing_and_quarantine": "passed",
                      "exhaustive": answer}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
