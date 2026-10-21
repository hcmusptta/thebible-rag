(() => {
  const ids = { button: "runtime-about-button", backdrop: "runtime-about-backdrop" };

  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  const addSection = (container, title, rows) => {
    const section = el("section", "runtime-about-section");
    section.append(el("h3", "", title));
    const list = el("dl");
    rows.forEach(([label, value]) => {
      const row = el("div", "runtime-about-row");
      row.append(
        el("dt", "runtime-about-label", label),
        el("dd", "runtime-about-value", String(value ?? "Không có"))
      );
      list.append(row);
    });
    section.append(list);
    container.append(section);
  };

  const formatProviders = (providers) =>
    providers.map(({ name, model }) => `${name} (${model})`).join(" → ");

  const buildContent = (config) => {
    const content = el("div", "runtime-about-content");
    const providers = config.llm?.providers || [];
    const primary = providers[0];
    addSection(content, "Runtime", [
      ["Ứng dụng", config.runtime?.application],
      ["Framework", config.runtime?.framework],
      ["Python", config.runtime?.python],
    ]);
    addSection(content, "Mô hình ngôn ngữ", [
      ["Provider chính", primary?.name],
      ["Model chính", primary?.model],
      ["Chuỗi xử lý", formatProviders(providers)],
      ["Fallback", formatProviders(providers.slice(1)) || "Không bật"],
      ["Ollama endpoint", config.llm?.ollama_base_url],
      ["Temperature", config.llm?.temperature],
    ]);
    addSection(content, "Truy xuất", [
      ["Embedding", config.retrieval?.embedding_model],
      ["Reranker", config.retrieval?.reranker_model],
      ["Reranker đang bật", config.retrieval?.reranker_enabled ? "Có" : "Không"],
      ["Số kết quả", config.retrieval?.top_k],
      ["Ứng viên rerank", config.retrieval?.candidate_limit],
    ]);
    addSection(content, "Lưu trữ", [
      ["Đường dẫn Chroma", config.storage?.database_path],
      ["Chroma collection", config.storage?.collection],
    ]);
    addSection(content, "Tài liệu", [
      ["PDF tối đa", `${config.documents?.max_pdf_size_mb} MB`],
      ["Số trang tối đa", config.documents?.max_pdf_pages],
      ["Embedding batch", config.documents?.embedding_batch_size],
      ["Tóm tắt tối đa", `${config.documents?.summary_max_chars} ký tự`],
      ["Kích thước đoạn tóm tắt", config.documents?.summary_chunk_chars],
    ]);
    addSection(content, "Đánh giá RAGAS", [
      ["Judge model", config.benchmark?.judge_model],
      ["Số mẫu mặc định", config.benchmark?.sample_size],
      ["Top K", config.benchmark?.top_k],
      ["Ứng viên", config.benchmark?.candidate_limit],
      ["Timeout", `${config.benchmark?.timeout_seconds} giây`],
    ]);
    return content;
  };

  const close = () => {
    const backdrop = document.getElementById(ids.backdrop);
    if (!backdrop) return;
    backdrop.remove();
    document.getElementById("root")?.removeAttribute("inert");
    const button = document.getElementById(ids.button);
    button?.setAttribute("aria-expanded", "false");
    button?.focus();
  };

  const open = async () => {
    if (document.getElementById(ids.backdrop)) return close();
    const button = document.getElementById(ids.button);
    button?.setAttribute("aria-expanded", "true");

    const backdrop = el("div", "runtime-about-backdrop");
    backdrop.id = ids.backdrop;
    const dialog = el("div", "runtime-about-dialog");
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    dialog.setAttribute("aria-labelledby", "runtime-about-title");
    const header = el("div", "runtime-about-header");
    const title = el("h2", "", "Cấu hình hiện tại");
    title.id = "runtime-about-title";
    const closeButton = el("button", "runtime-about-close");
    closeButton.innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg>';
    closeButton.type = "button";
    closeButton.title = "Đóng";
    closeButton.setAttribute("aria-label", "Đóng");
    closeButton.addEventListener("click", close);
    header.append(title, closeButton);
    const status = el("p", "runtime-about-status", "Đang đọc cấu hình...");
    dialog.append(header, status);
    backdrop.append(dialog);
    backdrop.addEventListener("click", (event) => {
      if (event.target === backdrop) close();
    });
    document.body.append(backdrop);
    document.getElementById("root")?.setAttribute("inert", "");
    closeButton.focus();

    try {
      const response = await fetch("/public/runtime-config.json", { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      status.replaceWith(buildContent(await response.json()));
    } catch (error) {
      status.textContent = "Không thể đọc cấu hình. Hãy khởi động lại ứng dụng.";
      console.error("Unable to load runtime configuration", error);
    }
  };

  const mount = () => {
    const welcome = document.getElementById("welcome-screen");
    if (welcome && !welcome.querySelector(".library-intro")) {
      const intro = el("div", "library-intro");
      intro.append(el("h1", "", "Cùng đọc, cùng hiểu Lời Chúa."));
      intro.append(el("p", "", "Đặt câu hỏi về Kinh Thánh, chú giải và giáo lý.\nĐối chiếu câu trả lời với nguồn trong tài liệu của bạn."));
      welcome.querySelector("#message-composer")?.before(intro);
    }
    const header = document.getElementById("header");
    if (!header || document.getElementById(ids.button)) return;
    const brand = el("div", "library-brand");
    const mark = el("span", "library-mark");
    mark.setAttribute("aria-hidden", "true");
    const logo = el("img");
    logo.src = "/public/library-logo.svg";
    logo.alt = "";
    mark.append(logo);
    const name = el("div", "library-brand-copy");
    name.append(el("strong", "", "Thư viện Kinh Thánh"), el("span", "", "Đọc hiểu · Tra cứu · Đối chiếu"));
    brand.append(mark, name);
    header.firstElementChild?.append(brand);
    const button = el("button", "runtime-about-button");
    const infoIcon = el("span", "runtime-about-icon");
    infoIcon.setAttribute("aria-hidden", "true");
    infoIcon.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7v.1"/></svg>';
    button.append(infoIcon, el("span", "runtime-about-text", "Thông tin"));
    button.id = ids.button;
    button.type = "button";
    button.title = "About - Cấu hình hiện tại";
    button.setAttribute("aria-label", "Mở cấu hình hiện tại");
    button.setAttribute("aria-haspopup", "dialog");
    button.setAttribute("aria-expanded", "false");
    button.addEventListener("click", open);
    header.lastElementChild?.prepend(button);
    document.getElementById("new-chat-button")?.setAttribute("aria-label", "Cuộc trò chuyện mới");
    document.getElementById("theme-toggle")?.setAttribute("aria-label", "Đổi giao diện sáng hoặc tối");
  };

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") close();
    const dialog = document.querySelector(".runtime-about-dialog");
    if (dialog && event.key === "Tab") {
      event.preventDefault();
      dialog.querySelector("button")?.focus();
    }
  });
  new MutationObserver(mount).observe(document.documentElement, { childList: true, subtree: true });
  document.readyState === "loading"
    ? document.addEventListener("DOMContentLoaded", mount, { once: true })
    : mount();
})();

import("/public/workspace.js").catch((error) => console.error("Unable to load chat workspace", error));
