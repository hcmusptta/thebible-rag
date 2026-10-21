import { openDocumentLibrary, syncDocumentLibrary } from "/public/document-library.js";
import "/public/source-view.js";
import { openUploadDialog, syncUploadDialog } from "/public/document-upload.js";

(() => {
  const storageKey = "scripture-chat-history-v1";
  const actions = ["Thêm tài liệu", "Kho tài liệu", "Đánh giá RAG", "Xóa toàn bộ"];
  const icons = {
    history: '<path d="M3 11a9 9 0 1 0 3-6.7L3 7"/><path d="M3 3v4h4M12 7v5l3 2"/>',
    close: '<path d="m6 6 12 12M18 6 6 18"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    back: '<path d="m10 5-7 7 7 7M3 12h18"/>',
  };
  const node = (tag, className, text) => {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (text !== undefined) result.textContent = text;
    return result;
  };
  const icon = (name) => {
    const result = node("span", "workspace-icon");
    result.setAttribute("aria-hidden", "true");
    result.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${icons[name]}</svg>`;
    return result;
  };
  const button = (label, className, onClick, iconName) => {
    const result = node("button", className);
    result.type = "button";
    if (iconName) result.append(icon(iconName));
    result.append(node("span", "", label));
    result.addEventListener("click", onClick);
    return result;
  };

  let entries = [];
  let storageError = "";
  let selectedId = null;
  let listSignature = "";
  let saveTimer;
  let desktopHistoryOpen = true;
  const deletedIds = new Set();
  let sidebar, main, historyList, historyStatus, reader, toggle, backdrop;
  const narrow = matchMedia("(max-width: 900px)");
  const validEntry = (entry) => entry && typeof entry.id === "string" &&
    typeof entry.title === "string" && Number.isFinite(entry.updatedAt) && !Number.isNaN(new Date(entry.updatedAt).getTime()) &&
    Array.isArray(entry.messages) && entry.messages.every((message) =>
      message && ["user", "assistant"].includes(message.role) && typeof message.text === "string");
  const readEntries = () => {
    const value = JSON.parse(localStorage.getItem(storageKey) || "[]");
    if (!Array.isArray(value) || !value.every(validEntry)) throw new Error("Invalid chat history");
    return value;
  };
  try {
    entries = readEntries();
  } catch {
    storageError = "Không thể đọc lịch sử đã lưu. Phiên chat hiện tại vẫn hoạt động.";
  }

  const nativeAction = (label) => [...document.querySelectorAll("[data-step-type] button")]
    .filter((candidate) => candidate.textContent.trim() === label).at(-1);

  const syncHistoryToggle = () => {
    if (!sidebar || !toggle) return;
    sidebar.classList.toggle("is-collapsed", !desktopHistoryOpen);
    const expanded = narrow.matches ? sidebar.classList.contains("is-open") : desktopHistoryOpen;
    toggle.setAttribute("aria-expanded", String(expanded));
    toggle.setAttribute("aria-label", expanded ? "Đóng lịch sử trò chuyện" : "Mở lịch sử trò chuyện");
    toggle.title = expanded ? "Đóng lịch sử trò chuyện" : "Mở lịch sử trò chuyện";
  };

  const closeDrawer = () => {
    if (!sidebar?.classList.contains("is-open")) return;
    sidebar.classList.remove("is-open");
    sidebar.removeAttribute("role");
    sidebar.removeAttribute("aria-modal");
    main.inert = false;
    backdrop.hidden = true;
    syncHistoryToggle();
    toggle.focus({ preventScroll: true });
  };
  const closeHistory = () => {
    if (narrow.matches) return closeDrawer();
    desktopHistoryOpen = false;
    syncHistoryToggle();
    toggle.focus({ preventScroll: true });
  };
  const showCurrent = () => {
    selectedId = null;
    main?.classList.remove("workspace-reading-history");
    main?.querySelector(":scope > [data-panel-group]")?.removeAttribute("inert");
    if (reader) reader.hidden = true;
    renderHistory();
  };
  const showEntry = (entry) => {
    selectedId = entry.id;
    reader.replaceChildren();
    const heading = node("div", "history-reader-heading");
    heading.append(button("Về cuộc trò chuyện hiện tại", "workspace-back", () => {
      showCurrent();
      document.getElementById("chat-input")?.focus({ preventScroll: true });
    }, "back"));
    heading.append(node("h1", "", entry.title));
    heading.append(node("p", "history-reader-note", "Bản lưu trên trình duyệt để xem lại. Nguồn đính kèm và ngữ cảnh cho mô hình không được khôi phục."));
    reader.append(heading);
    for (const message of entry.messages) {
      const article = node("article", `history-message history-message-${message.role}`);
      article.append(node("h2", "", message.role === "user" ? "Bạn" : "Trợ lý"));
      article.append(node("div", "history-message-text", message.text));
      reader.append(article);
    }
    reader.hidden = false;
    main.classList.add("workspace-reading-history");
    main.querySelector(":scope > [data-panel-group]")?.setAttribute("inert", "");
    closeDrawer();
    reader.querySelector("button").focus({ preventScroll: true });
    reader.scrollTop = 0;
    renderHistory();
  };
  const renderHistory = () => {
    if (!historyList) return;
    historyStatus.textContent = storageError || "Lưu trên trình duyệt này, từ các cuộc trò chuyện bạn mở ở đây.";
    const warning = document.querySelector(".workspace-storage-error");
    if (warning) {
      warning.textContent = storageError;
      warning.hidden = !storageError;
    }
    const ordered = [...entries].sort((a, b) => b.updatedAt - a.updatedAt);
    const signature = JSON.stringify([ordered.map(({ id, title, updatedAt }) => [id, title, Math.floor(updatedAt / 60000)]), selectedId]);
    if (signature === listSignature) return;
    listSignature = signature;
    const focusedRow = document.activeElement.closest(".history-item");
    const focusedId = focusedRow?.dataset.entryId;
    const focusedAction = document.activeElement.classList.contains("history-remove") ? ".history-remove" : ".history-open";
    historyList.replaceChildren();
    if (!entries.length) {
      historyList.append(node("p", "workspace-empty", "Chưa có cuộc trò chuyện. Lịch sử sẽ xuất hiện sau câu hỏi đầu tiên."));
      if (focusedId) sidebar.querySelector(".workspace-new-chat")?.focus({ preventScroll: true });
      return;
    }
    for (const entry of ordered) {
      const row = node("div", "history-item");
      row.dataset.entryId = entry.id;
      const open = button(entry.title, "history-open", () => showEntry(entries.find((item) => item.id === entry.id)));
      open.title = entry.title;
      if (selectedId === entry.id) open.setAttribute("aria-current", "true");
      const date = node("time", "", new Date(entry.updatedAt).toLocaleString("vi-VN", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }));
      date.dateTime = new Date(entry.updatedAt).toISOString();
      open.append(date);
      const remove = button("Xóa bản lưu", "history-remove", () => {
        try {
          const remaining = readEntries().filter((item) => item.id !== entry.id);
          localStorage.setItem(storageKey, JSON.stringify(remaining));
          deletedIds.add(entry.id);
          entries = remaining;
          storageError = "";
          if (selectedId === entry.id) showCurrent();
          renderHistory();
        } catch {
          storageError = "Không thể xóa bản lưu. Hãy kiểm tra quyền lưu trữ của trình duyệt.";
          renderHistory();
        }
      }, "close");
      remove.setAttribute("aria-label", `Xóa bản lưu: ${entry.title}`);
      remove.title = `Xóa bản lưu: ${entry.title}`;
      row.append(open, remove);
      historyList.append(row);
    }
    if (focusedId) {
      const row = [...historyList.children].find((item) => item.dataset.entryId === focusedId);
      (row?.querySelector(focusedAction) || historyList.querySelector("button") || sidebar.querySelector(".workspace-new-chat"))?.focus({ preventScroll: true });
    }
  };
  const saveConversation = () => {
    const messages = [...document.querySelectorAll('[data-step-type="user_message"], [data-step-type="assistant_message"]')]
      .filter((step) => !step.matches(".library-documents-result, .library-home-message, .library-upload-result"))
      .map((step) => ({
        id: step.querySelector('[id^="step-"]')?.id,
        role: step.dataset.stepType === "user_message" ? "user" : "assistant",
        text: step.querySelector(".message-content")?.innerText.trim() || "",
      }));
    const first = messages.findIndex((message) => message.role === "user");
    if (first < 0 || !messages[first].id || !messages[first].text) return;
    if (deletedIds.has(messages[first].id)) return;
    const conversation = {
      id: messages[first].id,
      title: messages[first].text.replace(/\s+/g, " ").slice(0, 100),
      messages: messages.slice(first).filter((message) => message.text).map(({ role, text }) => ({ role, text })),
    };
    const existing = entries.find((entry) => entry.id === conversation.id);
    if (existing && JSON.stringify(existing.messages) === JSON.stringify(conversation.messages)) return;
    if (storageError) return;
    try {
      const current = readEntries();
      conversation.updatedAt = Date.now();
      entries = [conversation, ...current.filter((entry) => entry.id !== conversation.id)];
      localStorage.setItem(storageKey, JSON.stringify(entries));
    } catch {
      storageError = "Không thể lưu lịch sử. Trình duyệt có thể đã hết chỗ hoặc chặn lưu trữ.";
    }
    renderHistory();
  };

  const mount = () => {
    const header = document.getElementById("header");
    if (!header) return;
    main = header.parentElement;
    if (!document.getElementById("library-function-bar")) {
      main.classList.add("library-main");
      const bar = node("nav", "library-function-bar");
      bar.id = "library-function-bar";
      bar.setAttribute("aria-label", "Thanh công cụ");
      const label = node("div", "function-bar-heading");
      label.append(node("span", "", "Thanh công cụ"));
      toggle = button("Lịch sử", "history-toggle", () => {
        if (!narrow.matches) {
          desktopHistoryOpen = !desktopHistoryOpen;
          syncHistoryToggle();
          return;
        }
        if (sidebar.classList.contains("is-open")) return closeDrawer();
        sidebar.classList.add("is-open");
        sidebar.setAttribute("role", "dialog");
        sidebar.setAttribute("aria-modal", "true");
        main.inert = true;
        backdrop.hidden = false;
        syncHistoryToggle();
        sidebar.querySelector("button").focus();
      }, "history");
      toggle.setAttribute("aria-controls", "library-chat-history");
      toggle.setAttribute("aria-expanded", "false");
      label.append(toggle);
      const controls = node("div", "function-bar-actions");
      for (const label of actions) {
        const control = button(label, "function-action", () => {
          saveConversation();
          showCurrent();
          if (label === "Kho tài liệu" && !openDocumentLibrary()) return;
          if (label === "Thêm tài liệu") { openUploadDialog(nativeAction(label)); return; }
          nativeAction(label)?.click();
        });
        control.dataset.nativeAction = label;
        control.disabled = true;
        if (label === "Xóa toàn bộ") control.classList.add("function-action-danger");
        controls.append(control);
      }
      const warning = node("p", "workspace-storage-error");
      warning.setAttribute("role", "status");
      warning.hidden = true;
      bar.append(label, controls, warning);
      header.after(bar);
      reader = node("section", "library-history-reader");
      reader.setAttribute("aria-label", "Nội dung cuộc trò chuyện đã lưu");
      reader.hidden = true;
      main.append(reader);
    }
    if (!document.getElementById("library-chat-history")) {
      sidebar = node("aside", "library-chat-history");
      sidebar.id = "library-chat-history";
      sidebar.setAttribute("aria-labelledby", "history-heading");
      const title = node("div", "history-sidebar-heading");
      const heading = node("h2", "", "Lịch sử trò chuyện");
      heading.id = "history-heading";
      const close = button("Đóng lịch sử", "history-drawer-close", closeHistory, "close");
      close.setAttribute("aria-label", "Đóng lịch sử");
      title.append(heading, close);
      const start = button("Trò chuyện mới", "workspace-new-chat", () => {
        saveConversation();
        showCurrent();
        closeDrawer();
        document.getElementById("new-chat-button")?.click();
      }, "plus");
      historyList = node("div", "history-list");
      historyStatus = node("p", "history-storage-note");
      historyStatus.setAttribute("role", "status");
      sidebar.append(title, start, historyList, historyStatus);
      main.before(sidebar);
      backdrop = node("div", "history-drawer-backdrop");
      backdrop.hidden = true;
      backdrop.addEventListener("click", closeDrawer);
      sidebar.after(backdrop);
      listSignature = "";
      renderHistory();
    }
    syncHistoryToggle();
    for (const label of actions) {
      const source = nativeAction(label);
      document.querySelectorAll("[data-step-type] button").forEach((candidate) => {
        if (candidate.textContent.trim() === label) candidate.classList.add("library-original-action");
      });
      const control = [...document.querySelectorAll(".function-action")].find((item) => item.dataset.nativeAction === label);
      control.disabled = !source || source.disabled;
      control.title = source?.title || (source ? label : "Đang chờ phiên trò chuyện sẵn sàng");
      if (source?.querySelector("svg") && !control.querySelector("svg")) {
        const glyph = source.querySelector("svg").cloneNode(true);
        glyph.removeAttribute("class");
        control.prepend(glyph);
      }
    }
    syncDocumentLibrary();
    syncUploadDialog();
  };

  let frame;
  new MutationObserver((mutations) => {
    if (mutations.every((mutation) => mutation.target.closest?.(".library-chat-history, .library-history-reader, .library-function-bar, .document-library-backdrop"))) return;
    if (!frame) frame = requestAnimationFrame(() => { frame = null; mount(); });
    clearTimeout(saveTimer);
    saveTimer = setTimeout(saveConversation, 500);
  }).observe(document.documentElement, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ["disabled"] });
  document.addEventListener("click", (event) => {
    if (event.target.closest("#new-chat-button")) {
      saveConversation();
      showCurrent();
    }
  }, true);
  document.addEventListener("keydown", (event) => {
    if (!narrow.matches && event.key === "Escape" && sidebar?.contains(document.activeElement)) closeHistory();
    if (!narrow.matches || !sidebar?.classList.contains("is-open")) return;
    if (event.key === "Escape") closeDrawer();
    if (event.key !== "Tab") return;
    const controls = [...sidebar.querySelectorAll("button")].filter((control) => control.getClientRects().length && !control.disabled);
    const first = controls[0], last = controls.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  narrow.addEventListener("change", () => { closeDrawer(); syncHistoryToggle(); });
  window.addEventListener("pagehide", saveConversation);
  document.addEventListener("visibilitychange", () => { if (document.hidden) saveConversation(); });
  window.addEventListener("storage", (event) => {
    if (event.key !== storageKey && event.key !== null) return;
    try {
      const latest = readEntries();
      for (const entry of entries) {
        if (!latest.some((item) => item.id === entry.id)) deletedIds.add(entry.id);
      }
      entries = latest;
      storageError = "";
      if (selectedId && !entries.some((entry) => entry.id === selectedId)) showCurrent();
      renderHistory();
    } catch {
      storageError = "Không thể cập nhật lịch sử từ tab khác.";
      renderHistory();
    }
  });
  mount();
})();
