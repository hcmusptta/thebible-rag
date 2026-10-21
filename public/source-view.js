// Chainlit opens every new side element automatically; source citations require an explicit click.
// shortcut: uses the installed Chainlit pane DOM, revalidate selectors when upgrading Chainlit.
let selection = null;
let latestUser = null;
const userKey = () => {
  const step = [...document.querySelectorAll('[data-step-type="user_message"]')].at(-1);
  return step?.querySelector('[id^="step-"]')?.id || step || null;
};

const syncSourceView = () => {
  const currentUser = userKey();
  if (latestUser !== currentUser) {
    latestUser = currentUser;
    selection = null;
  }
  const title = document.getElementById("side-view-title");
  const content = document.getElementById("side-view-content");
  if (!title || !content) {
    if (selection?.opened) selection = null;
    return;
  }
  const name = title.textContent.trim();
  if (!/^Nguồn\s+\d+$/.test(name)) return;
  const dialog = content.closest('[role="dialog"]');
  const panel = content.closest("[data-panel]");
  const container = dialog || panel;
  if (!container) return;
  const allowed = selection?.link.isConnected && selection.name === name && content.children.length <= 1;
  const sibling = container.previousElementSibling;
  if (allowed) {
    selection.opened = true;
    container.classList.remove("library-source-suppressed");
    sibling?.classList.remove("library-source-suppressed");
    return;
  }
  container.classList.add("library-source-suppressed");
  if (dialog || sibling?.hasAttribute("data-panel-resize-handle-id")) sibling?.classList.add("library-source-suppressed");
  const close = title.querySelector("button") || dialog?.querySelector(":scope > button");
  close?.click();
};

document.addEventListener("click", (event) => {
  const link = event.target.closest?.('[data-step-type] .element-link');
  if (!event.isTrusted || !link || !/^Nguồn\s+\d+$/.test(link.textContent.trim())) return;
  latestUser = userKey();
  selection = { name: link.textContent.trim(), link, opened: false };
}, true);

new MutationObserver(syncSourceView).observe(document.documentElement, { childList: true, subtree: true, characterData: true });
syncSourceView();
