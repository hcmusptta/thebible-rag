# Workspace extension review

Disposition: **ship** for the frontend function bar and browser-local read-only history extension.

The four action controls forward to the latest native action and mirror disabled states; originals remain in React ownership and are hidden from the message area. Desktop history becomes a focus-protected mobile drawer. Saved text is rendered safely and discloses that model context and source attachments are not restored.

The single material finding, returning focus from the archive Back action to the native composer, is resolved and asserted on desktop/mobile. All eight required captures were valid. DESIGN.md and .impeccable/design.json were updated without changing the incumbent identity.

QA passed: callback forwarding, disabled state, both message roles, saving across reload, multiple conversations, safe text rendering, deletion suppression, drawer keyboard/focus, header/toolbar geometry, quota warnings and corrupt data preservation. Tested viewports: 320, 390 and 1440px. Test harness: tests/frontend_workspace.mjs (isolated profile and explicit DOM fixtures). Actual model responses and destructive backend callbacks are not test fixtures and were not invoked. Syntax and frontend whitespace checks passed; 28 backend source files remain unchanged from baseline.

Limits: transcripts are plain text on this browser origin, from conversations observed since this feature was installed. This is not account sync or backend conversation resume. Backend upload/query/settings/source-panel workflows remain uncertified.
