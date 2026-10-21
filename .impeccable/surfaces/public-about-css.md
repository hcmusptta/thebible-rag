---
version: 1
slug: "public-about-css"
primary_target: "public/about.css"
related_targets: ["public/about.js","public/library-logo.svg","public/workspace.js","public/document-library.js","public/source-view.js","public/document-upload.js","chainlit.md"]
---

# Chat and source reading workspace

Visitor mode: Operate, with long-form Read content. Preserve native Chainlit controls. Frontend-only scope.

## Direction contract

THESIS: A clear Vietnamese Scripture study workspace, with quiet reading space and immediately recognizable citations.

OWN-WORLD: The fourth grounded direction draws from lectionary rubrics: restrained berry ink, a pale tinted shell, white composer and dialog surfaces, fixed sans-serif UI sizing and modest serif welcome headings. No illustrations, paper effects or decorative motion.

STORY: The reader identifies the study space, asks a question, reads an answer and opens native source attachments. Upload, document, evaluation and reset controls remain native.

FIRST VIEWPORT: Compact book identity in the native header. Keep Chainlit's centered welcome composer, then its bottom composer during conversation. Native chat measure is approximately 48rem; welcome measure 760px. Short introduction, source chips, inherited side panels and dialogs. Mobile remains one column; the information label becomes an accessible icon.

FORM: Grounded list position four, lectionary rubric typography translated into a normal chat application. seed:b3019e1cfda8fb9e. Code-led under the user's authorized redesign. Signature: inline source references respond on hover and keyboard focus, then use the existing source panel. No backend changes. Keep native chat controls while adding the user-requested local read-only history viewer.

FINISH: Finish review, scoped verdict and extracted DESIGN.md plus .impeccable/design.json. No shipping raster assets; the book mark is authored SVG geometry.

No user visual reference was supplied. Reading priority was inferred from the conversation; the optional preference question received no answer. Preserve native callbacks and backend code.

## User follow-up: workspace tools and history

Move the four document actions out of message content into a persistent top function bar. Keep original callbacks and confirmation behavior. Add a 256px history sidebar (224px at compact desktop), a mobile drawer at 900px, and a text-only archive reader. Browser snapshots persist on this origin; they do not restore model context or source attachments. The header and tool bar stay visible while the reader scrolls. At 640px action buttons wrap into two columns. Continue the same palette and type identity.
## User follow-up: document-library dialog

Kho tài liệu opens a compact dialog with the existing grouped catalog and native summary/delete actions, plus the vector total observed in the startup greeting. The vector number is explicitly captioned as a session-start snapshot, not a live total. Replace the old technical greeting in chat with a friendly study introduction. Keep original React nodes/callbacks; hide recognized management responses from chat and exclude them from saved transcripts, including delayed responses after dismissal. Dialog uses the existing palette, 680px maximum width, scrollable body, keyboard containment, Escape/backdrop dismissal and toolbar focus restoration. No backend source changes.

## User follow-up: history visibility and explicit source opening

History closes with its close control and reopens from the toolbar on desktop and mobile, without deleting saved entries. Desktop starts visible and does not persist the visibility preference. Preserve the mobile drawer and focus handling. Source panes stay closed after answers; only trusted mouse or keyboard activation of a native Nguồn N citation opens the selected source. New user messages clear the previous source selection. Preserve native source rendering, closing and other side elements; revalidate DOM selectors on Chainlit upgrades. Verified using actual native Chainlit source widgets with isolated test content; production retrieval is outside this UI certification.

## User follow-up: upload dialog and transient cancellation notice

Hide the composer paperclip. Thêm tài liệu opens a 640px maximum-width dialog using the existing library dialog shell. Show a font/Vietnamese-text check note and permit native type/translation settings before beginning file selection. Keep native picker limits and validation, browse/drop callbacks, processing, results and errors, presenting these inside the upload window and excluding management output from local history. Before processing, cancel via controls, Escape or backdrop; a pending native request must acknowledge cancellation before the window closes and a short chat-ready bubble appears. Bubble lasts 3.5 seconds then fades over 0.5 seconds; reduced motion removes the transition. Processing and active transfer protect against dismissal. Test against an isolated actual Chainlit upload fixture without production ingestion. No backend changes.

## User follow-up: supplied cross-and-book symbol

Replace the berry square with white outlined book using the user's second reference: a black cross rising from a layered open book, with a gray oval below. Use a single shared vector asset for header, welcome logo and native assistant avatar; the header has no colored tile. The manually authored SVG follows the supplied visual reference, with transparent negative space. Invert its rendered image in dark mode for contrast. Keep existing layout and mobile header visibility. Verify asset loading, old-tile removal and desktop light/dark plus mobile rendering. No backend changes.
