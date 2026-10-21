---
name: Thư viện Kinh Thánh
description: A Vietnamese Scripture reading workspace with restrained berry ink and clear source references.
colors:
  primary: "hsl(344 39% 37%)"
  primary-foreground: "hsl(0 0% 100%)"
  background: "hsl(330 20% 97%)"
  foreground: "hsl(326 15% 18%)"
  card: "hsl(0 0% 100%)"
  secondary: "hsl(334 23% 93%)"
  secondary-foreground: "hsl(326 20% 26%)"
  muted: "hsl(330 15% 93%)"
  muted-foreground: "hsl(326 9% 40%)"
  accent: "hsl(334 23% 93%)"
  accent-foreground: "hsl(344 39% 31%)"
  border: "hsl(330 13% 85%)"
  input: "hsl(330 13% 80%)"
  sidebar-background: "hsl(330 20% 96%)"
  sidebar-accent: "hsl(334 23% 91%)"
  dark-primary: "hsl(343 52% 76%)"
  dark-primary-foreground: "hsl(326 25% 14%)"
  dark-background: "hsl(326 12% 10%)"
  dark-foreground: "hsl(330 15% 91%)"
  dark-card: "hsl(326 12% 14%)"
  dark-secondary: "hsl(326 13% 20%)"
  dark-muted: "hsl(326 12% 20%)"
  dark-muted-foreground: "hsl(330 12% 70%)"
  dark-accent: "hsl(326 16% 23%)"
  dark-accent-foreground: "hsl(343 52% 82%)"
  dark-border: "hsl(326 10% 29%)"
  dark-input: "hsl(326 10% 35%)"
  dark-sidebar-background: "hsl(326 12% 12%)"
  logo-ink: "#000"
  logo-shadow: "#e6e6e6"
typography:
  headline:
    fontFamily: 'Georgia, "Times New Roman", serif'
    fontSize: "34px"
    fontWeight: 500
    lineHeight: 1.25
    letterSpacing: "-.025em"
  title:
    fontFamily: '"Segoe UI", system-ui, sans-serif'
    fontSize: "22px"
    fontWeight: 650
    lineHeight: 1.4
    letterSpacing: "-.02em"
  subtitle:
    fontFamily: '"Segoe UI", system-ui, sans-serif'
    fontSize: "18px"
    fontWeight: 650
    lineHeight: 1.5
  body:
    fontFamily: '"Segoe UI", system-ui, sans-serif'
    fontSize: "16px"
    lineHeight: 1.8
  source-label:
    fontFamily: '"Segoe UI", system-ui, sans-serif'
    fontSize: "12px"
    fontWeight: 650
    lineHeight: 1.6
  runtime-value:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
    fontSize: "13px"
    lineHeight: 1.65
rounded:
  source: "6px"
  control: "9px"
  mark: "12px"
  surface: "16px"
spacing:
  compact: "4px"
  small: "8px"
  control: "12px"
  section: "16px"
  reading-inset: "20px"
  surface: "24px"
components:
  info-button:
    backgroundColor: "{colors.card}"
    textColor: "{colors.foreground}"
    rounded: "{rounded.control}"
    padding: "0 12px"
    height: "40px"
  info-button-hover:
    backgroundColor: "{colors.accent}"
  source-link:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.accent-foreground}"
    typography: "{typography.source-label}"
    rounded: "{rounded.source}"
    padding: "2px 8px"
  source-link-hover:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.primary-foreground}"
  composer:
    backgroundColor: "{colors.card}"
    rounded: "{rounded.surface}"
    padding: "16px 18px 12px"
  runtime-dialog:
    backgroundColor: "{colors.card}"
    textColor: "{colors.foreground}"
    rounded: "{rounded.surface}"
    width: "min(520px, 100%)"
  brand-mark:
    width: "42px"
    height: "42px"
---

# Design System: Thư viện Kinh Thánh

## Overview

**Creative North Star: "Lectionary rubrics"**

Restrained berry ink gives this Vietnamese Scripture workspace its identity. Pale tinted surroundings and distinct reading surfaces support long answers and source inspection. The serif welcome heading is modest; the working interface uses a sans-serif face.

