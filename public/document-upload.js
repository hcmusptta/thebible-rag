const create = (tag, className, text) => {
  const element = document.createElement(tag);
  element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
};
const steps = () => [...document.querySelectorAll('[data-step-type="assistant_message"]')];
const textOf = (step) => step.querySelector(".message-content")?.textContent.trim() || "";
const cancelledText = "Đã hủy thêm tài liệu. Bạn có thể tiếp tục trò chuyện.";
const timeoutText = "Đã đóng hộp chọn file vì chưa có tài liệu được chọn.";
let session;
let toastTimer;

const showToast = (text) => {
  document.querySelector(".document-upload-toast")?.remove();
  clearTimeout(toastTimer);
  const toast = create("p", "document-upload-toast", text);
  toast.setAttribute("role", "status");
  document.body.append(toast);
  toastTimer = setTimeout(() => {
    toast.classList.add("is-leaving");
    toastTimer = setTimeout(() => toast.remove(), 500);
  }, 3500);
};
const dismiss = () => {
  document.querySelector(".document-upload-backdrop")?.remove();
  document.getElementById("root")?.removeAttribute("inert");
  document.querySelector('[data-native-action="Thêm tài liệu"]')?.focus({ preventScroll: true });
  session = null;
};
const cancelUpload = () => {
  if (!session) return;
  if (session.finished) return dismiss();
  if (session.request && (!session.request.isConnected || session.request.querySelector("#ask-upload-button-loading"))) return;
  if (session.status?.isConnected && textOf(session.status).startsWith("Đang nạp")) return;
  if (!session.started) { dismiss(); showToast("Đã hủy thêm tài liệu. Bạn có thể tiếp tục trò chuyện."); return; }
  // The backend owns cancellation and rollback; do not claim the chat is ready before its acknowledgement.
  session.cancelRequested = true;
  const cancel = [...(session.request?.querySelectorAll("button") || [])].find((button) => button.textContent.trim() === "Hủy");
  if (cancel && !cancel.disabled && !session.cancelSent) {
    session.cancelSent = true;
    cancel.click();
  }
  const control = document.querySelector(".document-upload-cancel");
  if (control) {
    control.textContent = "Đang hủy…";
    control.disabled = true;
  }
};

const startUpload = (transfer) => {
  if (!session || session.started || !session.start?.isConnected || session.start.disabled) return;
  if (transfer && !transfer.files.length) return;
  session.started = true;
  if (transfer) {
    session.transfer = new DataTransfer();
    for (const file of transfer.files) session.transfer.items.add(file);
  }
  session.openPicker = !transfer;
  session.start.click();
};

