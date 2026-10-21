# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users and purpose

Vietnamese users ask questions about Catholic Scripture, catechism and commentary, then
read the answer and inspect its cited source passages. The user's current request is a
frontend redesign of the existing application.

## Operating context

The existing Chainlit application accepts PDF, Word and TXT documents. It presents chat,
source passages, document settings, document management and evaluation actions. Its
current entry point is `main.py`; visual customizations are loaded from `public/` by
`.chainlit/config.toml`.

## Capabilities and constraints

- This redesign must change frontend files only. Preserve Python, retrieval, LLM behavior,
  document processing, database contents and existing action callbacks.
- Preserve normal Chainlit controls, source inspection, upload, settings and keyboard use.
- Use the existing frontend extension points; do not replace Chainlit or add a frontend framework.
- Keep the interface usable on desktop and narrow mobile viewports.

## Open decisions

The user has not specified a visual reference or a primary device. Reading long answers
and consulting citations is the working priority inferred from the preceding conversation;
it is a session assumption, not a permanent user preference.