This is an extracted record of the implemented frontend, not a replacement component library. `public/about.css` owns the visual overrides, including its final cascade; `public/about.js` owns the inserted identity, welcome copy and runtime information dialog, and dynamically imports `public/workspace.js` for the function bar and local history reader. The workspace imports `public/document-library.js` for the document-library modal and friendly initial chat introduction, `public/document-upload.js` for upload presentation, and `public/source-view.js` for explicit citation-open behavior. `.chainlit/config.toml` loads the entry files and shared transparent SVG logo, chooses Vietnamese and defaults to light mode. Chainlit continues to own the live chat panels, native action callbacks, upload picker/validation, source rendering, settings and menus. `chainlit.md` and `.chainlit/translations/vi.json` provide interface text.

**Key Characteristics:**

- Berry accents on a pale light shell, with a separate charcoal and rose dark theme.
- Segoe UI reading and controls, with Georgia reserved for top-level reading headings.
- Recognizable inline source references that open Chainlit's existing source surface on explicit activation.
- Quiet borders, restrained corners and a shared modal shadow.
- A separate function bar and local, read-only transcript history around the live chat.
- A dedicated document-library modal that presents existing catalog content and actions.
- A compact upload window that keeps file preparation, progress and results together.

## Colors

The palette uses one berry accent family and neutrals tinted toward the same hue. Frontmatter records complete CSS colors from the HSL channel values in the stylesheet. The sidecar's eight-step OKLCH ramps are generated swatch previews, not additional shipped theme tokens.

### Primary

The primary ink colors source links, section labels, primary actions and focus rings. Its foreground supplies contrasting action and hovered source-link content. The shared logo instead uses its own black artwork and gray oval fills on a transparent background. Logo images are inverted in dark mode for legibility; these asset fills do not replace the interface palette.

### Neutral

Background is the outer reading shell; card supplies the header, composer and custom dialogs. Foreground carries reading text, while muted foreground supports introductory copy, placeholders and metadata labels. Border and input are separate stroke strengths. Secondary and accent are shared native-control surfaces in light mode but have distinct assignments in dark mode.

Sidebar background and sidebar accent are recorded separately because they differ from the main shell in light mode. The remaining sidebar foreground, primary, border and ring variables alias the equivalent main theme values. Popover equals card, card foreground equals foreground, and ring equals primary in both modes. Dark secondary foreground also equals dark foreground.

**The Paired Theme Rule.** Apply each semantic interface color through the existing CSS variable so switching the root dark class changes the entire pairing together. The shared SVG logo retains its asset fills and uses image inversion in dark mode.

The function bar's destructive action uses a component-scoped hover text override: `hsl(0 65% 40%)` in light mode and `hsl(0 80% 75%)` in dark mode. These literal values are not additional general-purpose palette primitives.

## Typography

**Heading Font:** Georgia with Times New Roman and serif fallbacks.  
**Body Font:** Segoe UI with system-ui and sans-serif fallbacks.  
**Runtime Value Font:** The existing UI monospace stack.

The small serif heading introduces a reading task without becoming a display treatment for the application. Brand labels, navigation, actions and answer text remain sans-serif.

### Hierarchy

- **Headline:** Welcome and message level-one headings use the frontmatter headline role. At the mobile breakpoint, the size becomes 28px.
- **Title / subtitle:** Message level-two and level-three headings use the corresponding frontmatter roles.
- **Body:** Answer text uses the frontmatter body role; on mobile it becomes 15px with line height 1.75.
- **Introduction:** Supporting welcome copy uses 15px and line height 1.8, becoming 14px on mobile.
- **Initial chat introduction:** The friendly greeting inside the live conversation uses 16px and line height 1.8, becoming 15px at 640px. It is separate from the centered welcome heading and copy.
- **Brand:** The header name uses 17px at weight 650 with slight negative tracking, becoming 14px on mobile. Its 12px supporting line is hidden on mobile.
- **Source label:** The compact frontmatter source-label role identifies inline citations without uppercasing them.
- **Runtime values:** Monospace metadata uses tabular numerals; adjacent labels retain the body family.
- **History:** The read-only transcript title uses sans-serif 24px type at weight 650 and line height 1.45, becoming 21px at 640px. Transcript text uses 16px and line height 1.8, becoming 15px on mobile; user messages use weight 550. Toolbar actions and history titles use 13px type at weight 550, with 11px tabular timestamps.

