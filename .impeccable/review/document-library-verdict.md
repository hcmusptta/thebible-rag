# Document-library dialog review

Disposition: **ship**, scoped to the scored late-response isolation fix after the full modal review.

Six valid captures covered loading, grouped catalog, mobile, dark, empty and busy states. The dialog holds the existing catalog and summary/delete controls, while the technical greeting/count are replaced in chat. Counts explicitly retain their session-start qualifier.

Resolved: dismissed request snapshots classify late empty/busy responses, hide them without reopening and exclude them from saved history. Standalone busy messages remain visible. Existing native summary/delete callbacks and their confirmation/input flows are unchanged; modal closes before forwarding.

Full isolated-browser QA passed, including count0/41849 fixtures, revised intro, catalog rendering, focus/Tab/Escape, summary/delete proxies, late responses and all prior history checks. Syntax and whitespace checks passed, 28 backend file hashes remain unchanged. DESIGN.md and .impeccable/design.json updated and validated.

Limits: QA uses explicitly named frontend fixtures, not real deletion/model responses. Actual backend workflows remain uncertified. Vector statistics are the existing startup snapshot; no live-statistics endpoint was added.
