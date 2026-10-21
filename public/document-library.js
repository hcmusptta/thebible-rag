const create = (tag, className, text) => {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
};
const assistantSteps = () => [...document.querySelectorAll('[data-step-type="assistant_message"]')];
const contentText = (step) => step.querySelector(".message-content")?.textContent.replace(/\s+/g, " ").trim() || "";
const storageErrors = new Set([
  "Kho dữ liệu đang bận xử lý. Vui lòng thử lại sau.",
  "Kho dữ liệu đang được tiến trình khác sử dụng. Vui lòng thử lại sau.",
  "Kho dữ liệu chưa hoàn tất nạp tài liệu. Hãy đợi tác vụ hiện tại hoặc khởi động lại ứng dụng để phục hồi.",
]);
let vectorCount = null;
let firstStep;
let pendingSteps = null;
let dismissedRequests = [];
let resultStep = null;
let resultSignature = "";
let loadingTimer;

const closeLibrary = (restoreFocus = true) => {
  document.getElementById("document-library-backdrop")?.remove();
  if (pendingSteps) dismissedRequests.push(pendingSteps);
  pendingSteps = null;
  resultStep = null;
  resultSignature = "";
  clearTimeout(loadingTimer);
  document.getElementById("root")?.removeAttribute("inert");
  if (restoreFocus) document.querySelector('[data-native-action="Kho tài liệu"]')?.focus({ preventScroll: true });
};
const statistics = () => {
  const container = create("div", "document-library-statistics");
  // shortcut: totals come from the existing welcome, add a backend statistics endpoint if live counts are required.
  container.append(create("p", "", vectorCount === null ? "Chưa có thống kê vector trong phiên này." : `Kho hiện có ${vectorCount} vector.`));
  if (vectorCount !== null) container.append(create("small", "", "Ghi nhận khi bắt đầu phiên trò chuyện."));
  return container;
};
const renderResult = () => {
  const body = document.querySelector(".document-library-body");
  if (!body || !resultStep?.isConnected) return;
  const source = resultStep.querySelector(".message-content");
  const nativeButtons = [...resultStep.querySelectorAll("button")]
    .filter((button) => /^(Tóm tắt|Xóa): /.test(button.textContent.trim()));
  const signature = JSON.stringify([vectorCount, source?.innerHTML, nativeButtons.map((button) => [button.id, button.textContent, button.disabled])]);
  if (signature === resultSignature) return;
  resultSignature = signature;
  body.replaceChildren(statistics());
  if (source) {
    const copy = source.cloneNode(true);
    copy.removeAttribute("id");
    copy.querySelectorAll("[id]").forEach((element) => element.removeAttribute("id"));
    body.append(copy);
  }
  if (nativeButtons.length) {
    const controls = create("div", "document-library-actions");
    for (const sourceButton of nativeButtons) {
      const control = create("button", "document-library-action", sourceButton.textContent.trim());
      control.type = "button";
      control.disabled = sourceButton.disabled;
      if (sourceButton.textContent.trim().startsWith("Xóa:")) control.classList.add("document-library-delete");
      control.addEventListener("click", () => {
        if (!sourceButton.isConnected || sourceButton.disabled) return;
        closeLibrary(false);
        document.getElementById("chat-input")?.focus({ preventScroll: true });
        sourceButton.click();
      });
      controls.append(control);
    }
    body.append(controls);
  }
};

export const openDocumentLibrary = () => {
  if (document.getElementById("document-library-backdrop")) return false;
  const backdrop = create("div", "document-library-backdrop");
  backdrop.id = "document-library-backdrop";
  const dialog = create("section", "document-library-dialog");
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("aria-labelledby", "document-library-title");
  const header = create("div", "document-library-header");
  const title = create("h2", "", "Kho tài liệu");
  title.id = "document-library-title";
  const close = create("button", "document-library-close");
  close.type = "button";
  close.setAttribute("aria-label", "Đóng kho tài liệu");
  close.innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg>';
  close.addEventListener("click", () => closeLibrary());
  header.append(title, close);
  const body = create("div", "document-library-body");
  const status = create("p", "document-library-status", "Đang tải danh sách tài liệu…");
  status.setAttribute("role", "status");
  body.append(statistics(), status);
  dialog.append(header, body);
  backdrop.append(dialog);
  backdrop.addEventListener("click", (event) => { if (event.target === backdrop) closeLibrary(); });
  document.body.append(backdrop);
  document.getElementById("root")?.setAttribute("inert", "");
  close.focus({ preventScroll: true });
  resultStep = null;
  resultSignature = "";
  if (pendingSteps) return false;
  pendingSteps = new Set(assistantSteps());
  clearTimeout(loadingTimer);
  loadingTimer = setTimeout(() => {
    const status = document.querySelector(".document-library-status");
    if (status) status.textContent = "Kho tài liệu đang phản hồi chậm. Bạn có thể đóng cửa sổ và quay lại sau.";
  }, 30000);
  return true;
};

export const syncDocumentLibrary = () => {
  const steps = assistantSteps();
  if (firstStep !== steps[0]) {
    firstStep = steps[0];
    vectorCount = null;
    dismissedRequests = [];
  }
  for (const step of steps) {
    const text = contentText(step);
    const welcome = text.match(/^Chào bạn\. Hãy đặt câu hỏi về tài liệu hoặc dùng các nút bên dưới để quản lý tài liệu PDF, Word và TXT\.\s*Kho hiện có (\d+) vector\.$/);
    if (welcome) {
      vectorCount = welcome[1];
      step.classList.add("library-home-message");
      if (!step.querySelector(".library-home-copy")) {
        step.querySelector(".message-content").after(create("p", "library-home-copy", "Chào bạn. Hãy cùng tìm hiểu Kinh Thánh, chú giải và giáo lý qua các tài liệu đã nạp. Đặt câu hỏi cụ thể để tra cứu và đối chiếu nguồn; dùng Thanh công cụ phía trên để thêm tài liệu hoặc mở Kho tài liệu."));
      }
    }
    const catalog = text.startsWith("Các tài liệu trong kho:");
    const management = catalog || text === "Kho tài liệu đang trống." || storageErrors.has(text);
    const alreadyHandled = step.classList.contains("library-documents-result");
    const dismissed = management && !alreadyHandled ? dismissedRequests.findIndex((snapshot) => !snapshot.has(step)) : -1;
    const response = management && !alreadyHandled && dismissed < 0 && pendingSteps && !pendingSteps.has(step);
    if (dismissed >= 0) dismissedRequests.splice(dismissed, 1);
    if (catalog || response || dismissed >= 0) step.classList.add("library-documents-result");
    if (response) {
      resultStep = step;
      pendingSteps = null;
      clearTimeout(loadingTimer);
      resultSignature = "";
    }
  }
  renderResult();
};

document.addEventListener("keydown", (event) => {
  const dialog = document.querySelector("#document-library-backdrop .document-library-dialog");
  if (!dialog) return;
  if (event.key === "Escape") { event.preventDefault(); closeLibrary(); }
  if (event.key !== "Tab") return;
  const controls = [...dialog.querySelectorAll("button:not(:disabled)")];
  const first = controls[0], last = controls.at(-1);
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
});