**The Reading Heading Rule.** Keep the serif treatment on the implemented welcome and message level-one headings; preserve sans-serif type for the working controls and body.

## Layout

The custom outer workspace places local history beside the live Chainlit chat. The main area uses three grid rows: `auto auto minmax(0, 1fr)`. The native header occupies row one, the function bar row two, and live chat panels row three. Header and bar remain outside the scrolling reading area. The custom welcome container is capped at 760px with 40px 24px padding; its introductory copy is capped at 590px and centered. Chainlit supplies the live conversation measure and moves the composer from the welcome surface to the conversation bottom. The direction contract's approximate 48rem conversation measure is framework behavior, not a new custom width token.

The desktop header is 84px tall with 16px 28px padding. At a maximum width of 640px, it becomes 72px with 12px padding; gaps tighten, the header book mark and brand subline disappear, and the information action becomes an accessible SVG icon in a 40px control. The welcome padding becomes 24px 16px and the composer padding becomes 12px.

The desktop history sidebar is 256px wide and viewport-height, with 24px 16px 16px padding and its own scrolling list. Between 901px and 1100px it narrows to 224px. At 900px and below it becomes a modal drawer, width `min(300px, 88vw)`, opened by the function bar's history action. The drawer uses a dim backdrop, makes the main area inert while open, contains keyboard focus, closes on Escape or backdrop click and returns focus to its opener.

On desktop, history starts visible and can be hidden with the sidebar close action or the toolbar's history toggle; the toggle reopens it without changing the recorded widths. Its collapsed class removes the sidebar from layout, allowing the main area to use the space. The history toggle and 40px square sidebar close action are visible at every width. Desktop visibility is held only in memory, with no persisted preference. Crossing the 900px breakpoint closes an open mobile drawer and resynchronizes the toggle with the appropriate desktop or drawer state.

The function bar has a 64px minimum height, 8px 28px padding and a heading/actions grid on desktop. Its horizontal padding becomes 16px between 901px and 1100px, and its heading label disappears there. At 900px and below the bar stacks its heading above a four-column action grid. At 640px and below the actions become a two-column grid with left-aligned 12px labels.

The local transcript reader occupies the same third grid row and first column as live chat, above it at z-index 1. The live panel remains mounted with opacity zero, pointer events disabled and the inert attribute while a saved transcript is visible. Returning to the current conversation restores that panel. The reader scrolls independently and uses a 736px reading measure through responsive padding; mobile padding becomes 20px 16px.

The runtime backdrop scrolls when necessary. Its dialog is width-limited rather than fixed-width, with a sticky header and two-column label/value rows. Mobile rows stack, the backdrop uses 16px 8px padding, and the dialog maximum height is based on the dynamic viewport minus 32px. Long metadata wraps anywhere; code blocks scroll horizontally. Native dialogs and side panels retain Chainlit's own responsive structure.

The document-library backdrop centers a flex-column dialog with width `min(680px, 100%)`, maximum height `calc(100dvh - 48px)` and 24px outer padding. Its header remains above a body that scrolls independently. At 640px and below, outer padding becomes 12px, maximum height becomes `calc(100dvh - 24px)`, header padding becomes 12px 16px and body padding becomes 16px.

The upload window reuses this modal shell and scrolling body with a narrower `min(640px, 100%)` width. Its drop area lays out guidance and file selection side by side with 20px padding; at 640px and below it stacks them and uses 16px padding. A separate footer keeps the cancellation or terminal close action beneath the body.

## Elevation & Depth

Most surfaces use color and a thin border instead of shadows. The final information-button rule removes its earlier shadow. The runtime, document-library and upload dialogs share a diffuse shadow and translucent dark backdrop. These are final cascade values, not the superseded declarations earlier in the stylesheet.