export const openUploadDialog = (start) => {
  if (session) return false;
  document.querySelector(".document-upload-toast")?.remove();
  clearTimeout(toastTimer);
  session = { before: new Set(steps()), toasts: new Set(document.querySelectorAll('[data-sonner-toast]')), signature: "", finished: false, request: null, start };
  const backdrop = create("div", "document-library-backdrop document-upload-backdrop");
  const dialog = create("section", "document-library-dialog document-upload-dialog");
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("aria-labelledby", "document-upload-title");
  const header = create("div", "document-library-header");
  const title = create("h2", "", "Thêm tài liệu");
  title.id = "document-upload-title";
  const close = create("button", "document-library-close document-upload-close");
  close.type = "button";
  close.setAttribute("aria-label", "Hủy thêm tài liệu");
  close.innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg>';
  close.addEventListener("click", cancelUpload);
  header.append(title, close);
  const body = create("div", "document-library-body");
  const note = create("p", "document-upload-note", "Lưu ý: trước khi tải lên, hãy kiểm tra font chữ và dấu tiếng Việt. Thử sao chép một đoạn sang trình soạn thảo để xem chữ có bị lỗi không. Với TXT, dùng Unicode UTF-8 hoặc UTF-16 có BOM.");
  const settings = create("button", "document-library-action document-upload-settings", "Cài đặt loại tài liệu và bản dịch");
  settings.type = "button";
  settings.addEventListener("click", () => {
    const native = document.getElementById("chat-settings-open-modal");
    if (!native || native.disabled) return;
    session.settingsOpen = true;
    backdrop.hidden = true;
    document.getElementById("root")?.removeAttribute("inert");
    native.click();
  });
  const content = create("div", "document-upload-content");
  content.setAttribute("role", "status");
  content.setAttribute("aria-live", "polite");
  const picker = create("div", "document-upload-dropzone");
  picker.append(create("p", "", "Kéo và thả PDF, Word (.docx/.doc) hoặc TXT Unicode vào đây."));
  const browse = create("button", "document-upload-browse", "Chọn tài liệu");
  browse.type = "button";
  browse.addEventListener("click", () => startUpload());
  picker.addEventListener("dragover", (event) => event.preventDefault());
  picker.addEventListener("drop", (event) => { event.preventDefault(); startUpload(event.dataTransfer); });
  picker.append(browse);
  content.append(picker);
  const footer = create("div", "document-upload-footer");
  const cancel = create("button", "document-library-action document-upload-cancel", "Hủy");
  cancel.type = "button";
  cancel.addEventListener("click", cancelUpload);
  footer.append(cancel);
  const errors = create("p", "document-upload-errors");
  errors.setAttribute("role", "alert");
  errors.hidden = true;
  body.append(note, settings, content, errors);
  dialog.append(header, body, footer);
  backdrop.append(dialog);
  backdrop.addEventListener("click", (event) => { if (event.target === backdrop) cancelUpload(); });
  document.body.append(backdrop);
  document.getElementById("root")?.setAttribute("inert", "");
  close.focus({ preventScroll: true });
  return true;
};

