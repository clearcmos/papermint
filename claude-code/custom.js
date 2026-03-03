/**
 * Custom search modal for self-hosted Mintlify docs.
 * Intercepts Cmd+K / Ctrl+K and queries the local search API.
 */
(function () {
  const SEARCH_API =
    window.__SEARCH_API_URL || window.location.origin.replace(/:\d+$/, ":3002");

  // --- Styles ---
  const STYLES = `
    .cs-overlay {
      position: fixed; inset: 0;
      background: rgba(0,0,0,0.6);
      z-index: 99999;
      display: flex; align-items: flex-start; justify-content: center;
      padding-top: 12vh;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }
    .cs-modal {
      background: #09090B;
      border: 1px solid #27272A;
      border-radius: 12px;
      width: 580px; max-width: 90vw;
      max-height: 70vh;
      display: flex; flex-direction: column;
      box-shadow: 0 25px 60px rgba(0,0,0,0.5);
      overflow: hidden;
    }
    .cs-input-wrap {
      display: flex; align-items: center;
      padding: 12px 16px;
      border-bottom: 1px solid #27272A;
      gap: 10px;
    }
    .cs-input-wrap svg {
      flex-shrink: 0;
      color: #71717A;
    }
    .cs-input {
      flex: 1;
      background: transparent;
      border: none; outline: none;
      color: #FAFAFA;
      font-size: 15px;
    }
    .cs-input::placeholder { color: #52525B; }
    .cs-kbd {
      font-size: 11px;
      color: #52525B;
      border: 1px solid #27272A;
      border-radius: 4px;
      padding: 2px 6px;
    }
    .cs-body {
      overflow-y: auto;
      padding: 8px;
      flex: 1;
    }
    .cs-section-label {
      font-size: 11px;
      font-weight: 600;
      color: #71717A;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      padding: 8px 8px 4px;
    }
    .cs-result {
      display: block;
      padding: 10px 12px;
      border-radius: 8px;
      text-decoration: none;
      color: #FAFAFA;
      cursor: pointer;
      transition: background 0.1s;
    }
    .cs-result:hover, .cs-result.cs-active {
      background: #18181B;
    }
    .cs-result-title {
      font-size: 14px;
      font-weight: 500;
      color: #FAFAFA;
    }
    .cs-result-heading {
      font-size: 12px;
      color: #D4A27F;
      margin-left: 6px;
    }
    .cs-result-snippet {
      font-size: 12px;
      color: #A1A1AA;
      margin-top: 3px;
      line-height: 1.4;
      overflow: hidden;
      text-overflow: ellipsis;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
    }
    .cs-ai-section {
      border-top: 1px solid #27272A;
      padding: 12px 16px;
    }
    .cs-ai-label {
      font-size: 11px;
      font-weight: 600;
      color: #D4A27F;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      margin-bottom: 8px;
    }
    .cs-ai-answer {
      font-size: 13px;
      color: #D4D4D8;
      line-height: 1.6;
      white-space: pre-wrap;
    }
    .cs-ai-answer .cs-cursor {
      display: inline-block;
      width: 6px; height: 14px;
      background: #D4A27F;
      margin-left: 2px;
      animation: cs-blink 1s step-end infinite;
      vertical-align: text-bottom;
    }
    @keyframes cs-blink {
      50% { opacity: 0; }
    }
    .cs-empty {
      padding: 32px 16px;
      text-align: center;
      color: #52525B;
      font-size: 13px;
    }
    .cs-spinner {
      display: inline-block;
      width: 14px; height: 14px;
      border: 2px solid #27272A;
      border-top-color: #D4A27F;
      border-radius: 50%;
      animation: cs-spin 0.6s linear infinite;
      margin-right: 6px;
      vertical-align: middle;
    }
    @keyframes cs-spin {
      to { transform: rotate(360deg); }
    }
  `;

  let modal = null;
  let activeIndex = -1;
  let resultEls = [];
  let debounceTimer = null;
  let askController = null;

  function injectStyles() {
    if (document.getElementById("cs-styles")) return;
    const el = document.createElement("style");
    el.id = "cs-styles";
    el.textContent = STYLES;
    document.head.appendChild(el);
  }

  function open() {
    if (modal) return;
    injectStyles();

    const overlay = document.createElement("div");
    overlay.className = "cs-overlay";
    overlay.addEventListener("mousedown", (e) => {
      if (e.target === overlay) close();
    });

    overlay.innerHTML = `
      <div class="cs-modal">
        <div class="cs-input-wrap">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
               stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>
          </svg>
          <input class="cs-input" placeholder="Search docs..." autofocus />
          <span class="cs-kbd">ESC</span>
        </div>
        <div class="cs-body">
          <div class="cs-empty">Type to search documentation</div>
        </div>
        <div class="cs-ai-section" style="display:none">
          <div class="cs-ai-label">AI Answer</div>
          <div class="cs-ai-answer"></div>
        </div>
      </div>
    `;

    document.body.appendChild(overlay);
    modal = overlay;

    const input = overlay.querySelector(".cs-input");
    input.addEventListener("input", onInput);
    input.addEventListener("keydown", onKeydown);
    input.focus();
  }

  function close() {
    if (!modal) return;
    if (askController) askController.abort();
    modal.remove();
    modal = null;
    activeIndex = -1;
    resultEls = [];
  }

  function onInput(e) {
    const query = e.target.value.trim();
    clearTimeout(debounceTimer);
    if (!query) {
      renderEmpty();
      return;
    }
    debounceTimer = setTimeout(() => {
      doSearch(query);
      doAsk(query);
    }, 250);
  }

  function onKeydown(e) {
    if (e.key === "Escape") {
      close();
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive(activeIndex + 1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive(activeIndex - 1);
    } else if (e.key === "Enter" && activeIndex >= 0 && resultEls[activeIndex]) {
      e.preventDefault();
      resultEls[activeIndex].click();
    }
  }

  function setActive(idx) {
    if (resultEls.length === 0) return;
    activeIndex = Math.max(0, Math.min(idx, resultEls.length - 1));
    resultEls.forEach((el, i) => el.classList.toggle("cs-active", i === activeIndex));
    resultEls[activeIndex]?.scrollIntoView({ block: "nearest" });
  }

  function renderEmpty() {
    if (!modal) return;
    const body = modal.querySelector(".cs-body");
    body.innerHTML = '<div class="cs-empty">Type to search documentation</div>';
    modal.querySelector(".cs-ai-section").style.display = "none";
    resultEls = [];
    activeIndex = -1;
  }

  async function doSearch(query) {
    if (!modal) return;
    const body = modal.querySelector(".cs-body");
    body.innerHTML = '<div class="cs-empty"><span class="cs-spinner"></span>Searching...</div>';

    try {
      const resp = await fetch(`${SEARCH_API}/api/search`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query }),
      });
      const data = await resp.json();

      if (!modal) return;
      if (!data.results || data.results.length === 0) {
        body.innerHTML = '<div class="cs-empty">No results found</div>';
        resultEls = [];
        activeIndex = -1;
        return;
      }

      let html = '<div class="cs-section-label">Results</div>';
      for (const r of data.results) {
        const heading = r.heading ? `<span class="cs-result-heading">${esc(r.heading)}</span>` : "";
        html += `
          <a class="cs-result" href="${esc(r.url)}">
            <div class="cs-result-title">${esc(r.title)}${heading}</div>
            <div class="cs-result-snippet">${esc(r.snippet)}</div>
          </a>`;
      }
      body.innerHTML = html;
      resultEls = Array.from(body.querySelectorAll(".cs-result"));
      activeIndex = -1;

      // Navigate on click
      resultEls.forEach((el) => {
        el.addEventListener("click", (e) => {
          e.preventDefault();
          close();
          window.location.href = el.getAttribute("href");
        });
      });
    } catch (err) {
      if (!modal) return;
      body.innerHTML = `<div class="cs-empty">Search unavailable (${esc(err.message)})</div>`;
      resultEls = [];
    }
  }

  async function doAsk(query) {
    if (!modal) return;
    if (askController) askController.abort();
    askController = new AbortController();

    const section = modal.querySelector(".cs-ai-section");
    const answerEl = modal.querySelector(".cs-ai-answer");
    section.style.display = "block";
    answerEl.innerHTML = '<span class="cs-spinner"></span>Thinking...';

    try {
      const resp = await fetch(`${SEARCH_API}/api/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query }),
        signal: askController.signal,
      });

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let answer = "";
      answerEl.innerHTML = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const text = decoder.decode(value, { stream: true });
        for (const line of text.split("\n")) {
          if (!line.startsWith("data: ")) continue;
          const payload = line.slice(6).trim();
          if (payload === "[DONE]") break;
          try {
            const parsed = JSON.parse(payload);
            if (parsed.token) {
              answer += parsed.token;
              answerEl.textContent = answer;
            }
          } catch {}
        }
      }

      if (!answer && modal) {
        answerEl.textContent = "No answer available.";
      }
    } catch (err) {
      if (err.name === "AbortError") return;
      if (modal) {
        answerEl.textContent = `AI answer unavailable (${err.message})`;
      }
    }
  }

  function esc(s) {
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  // --- Intercept Cmd+K / Ctrl+K ---
  document.addEventListener(
    "keydown",
    (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        e.stopPropagation();
        if (modal) {
          close();
        } else {
          open();
        }
      }
    },
    true
  );
})();