- **Runtime, document-library and upload dialogs:** `0 20px 70px rgb(0 0 0 / 22%)`.
- **Runtime, document-library and upload backdrops:** `hsl(326 20% 8% / 58%)`.
- **Upload cancellation/timeout status bubble:** `0 8px 24px rgb(0 0 0 / 16%)`.

The history drawer shares the runtime backdrop color but adds no shadow. Its fixed sidebar and backdrop use z-index 70 and 60 respectively; the transcript overlay uses z-index 1 within the reading grid. The document-library backdrop uses z-index 1300. No drawer, transcript or document-library entrance animation is added.

The composer border transitions over 160ms with ease and turns primary on focus within. Reduced-motion preference removes composer and welcome transitions and animations. Native Chainlit motion is not overridden globally.

The upload status bubble stays visible for 3.5 seconds, then fades through an opacity transition of 500ms ease-out and is removed. Reduced-motion preference removes that transition. The bubble uses z-index 1400 above the modal, stays 28px from the viewport bottom and is centered with a maximum width of the viewport minus 32px; it does not receive pointer events.

## Shapes

Source labels use the smallest recorded radius; header actions use the control radius. Code blocks and upload drop areas use the mark radius; composers and dialogs use the surface radius. The shared logo is an unframed cross rising from a layered open book above a gray oval, with a transparent surround. These are observed component assignments, not a requirement to round every future surface identically.

The workspace extension uses 8px corners on toolbar actions, the new-chat action and history rows. Its destructive toolbar action has a left separator and square left corners on larger layouts; at 640px and below the separator is removed and all four corners use 8px. These component assignments do not replace the existing radius primitives.

Borders separate the header, sidebar, composer and runtime metadata sections. Source quotes retain a one-pixel primary left rule with the reading inset. The runtime dialog deliberately has no border.

## Components

### Information button

A quiet outlined header action uses the frontmatter info-button tokens, a one-pixel border and no shadow. Hover uses accent. Its focus outline is two pixels in ring color with a two-pixel offset. Mobile substitutes an inline SVG information icon for the visible label while keeping the accessible name. The existing dialog can close by its close control, Escape or backdrop click and returns focus to this action.

### Composer / input

The frontmatter composer surface has a one-pixel input-color border. Focus within changes that stroke to primary; the input caret also uses primary. The textarea uses 16px type, line height 1.6 and a minimum height of 46px. Composer buttons have minimum 40px dimensions. The composer paperclip and its loading indicator are hidden through CSS; document uploads start from the toolbar window. This changes presentation only, without changing server upload configuration. Settings, submit state and callbacks remain native.

### Inline source links

Source references use the frontmatter source-link tokens, compact mixed-case labels and no underline. Hover swaps to primary with its paired foreground. Keyboard focus retains the shared two-pixel ring outline with a three-pixel offset. Ordinary prose links remain underlined and increase underline thickness on hover.

The source-view guard applies only to native panes titled **Nguồn N**, matching the numbered citation labels. Such panes remain closed when Chainlit tries to open new side elements automatically: the guard temporarily suppresses the pane and its associated overlay or resize handle, then invokes the existing native close control. A trusted mouse or keyboard activation of a matching message citation permits its native source pane to open. The native source renderer, pane geometry and manual close control remain unchanged. A new user message resets this explicit selection; closing the pane also clears the opened selection. Other native side elements are outside this guard. No source panel is reconstructed from a saved transcript.

### Brand mark

The header uses the same `public/library-logo.svg` asset as the configured welcome logo and avatar, rather than a duplicate inline icon. The manually authored vector follows the supplied reference: a black cross rises from an open layered book above a gray oval, on a transparent background without the former berry tile. Its header container stays 42px square and does not shrink; the image fills that box with contained proportions. The header mark remains hidden at 640px and below. All images whose source ends in `/library-logo.svg` use `filter: invert(1)` in dark mode, preserving legibility without changing the shared asset.

### Runtime information dialog

The dialog uses the frontmatter runtime-dialog tokens and the elevation described above. Its sticky header, compact sections and label/value rows serve configuration inspection. The close action is 40px square. Loading and error content remain within the same surface.