export const syncUploadDialog = () => {
  const active = document.getElementById("ask-button-input")?.closest('[data-step-type="assistant_message"]');
  if (!session && active) {
    openUploadDialog(null);
    session.started = true;
    session.request = active;
    session.before.delete(active);
  }
  const all = steps();
  for (const step of all) {
    const text = textOf(step);
    const request = text.startsWith("Chọn loại tài liệu trong Cài đặt, sau đó tải PDF,");
    const acknowledgement = text === cancelledText || text === timeoutText;
    const ingest = /^(Đang nạp\s|Đã nạp\s|Không thể nạp\s|Đã dừng nạp\s)/.test(text) || /^`?[^\n]+ đã có trong kho, không tạo dữ liệu trùng\.$/.test(text);
    if (request || acknowledgement || (session && !session.before.has(step) && ingest)) step.classList.add("library-upload-result");
    if (!session || session.before.has(step)) continue;
    if (acknowledgement) {
      dismiss();
      showToast(text === cancelledText ? "Đã hủy thêm tài liệu. Bạn có thể tiếp tục trò chuyện." : text);
      continue;
    }
    if (request) session.request = step;
    if (ingest) session.status = step;
  }
  if (!session) return;
  for (const toast of document.querySelectorAll('[data-sonner-toast][data-type="error"]')) {
    if (session.toasts.has(toast)) continue;
    session.toasts.add(toast);
    const errors = document.querySelector(".document-upload-errors");
    const text = toast.querySelector('[data-title]')?.textContent || toast.textContent;
    errors.textContent = text.startsWith("File type must be ") ? "Định dạng chưa được hỗ trợ. Hãy chọn PDF, Word (.docx/.doc) hoặc TXT."
      : text.startsWith("File is larger than ") ? "Tài liệu vượt giới hạn dung lượng. Hãy kiểm tra giới hạn trong ô chọn file."
      : text === "Too many files" ? "Hãy chọn một tài liệu mỗi lần tải lên." : text;
    errors.hidden = false;
    toast.classList.add("library-upload-result");
  }
  const backdrop = document.querySelector(".document-upload-backdrop");
  if (session.settingsOpen) {
    const settings = document.querySelector("#chat-settings, #chat-settings-sidebar-title");
    if (settings) session.settingsSeen = true;
    if (session.settingsSeen && !settings) {
      session.settingsOpen = false;
      session.settingsSeen = false;
      backdrop.hidden = false;
      document.getElementById("root")?.setAttribute("inert", "");
      document.querySelector(".document-upload-settings")?.focus({ preventScroll: true });
    }
  }
  if (!session.started) return;
  const request = session.request?.isConnected ? session.request : null;
  const status = session.status?.isConnected ? session.status : null;
  const input = request?.querySelector("#ask-button-input");
  const picker = input?.parentElement;
  const loading = !!request?.querySelector("#ask-upload-button-loading");
  const cancel = [...(request?.querySelectorAll("button") || [])].find((button) => button.textContent.trim() === "Hủy");
  session.finished = !!status && !textOf(status).startsWith("Đang nạp");
  const processing = !!status && !session.finished;
  if (processing || loading) document.querySelector(".document-upload-errors").hidden = true;
  const close = document.querySelector(".document-upload-close");
  const footer = document.querySelector(".document-upload-cancel");
  close.disabled = processing || loading || !!session.cancelRequested;
  close.setAttribute("aria-label", session.finished ? "Đóng thêm tài liệu" : "Hủy thêm tài liệu");
  footer.disabled = processing || loading || !!session.cancelRequested;
  footer.textContent = session.finished ? "Đóng" : session.cancelRequested ? "Đang hủy…" : processing ? "Đang xử lý…" : "Hủy";
  document.querySelector(".document-upload-settings").disabled = !!document.getElementById("chat-settings-open-modal")?.disabled || processing || loading || !!session.cancelRequested;
  if (session.cancelRequested && !session.cancelSent && cancel) cancelUpload();
  if (input && session.transfer) {
    picker.dispatchEvent(new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer: session.transfer }));
    session.transfer = null;
  } else if (input && session.openPicker) {
    session.openPicker = false;
    input.click();
  }
  const source = status?.querySelector(".message-content");
  const signature = JSON.stringify([source?.innerHTML, picker?.innerHTML, loading]);
  if (signature === session.signature) return;
  session.signature = signature;
  const content = document.querySelector(".document-upload-content");
  const focusedPicker = content.contains(document.activeElement);
  content.replaceChildren();
  if (source) {
    const copy = source.cloneNode(true);
    copy.querySelectorAll("[id]").forEach((element) => element.removeAttribute("id"));
    copy.removeAttribute("id");
    content.append(copy);
  } else if (!picker) content.textContent = "Đang mở bộ chọn tài liệu…";
  if (picker && !status) {
    // shortcut: proxy the installed native picker DOM, revalidate selectors when upgrading Chainlit.
    const copy = picker.cloneNode(true);
    copy.className = "document-upload-dropzone";
    copy.removeAttribute("tabindex");
    copy.removeAttribute("role");
    copy.querySelectorAll("input").forEach((element) => element.remove());
    copy.querySelectorAll("[id]").forEach((element) => element.removeAttribute("id"));
    const browse = copy.querySelector("button");
    browse.type = "button";
    browse.addEventListener("click", () => { if (input.isConnected && !loading) input.click(); });
    for (const type of ["dragenter", "dragover", "dragleave", "drop"]) copy.addEventListener(type, (event) => {
      event.preventDefault();
      if (!loading && input.isConnected) picker.dispatchEvent(new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: event.dataTransfer }));
    });
    content.append(copy);
    if (focusedPicker && !browse.disabled) browse.focus({ preventScroll: true });
  }
};

document.addEventListener("keydown", (event) => {
  const dialog = document.querySelector(".document-upload-dialog");
  if (!dialog || dialog.parentElement.hidden) return;
  if (event.key === "Escape") {
    event.preventDefault();
    if (!dialog.querySelector(".document-upload-close").disabled) cancelUpload();
  }
  if (event.key !== "Tab") return;
  const controls = [...dialog.querySelectorAll("button:not(:disabled)")].filter((button) => button.getClientRects().length);
  const first = controls[0], last = controls.at(-1);
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
  if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
});