### Function bar

The separate top navigation forwards **Thêm tài liệu**, **Kho tài liệu**, **Đánh giá RAG** and **Xóa toàn bộ** to the latest rendered native action with the matching visible label. Original message actions are visually hidden; their callbacks remain native. The forwarded controls mirror the native disabled state and remain disabled while the corresponding action is unavailable. Icons are copied from those native controls. The buttons use 40px minimum height, 8px 12px padding, 8px corners and 13px type at weight 550. Enabled hover uses accent; disabled buttons use opacity .45. The destructive hover color is the scoped theme pair described in Colors.

**Kho tài liệu** first opens its custom modal and then forwards the native catalog action. Repeated activation while that modal is already open does not request another catalog.

**Thêm tài liệu** first opens the preparation window without starting the native file request. Choosing or dropping a document then activates the original upload action and its native picker.

The history toggle is present on desktop as well as mobile. Its accessible label, title and `aria-expanded` state track whether the desktop sidebar or mobile drawer is open. It retains the existing 40px minimum control height, 8px corners, primary text and accent hover treatment.

### Document-library modal

The modal uses the existing card/foreground pairing, surface radius and shared dialog elevation. Its header uses 16px 20px padding, a bottom border and a 20px sans-serif title at weight 650 with line height 1.4. A 40px square close button uses an inline SVG and 8px corners. The body uses 20px 24px 24px padding, contained overscroll and a zero minimum height so lengthy catalogs scroll without moving the header. Catalog text uses 14px and line height 1.8; code labels wrap instead of forcing horizontal overflow.

Opening makes the application root inert and focuses the close button. Enabled modal buttons contain Tab focus. Close, Escape and a click directly on the backdrop dismiss the modal and return focus to the toolbar's library action. Loading, slow-response, empty and recognized storage-busy results are displayed in the same body.

Catalog content comes from the existing native message rendering, copied into the modal without duplicate IDs. Its existing **Tóm tắt:** and **Xóa:** controls are represented by wrapped, left-aligned actions with 40px minimum height, 8px 12px padding and 8px corners. Summary uses primary text; delete uses muted foreground. Enabled hover uses accent and disabled controls use opacity .45. Selecting either closes the modal, focuses the live chat input and forwards the still-connected, enabled native callback, so existing confirmation and follow-up input continue in chat.

The vector count is read from the existing startup greeting, not a live-statistics backend endpoint. When available, a 15px, weight-650 tabular count is followed by the exact qualifier **Ghi nhận khi bắt đầu phiên trò chuyện.** in muted 12px text. Missing startup data produces an unavailable-statistics message. The catalog is requested on each new opening; that does not refresh this session-start count. Native Vietnamese greeting and response recognition are current integration limits, not new design-system primitives.

### Upload window

The upload window uses the existing dialog header, 40px SVG close action, body, surface radius and theme pairing. Its preparation note asks readers to check fonts and Vietnamese diacritics by copying a passage into a text editor, and to use UTF-8 or UTF-16 with BOM for TXT. The note uses muted 14px text with line height 1.8. A settings action opens the native document-type and translation settings before file selection; the preparation window is temporarily hidden and resumes with focus restored after settings closes.

The drop area uses an input-colored dashed border and the existing 12px radius. Its primary browse button uses 8px 16px padding, 8px corners, 14px type at weight 550 and a brightness .93 hover treatment. The window delegates file selection and dropped files to the installed native picker. Native accepted types, size and file-count limits remain authoritative; no new backend upload contract is introduced.

Upload progress, terminal results, duplicate-file notices and ingestion errors are copied from the existing rendered responses into the window. Native type, size and file-count validation messages appear there as readable Vietnamese alerts, while native message content retains its renderer. Recognized upload-management responses are hidden from chat and excluded from new local transcripts. This integration depends on the installed native picker DOM and current response shapes; revalidate those selectors on Chainlit upgrades.

Before starting the request, closing, Escape, backdrop dismissal or **Hủy** cancels preparation immediately. Once the native request is active, cancellation delegates to its existing **Hủy** action, displays **Đang hủy…** and waits for the backend acknowledgement before dismissing or claiming the chat is ready. Uploading and processing disable dismissal and cancellation controls, preserving native processing safety. Terminal results enable **Đóng**. The dialog contains keyboard focus, makes the application root inert while visible and restores toolbar focus when dismissed.

Acknowledged cancellation or request timeout produces a short status bubble; opening the upload window clears any prior bubble. The bubble uses the foreground/background pair in reverse, 12px corners, 12px 20px padding and 14px type with line height 1.7. Its timing and reduced-motion behavior are recorded in Elevation & Depth. This acknowledgement boundary is part of the upload behavior, not a new rollback or backend cancellation mechanism.

### Initial chat greeting

The recognized startup message is presented as a friendly Scripture-study introduction directing readers to ask a specific question, inspect sources and use the toolbar. Its original technical greeting and vector count remain hidden in chat; the count is presented only with its qualifier inside the library modal. Recognized catalog output, and recognized empty or storage-busy results received for the library request, are hidden from the conversation and presented in the modal instead.

### Local history and reader

The sidebar starts with a primary-filled new-chat action and then lists saved titles and Vietnamese date/time labels. Rows use sidebar accent on hover and selection; a separate 36px by 40px remove action deletes the local snapshot. A selected item uses `aria-current`. New chat forwards to the native new-chat button, retaining its existing confirmation behavior.

The sidebar close action is available on desktop and mobile. Desktop closing returns focus to the history toggle; Escape also closes desktop history when focus is inside the sidebar. Mobile closing keeps the existing modal drawer focus and backdrop behavior. Hiding the sidebar does not remove saved entries or dismiss an already selected read-only transcript.

History is plain-text snapshots of user and assistant messages observed in this browser, stored under `scripture-chat-history-v1` in localStorage. It is read-only: it does not restore model context, source attachments or an active backend conversation. Markdown and source links are displayed as saved text, rather than restored interactive source controls. A notice above each transcript states this limitation. Storage read/write failures appear in the sidebar and function bar, and the current chat remains usable. This browser-origin persistence is not account-synchronized history. The reader's return action restores the still-mounted live chat; deleting a snapshot does not delete backend documents or the live conversation.

The recognized startup greeting, document-management results and upload-management responses described above are excluded from newly saved transcripts; history remains focused on the user's questions and assistant answers.

### Native controls and navigation

The native sidebar receives the shared palette and a right border. Header buttons receive a minimum height and corner override, and native dialogs receive the surface radius. Native control geometry, action callbacks, menus, validation states and dialogs remain supplied by Chainlit apart from the recorded visual overrides and explicit citation-open policy. Source content rendering, uploads, document processing and settings behavior remain native.

## Do's and Don'ts

### Do:

- **Do** use the paired semantic CSS variables for both themes.
- **Do** keep source links visibly distinct and open their native panes only after explicit citation activation.
- **Do** retain keyboard focus outlines and the mobile information action's accessible name.
- **Do** preserve the final CSS cascade when consulting earlier declarations.
- **Do** keep the local reader's read-only limitation visible and retain a clear return to the current conversation.
- **Do** keep the session-start qualifier beside the document-library vector count and forward summary/delete actions through their existing native controls.
- **Do** keep history close and reopen actions available at every width with synchronized accessible state.
- **Do** keep upload progress and validation inside the upload window and wait for native cancellation acknowledgement before reporting completion.

### Don't:

- **Don't** expand the serif heading treatment into body text or working controls.
- **Don't** treat the light palette as the dark palette or restore the removed berry tile behind the shared transparent logo.
- **Don't** infer new native component states, dimensions or callbacks from these custom overrides.
- **Don't** introduce illustrations, paper textures or decorative motion into this recorded visual direction.
- **Don't** imply that a browser snapshot restores model context, attachments or an account-synchronized conversation.
- **Don't** present the startup vector count as live statistics or restore recognized catalog-management output to the chat transcript.
- **Don't** infer a persisted desktop history preference or replace the native source renderer with a custom pane.
- **Don't** bypass native upload limits or imply that closing preparation cancels an upload already being processed.

