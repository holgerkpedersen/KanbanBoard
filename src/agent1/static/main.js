"use strict";

// ---- API client -----------------------------------------------------------
const CSRF = {
  "X-Requested-With": "XMLHttpRequest",
};

// Read CSRF token from meta tag (injected by Flask template).  This is only
// the *initial* value: the cookie may be rotated by a later response (e.g.
// when an expired token is re-issued), so callers must read the live token
// via ``currentCsrfToken()`` instead of trusting this cached copy.
(function () {
  const el = document.querySelector('meta[name="csrf-token"]');
  if (el) CSRF["X-CSRF-Token"] = el.getAttribute("content");
})();

// Read the CSRF token the browser currently holds in the ``kanban_csrf``
// cookie.  The cookie is not HttpOnly, so JS can read it; this is the
// authoritative value after a server-side rotation and keeps the client in
// sync with the cookie jar (fixes "Boards -> Open" failing after the modal's
// listing request rotated an expired token).
function readCsrfFromCookie() {
  const m = (document.cookie || "").match(/(?:^|; )kanban_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : null;
}

function currentCsrfToken() {
  return readCsrfFromCookie() || CSRF["X-CSRF-Token"];
}

// Keep the cached CSRF value in sync with whatever the server reports.  Every
// response carries the (possibly rotated) token in the ``X-CSRF-Token``
// header, so update the cache after each request.
function syncCsrfFromResponse(res) {
  try {
    const rotated = res.headers.get("X-CSRF-Token");
    if (rotated) CSRF["X-CSRF-Token"] = rotated;
  } catch (e) {
    // Headers may be unavailable; ignore.
  }
}

async function api(method, path, body) {
  const opts = {
    method,
    headers: { "X-Requested-With": "XMLHttpRequest", "X-CSRF-Token": currentCsrfToken() },
  };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  syncCsrfFromResponse(res);
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error || `Request failed (${res.status})`);
  }
  return data;
}

const Api = {
  getBoard: () => api("GET", "/api/frames/board"),
  createFrame: (title) => api("POST", "/api/frames", { title }),
  renameFrame: (id, title) => api("PUT", `/api/frames/${id}`, { title }),
  deleteFrame: (id) => api("DELETE", `/api/frames/${id}`),
  reorderFrames: (order) => api("POST", "/api/frames/reorder", { order }),
  createCard: (frameId, card) =>
    api("POST", "/api/cards", { frame_id: frameId, ...card }),
  updateCard: (id, card) => api("PUT", `/api/cards/${id}`, card),
  deleteCard: (id) => api("DELETE", `/api/cards/${id}`),
  moveCard: (id, frameId, index) =>
    api("POST", `/api/cards/${id}/move`, { frame_id: frameId, index }),
  // Card ids linked to an Agent1 issue (read-only view of the shared
  // .card_id_map.json). Used for the integrated-card colouring + filter.
  getLinkedCards: () => api("GET", "/api/cards/linked"),

  // Board management (catalog-based: Agent1 seeds data/boards/,
  // the app copies to a working copy in data/ when the user opens it).
  listBoards: () => api("GET", "/api/boards"),
  getActiveBoard: () => api("GET", "/api/boards/active"),
  selectBoard: (catalog_id) =>
    api("POST", "/api/boards/select", { catalog_id }),
  resetBoard: (catalog_id) =>
    api("POST", "/api/boards/reset", { catalog_id }),
  // Per-board external-sync opt-in. The server persists this to the board's
  // working copy, which Agent1 reads from disk — so toggling it takes effect
  // on both sides of the sync link without a restart.
  getBoardSync: () => api("GET", "/api/boards/sync"),
  setBoardSync: (map) => api("POST", "/api/boards/sync", map),
  // Upload a board (JSON body or multipart file) into the catalog. When
  // `activate` is true the server also selects it as the active board.
  uploadBoard: (body, { activate = false } = {}) => {
    const fd = new FormData();
    if (body instanceof File) {
      fd.append("file", body);
    } else {
      const blob = new Blob([JSON.stringify(body)], {
        type: "application/json",
      });
      fd.append("file", blob, "board.json");
    }
    const qs = activate ? "?activate=1" : "";
    return fetch(`/api/boards/upload${qs}`, {
      method: "POST",
      headers: { "X-Requested-With": "XMLHttpRequest", "X-CSRF-Token": currentCsrfToken() },
      body: fd,
    }).then(async (res) => {
      syncCsrfFromResponse(res);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.error || `Upload failed (${res.status})`);
      }
      return data;
    });
  },
};

// ---- State ----------------------------------------------------------------
const state = {
  frames: [], // [{ id, title, cards: [{id,title,text,tags,frame_id}] }]
  query: "",
  activeBoard: null, // { id, name, path } — set on boot and after every switch
  // Card visibility filters. Date values are "YYYY-MM-DD" strings from the
  // calendar inputs (empty = open-ended); tags is the selected-tag list
  // (empty = tag filter inactive). Matching rules live in search.js.
  filters: {
    createdFrom: "",
    createdTo: "",
    updatedFrom: "",
    updatedTo: "",
    tags: [],
    // True = show only cards integrated with Agent1 (in the id map).
    integrated: false,
  },
  // Whether card descriptions are shown on the board. Persists to localStorage
  // under "kanban.showDescriptions" so it survives page reloads. Default is true.
  showDescriptions: localStorage.getItem("kanban.showDescriptions") !== "0",
  // Card ids linked to an Agent1 issue, loaded from /api/cards/linked.
  // Empty set when the endpoint fails — colouring/filtering degrade to
  // "nothing is integrated", never an error.
  linkedCardIds: new Set(),
};

// Fetch the Agent1-linked card ids into state.linkedCardIds. Non-fatal:
// any failure leaves the previous/empty set in place.
async function refreshLinkedCards() {
  try {
    const data = await Api.getLinkedCards();
    state.linkedCardIds = new Set(Array.isArray(data.card_ids) ? data.card_ids : []);
  } catch (err) {
    console.error("Failed to load linked card ids:", err);
  }
}

// ---- Sound effects (Web Audio synth, no asset files) -----------------------
// Lazily creates a single AudioContext on first user gesture (autoplay
// policy). All sounds are synthesized at runtime. Muted by default; the
// preference is persisted in localStorage.
const sfx = (() => {
  let ctx = null;
  let muted = localStorage.getItem("kanban.sfx.muted") !== "0";
  let lastMove = 0;

  const ensureCtx = () => {
    if (!ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      ctx = new AC();
    }
    if (ctx.state === "suspended") ctx.resume().catch(() => {});
    return ctx;
  };

  // A single oscillator with a quick attack/decay envelope.
  const tone = (type, freq, dur, { gain = 0.2, slideTo = null } = {}) => {
    const c = ensureCtx();
    if (!c) return;
    const t0 = c.currentTime;
    const osc = c.createOscillator();
    const g = c.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, t0);
    if (slideTo) osc.frequency.exponentialRampToValueAtTime(slideTo, t0 + dur);
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(gain, t0 + 0.01);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    osc.connect(g).connect(c.destination);
    osc.start(t0);
    osc.stop(t0 + dur + 0.02);
  };

  // A short filtered noise burst (used for the "whoosh" pickup).
  const noiseBurst = (dur, { gain = 0.15 } = {}) => {
    const c = ensureCtx();
    if (!c) return;
    const frames = Math.floor(c.sampleRate * dur);
    const buf = c.createBuffer(1, frames, c.sampleRate);
    const data = buf.getChannelData(0);
    for (let i = 0; i < frames; i++) {
      data[i] = (Math.random() * 2 - 1) * (1 - i / frames);
    }
    const src = c.createBufferSource();
    src.buffer = buf;
    const g = c.createGain();
    const hp = c.createBiquadFilter();
    hp.type = "highpass";
    hp.frequency.value = 800;
    g.gain.setValueAtTime(gain, c.currentTime);
    g.gain.exponentialRampToValueAtTime(0.0001, c.currentTime + dur);
    src.connect(hp).connect(g).connect(c.destination);
    src.start();
  };

  const guard = () => !muted && ensureCtx();

  return {
    pickup() {
      if (!guard()) return;
      noiseBurst(0.08, { gain: 0.12 });
      tone("sine", 220, 0.08, { gain: 0.18, slideTo: 520 });
    },
    move() {
      const now = performance.now();
      if (now - lastMove < 80) return; // throttle double-fires
      lastMove = now;
      if (!guard()) return;
      const pitch = 320 + Math.floor(Math.random() * 160);
      tone("square", pitch, 0.05, { gain: 0.12 });
    },
    frameSlide() {
      if (!guard()) return;
      tone("sine", 420, 0.15, { gain: 0.2, slideTo: 160 });
    },
    setMuted(m) {
      muted = m;
      localStorage.setItem("kanban.sfx.muted", m ? "1" : "0");
    },
    isMuted: () => muted,
    resume: ensureCtx,
  };
})();

// ---- DOM helpers (XSS-safe: only textContent / createElement) -------------
const $ = (sel, root = document) => root.querySelector(sel);

function escHtml(str) {
  const d = document.createElement("div");
  d.textContent = str || "";
  return d.innerHTML;
}
const board = $("#board");
const frameTpl = $("#frame-template");
const cardTpl = $("#card-template");
const searchInput = $("#search");
const cardModal = $("#card-modal");
const cardForm = $("#card-form");

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

// Format an ISO 8601 timestamp for display as a browser-local calendar day.
// Missing/unparseable dates render as "" so callers can hide them.
function fmtDate(iso) {
  return CardSearch.localDateOf(iso) || "";
}

// The matching rules live in search.js (pure, unit-tested in Node) so they
// can be verified without a browser. Visibility = free-text query AND tag
// selection AND active created/updated date ranges AND the integration
// toggle; each inactive group is a no-op.
function cardVisible(card) {
  return CardSearch.filterCard(card, state.query, state.filters, state.linkedCardIds);
}

// ---- Rendering ------------------------------------------------------------
function render() {
  board.replaceChildren();
  state.frames.forEach((frame) => board.appendChild(renderFrame(frame)));
  refreshSystemList();
  renderTagFilter();
  renderIntegratedFilter();
  renderDescriptionFilter();
}

// Keep the System <datalist> in sync with the systems used on the board so
// users get autocomplete suggestions for existing systems.
function refreshSystemList() {
  const list = $("#system-list");
  if (!list) return;
  const systems = new Set();
  state.frames.forEach((f) =>
    f.cards.forEach((c) => {
      if (c.system) systems.add(c.system);
    })
  );
  list.replaceChildren();
  systems.forEach((s) => {
    const opt = document.createElement("option");
    opt.value = s;
    list.appendChild(opt);
  });
}

function renderFrame(frame) {
  const node = frameTpl.content.firstElementChild.cloneNode(true);
  node.dataset.frameId = frame.id;

  const titleInput = $(".frame-title", node);
  titleInput.value = frame.title;
  titleInput.addEventListener("change", async () => {
    try {
      await Api.renameFrame(frame.id, titleInput.value.trim());
    } catch (e) {
      alert(e.message);
      titleInput.value = frame.title;
    }
  });

  $(".frame-add", node).addEventListener("click", () =>
    openCardModal(frame.id, null)
  );
  $(".frame-del", node).addEventListener("click", async () => {
    if (!confirm(`Delete frame "${frame.title}" and its cards?`)) return;
    try {
      await Api.deleteFrame(frame.id);
      state.frames = state.frames.filter((f) => f.id !== frame.id);
      render();
    } catch (e) {
      alert(e.message);
    }
  });

  const cardsBox = $(".cards", node);
  const visible = frame.cards.filter(cardVisible);
  if (frame.cards.length && !visible.length) {
    cardsBox.appendChild(el("div", "empty-hint", "No matching cards"));
  }
  visible.forEach((card) => cardsBox.appendChild(renderCard(card)));

  wireFrameDrag(node);
  wireCardDropTarget(cardsBox, frame.id, frame.cards);
  return node;
}

function renderCard(card) {
  const node = cardTpl.content.firstElementChild.cloneNode(true);
  node.dataset.cardId = card.id;

  $(".card-title", node).textContent = card.title;
  const text = $(".card-text", node);
  if (card.text) {
    text.textContent = card.text;
    text.classList.toggle("card-text-hidden", !state.showDescriptions);
  } else {
    text.remove();
  }

  // Integrated cards (linked to an Agent1 issue in the shared id map) get
  // a distinct colour and a badge; plain cards keep the default look.
  if (state.linkedCardIds.has(card.id)) {
    node.classList.add("card-integrated");
    const badge = $(".card-integrated-badge", node);
    if (badge) badge.hidden = false;
  } else {
    const badge = $(".card-integrated-badge", node);
    if (badge) badge.remove();
  }

  const systemBox = $(".card-system", node);
  if (card.system) {
    systemBox.textContent = card.system;
    systemBox.hidden = false;
  } else {
    systemBox.remove();
  }

  const tagsBox = $(".card-tags", node);
  (card.tags || []).forEach((t) => tagsBox.appendChild(el("span", "tag", t)));

  // The card ID is the reference users quote when they talk about a card, so
  // it is shown in full and copyable. Both the ID itself and the copy button
  // copy it, and the button confirms with a short "Copied" label.
  const idBox = $(".card-id", node);
  if (idBox) idBox.textContent = card.id;
  const copyBtn = $(".card-copy", node);
  if (copyBtn) {
    copyBtn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const ok = await CardId.copyText(card.id, navigator, document);
      const previous = copyBtn.title;
      copyBtn.title = ok ? "Copied" : "Copy failed";
      copyBtn.classList.toggle("copied", ok);
      setTimeout(() => {
        copyBtn.title = previous;
        copyBtn.classList.remove("copied");
      }, 1200);
    });
  }

  $(".card-edit", node).addEventListener("click", () =>
    openCardModal(card.frame_id, card)
  );
  $(".card-del", node).addEventListener("click", async () => {
    if (!confirm(`Delete card "${card.title}"?`)) return;
    try {
      await Api.deleteCard(card.id);
      removeCardFromState(card.id);
      render();
    } catch (e) {
      alert(e.message);
    }
  });

  wireCardDrag(node);
  return node;
}

function removeCardFromState(cardId) {
  state.frames.forEach((f) => {
    f.cards = f.cards.filter((c) => c.id !== cardId);
  });
}

// ---- Card modal -----------------------------------------------------------
let editing = null; // { frameId, card | null }

// ---- Card editor width follows the resized description ---------------------
// The description textarea has `resize: both`. Widening it must widen the whole
// panel, so the other fields stay inside the editor and stay aligned. The maths
// lives in modalwidth.js (pure, unit-tested in Node); this only does the DOM
// reads/writes and guards against the observer feeding itself.
let syncingCardWidth = false;

function cardModalPadding() {
  const styles = getComputedStyle(cardForm);
  const left = parseFloat(styles.paddingLeft) || 0;
  const right = parseFloat(styles.paddingRight) || 0;
  return left + right;
}

// Measure the description field and resize the panel to match it.
function syncModalWidth() {
  const field = $("#card-text");
  if (!field || syncingCardWidth) return;
  const plan = ModalWidth.planSizes({
    textareaWidth: field.getBoundingClientRect().width,
    cardPadding: cardModalPadding(),
    viewportWidth: window.innerWidth,
  });
  if (!plan) return;

  syncingCardWidth = true;
  try {
    // Only touch a width that actually needs changing. Writing to the panel
    // resizes the field, which re-triggers the observer, so a no-op write would
    // keep the loop alive.
    const cardWidth = Math.round(cardForm.getBoundingClientRect().width);
    if (cardWidth !== plan.cardWidth) {
      cardForm.style.width = plan.cardWidth + "px";
    }
    const fieldWidth = Math.round(field.getBoundingClientRect().width);
    if (fieldWidth !== plan.textareaWidth) {
      field.style.width = plan.textareaWidth + "px";
    }
  } finally {
    syncingCardWidth = false;
  }
}

// Start each open from the stylesheet width, so a width dragged out in a
// previous session does not leak into the next card.
function resetCardModalWidth() {
  cardForm.style.width = "";
  const field = $("#card-text");
  if (field) field.style.width = "";
}

// ResizeObserver is the reliable signal: it reports the user's textarea drag,
// and also covers window resizes that squeeze the field.
if (typeof ResizeObserver === "function") {
  const widthObserver = new ResizeObserver(syncModalWidth);
  widthObserver.observe(cardForm);
  const textField = $("#card-text");
  if (textField) widthObserver.observe(textField);
}

function openCardModal(frameId, card) {
  editing = { frameId, card };
  $("#card-title").value = card ? card.title : "";
  $("#card-text").value = card ? card.text || "" : "";
  $("#card-tags").value = card ? (card.tags || []).join(", ") : "";
  $("#card-system").value = card ? card.system || "" : "";

  // Populate the parent-card dropdown with every card on the board.
  const parentSelect = $("#card-parent");
  if (parentSelect) {
    const currentParentId = card && card.parent_id ? card.parent_id : "";
    let optionsHtml = '<option value="">— None —</option>';
    state.frames.forEach((f) => {
      f.cards.forEach((c) => {
        // Don't list the card itself as its own parent.
        if (card && c.id === card.id) return;
        const selected = c.id === currentParentId ? ' selected' : '';
        optionsHtml += `<option value="${c.id}"${selected}>${escHtml(c.title)}</option>`;
      });
    });
    parentSelect.innerHTML = optionsHtml;
  }

  // Existing cards show their read-only ID, so it can be copied without
  // leaving the editor. New cards have no ID yet, so the field stays hidden.
  const idField = $("#card-id-field");
  const idInput = $("#card-id");
  if (idField && idInput) {
    const hasId = Boolean(card && card.id);
    idInput.value = hasId ? card.id : "";
    idField.hidden = !hasId;
  }
  // Existing cards show their created/updated timestamps in local time.
  const dates = $("#card-dates");
  if (dates) {
    const created = fmtDate(card && card.created_at);
    const updated = fmtDate(card && card.updated_at);
    if (created || updated) {
      dates.textContent = "Created: " + (created || "unknown") + "   Updated: " + (updated || "unknown");
      dates.hidden = false;
    } else {
      dates.hidden = true;
    }
  }
  $("#card-modal-title").textContent = card ? "Edit card" : "New card";
  resetCardModalWidth();
  cardModal.classList.remove("hidden");
  $("#card-title").focus();
}

function closeCardModal() {
  cardModal.classList.add("hidden");
  editing = null;
}

cardModal.addEventListener("click", (e) => {
  if (e.target.hasAttribute("data-close")) closeCardModal();
});

// Copy button inside the editor: type="button", so it never submits the form.
const cardIdCopyBtn = $("#card-id-copy");
if (cardIdCopyBtn) {
  cardIdCopyBtn.addEventListener("click", async () => {
    const value = $("#card-id").value;
    const ok = await CardId.copyText(value, navigator, document);
    const previous = cardIdCopyBtn.textContent;
    cardIdCopyBtn.textContent = ok ? "Copied" : "Copy failed";
    setTimeout(() => {
      cardIdCopyBtn.textContent = previous;
    }, 1200);
  });
}

cardForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const title = $("#card-title").value.trim();
  const text = $("#card-text").value.trim();
  const tags = $("#card-tags")
    .value.split(",")
    .map((t) => t.trim())
    .filter(Boolean);
  const system = $("#card-system").value.trim();
  // Read parent_id from the dropdown; empty string means no parent.
  const parentId = ($("#card-parent").value || "").trim() || "";
  if (!title) return;

  try {
    if (editing.card) {
      const updated = await Api.updateCard(editing.card.id, {
        title,
        text,
        tags,
        system,
        parent_id: parentId,
      });
      Object.assign(editing.card, updated);
    } else {
      const created = await Api.createCard(editing.frameId, {
        title,
        text,
        tags,
        system,
        parent_id: parentId,
      });
      const frame = state.frames.find((f) => f.id === editing.frameId);
      frame.cards.push(created);
    }
    closeCardModal();
    render();
  } catch (err) {
    alert(err.message);
  }
});

// ---- Drag & drop: cards ---------------------------------------------------
let draggedCardId = null;

function wireCardDrag(node) {
  node.addEventListener("dragstart", (e) => {
    draggedCardId = node.dataset.cardId;
    node.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", draggedCardId);
    sfx.pickup();
  });
  node.addEventListener("dragend", () => {
    draggedCardId = null;
    node.classList.remove("dragging");
    document.querySelectorAll(".placeholder").forEach((p) => p.remove());
    document.querySelectorAll(".drop-active").forEach((d) => d.classList.remove("drop-active"));
  });
}

// Map a count of visible cards (DOM order) to the real index in the
// frame's full card list. Filtering hides cards from the DOM, so a plain
// DOM count would point at the wrong server position; hidden cards that
// sit before the drop gap must be counted too. The dragged card itself is
// excluded - the server removes it from its old position before inserting.
// Hidden cards AFTER the gap are never counted: the walk stops as soon as
// the gap position is reached, so a hidden card sitting beyond the dragged
// card (e.g. between it and the gap in list order) must not inflate the
// index.
function visibleCountToRealIndex(allCards, draggedId) {
  return function (visibleCount) {
    let real = 0;
    for (const card of allCards) {
      if (visibleCount === 0 && cardVisible(card)) break; // gap sits before this card
      if (card.id === draggedId) continue;
      real++;
      if (cardVisible(card)) visibleCount--;
    }
    return real;
  };
}

function wireCardDropTarget(box, frameId, allCards) {
  box.addEventListener("dragover", (e) => {
    if (!draggedCardId) return;
    e.preventDefault();
    box.classList.add("drop-active");
    const placeholder = ensurePlaceholder(box);
    const after = DnD.cardAfterPoint(box, e.clientY);
    if (after == null) box.appendChild(placeholder);
    else box.insertBefore(placeholder, after);
  });
  box.addEventListener("dragleave", () => box.classList.remove("drop-active"));
  box.addEventListener("drop", async (e) => {
    e.preventDefault();
    box.classList.remove("drop-active");
    const placeholder = box.querySelector(".placeholder");
    // Insert index = cards before the placeholder, translated from the
    // visible (filtered) DOM order into the frame's full card order.
    const index = DnD.computeInsertIndex(
      box,
      placeholder,
      visibleCountToRealIndex(allCards || [], draggedCardId)
    );
    const cardId = draggedCardId;
    try {
      await Api.moveCard(cardId, frameId, index);
      sfx.move();
      await reload();
    } catch (err) {
      alert(err.message);
    }
  });
}

function ensurePlaceholder(box) {
  let p = box.querySelector(".placeholder");
  if (!p) {
    p = el("div", "placeholder");
    p.textContent = "";
  }
  return p;
}

// ---- Drag & drop: frames (horizontal reorder) -----------------------------
let draggedFrameId = null;

function wireFrameDrag(node) {
  node.setAttribute("draggable", "true");
  node.addEventListener("dragstart", (e) => {
    // A card drag bubbles up to the frame. Returning early lets the card
    // stay draggable. We must NOT call preventDefault here, or the card
    // drag would be cancelled. Only start a frame drag when initiated
    // outside a card (i.e. from the frame header / background).
    if (e.target.closest(".card")) return;
    draggedFrameId = node.dataset.frameId;
    node.classList.add("dragging-frame");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", draggedFrameId);
    sfx.pickup();
  });
  node.addEventListener("dragend", () => {
    draggedFrameId = null;
    node.classList.remove("dragging-frame");
    document.querySelectorAll(".drop-target").forEach((d) => d.classList.remove("drop-target"));
  });
  node.addEventListener("dragover", (e) => {
    if (!draggedFrameId || draggedFrameId === node.dataset.frameId) return;
    if (e.target.closest(".card")) return; // card drag handled by cards box
    e.preventDefault();
    node.classList.add("drop-target");
  });
  node.addEventListener("dragleave", () => node.classList.remove("drop-target"));
  node.addEventListener("drop", async (e) => {
    if (!draggedFrameId || draggedFrameId === node.dataset.frameId) return;
    if (e.target.closest(".card")) return;
    e.preventDefault();
    node.classList.remove("drop-target");
    const fromIdx = state.frames.findIndex((f) => f.id === draggedFrameId);
    const toIdx = state.frames.findIndex((f) => f.id === node.dataset.frameId);
    if (fromIdx < 0 || toIdx < 0) return;
    const [moved] = state.frames.splice(fromIdx, 1);
    state.frames.splice(toIdx, 0, moved);
    try {
      await Api.reorderFrames(state.frames.map((f) => f.id));
      sfx.frameSlide();
      render();
    } catch (err) {
      alert(err.message);
    }
  });
}

// ---- Search ---------------------------------------------------------------
searchInput.addEventListener("input", () => {
  state.query = searchInput.value;
  render();
});

// ---- Filters: calendar date ranges + tag chips ------------------------------
// The four <input type="date"> controls write inclusive "YYYY-MM-DD" bounds
// into state.filters; the tag chip row renders one toggle per tag used on
// the board. Like the free-text query, filtering is client-side only: all
// cards stay loaded, hidden ones simply do not render.
const DATE_FILTER_INPUTS = [
  ["filter-created-from", "createdFrom"],
  ["filter-created-to", "createdTo"],
  ["filter-updated-from", "updatedFrom"],
  ["filter-updated-to", "updatedTo"],
];

DATE_FILTER_INPUTS.forEach(([id, key]) => {
  const input = document.getElementById(id);
  if (!input) return;
  input.addEventListener("change", () => {
    state.filters[key] = input.value || "";
    render();
  });
});

const clearFiltersBtn = document.getElementById("clear-filters");
if (clearFiltersBtn) {
  clearFiltersBtn.addEventListener("click", () => resetFilters());
}

// "Integrated" toggle: show only cards linked to an Agent1 issue.
const integratedFilterBtn = document.getElementById("filter-integrated");
if (integratedFilterBtn) {
  integratedFilterBtn.addEventListener("click", () => {
    state.filters.integrated = !state.filters.integrated;
    render();
  });
}

// Keep the toggle's pressed state and label in sync with the filter.
function renderIntegratedFilter() {
  if (!integratedFilterBtn) return;
  const on = Boolean(state.filters.integrated);
  integratedFilterBtn.setAttribute("aria-pressed", String(on));
  integratedFilterBtn.classList.toggle("active", on);
}

// "Show Descriptions" toggle: reveal or hide card description text on the board.
const showDescriptionsBtn = document.getElementById("show-descriptions");
if (showDescriptionsBtn) {
  showDescriptionsBtn.addEventListener("click", () => {
    state.showDescriptions = !state.showDescriptions;
    localStorage.setItem("kanban.showDescriptions", state.showDescriptions ? "1" : "0");
    render();
  });
}

function renderDescriptionFilter() {
  if (!showDescriptionsBtn) return;
  const on = Boolean(state.showDescriptions);
  showDescriptionsBtn.setAttribute("aria-pressed", String(on));
  showDescriptionsBtn.classList.toggle("active", on);
}

// Clear every filter: free-text query, date ranges, tag selection and the
// integration toggle.
function resetFilters() {
  state.query = "";
  if (searchInput) searchInput.value = "";
  DATE_FILTER_INPUTS.forEach(([id, key]) => {
    state.filters[key] = "";
    const input = document.getElementById(id);
    if (input) input.value = "";
  });
  state.filters.tags = [];
  state.filters.integrated = false;
  render();
}

// Render one chip per tag used anywhere on the board. Chips reflect the
// current selection via aria-pressed; clicking a chip toggles its tag.
function renderTagFilter() {
  const box = document.getElementById("tag-filter");
  if (!box) return;
  const tags = new Set();
  state.frames.forEach((f) =>
    (f.cards || []).forEach((c) =>
      (c.tags || []).forEach((t) => t && t.trim() && tags.add(t))
    )
  );
  // Drop selected tags that no longer exist on the board.
  state.filters.tags = state.filters.tags.filter((t) => tags.has(t));
  const sorted = [...tags].sort((a, b) => a.localeCompare(b));
  box.replaceChildren();
  if (!sorted.length) return;
  box.appendChild(el("span", "filter-label", "Tags:"));
  sorted.forEach((tag) => {
    const chip = el("button", "tag-chip", tag);
    chip.type = "button";
    chip.setAttribute("aria-pressed", String(state.filters.tags.includes(tag)));
    chip.addEventListener("click", () => {
      const i = state.filters.tags.indexOf(tag);
      if (i >= 0) state.filters.tags.splice(i, 1);
      else state.filters.tags.push(tag);
      render();
    });
    box.appendChild(chip);
  });
}

// ---- Boot -----------------------------------------------------------------
async function reload() {
  state.frames = await Api.getBoard();
  await refreshLinkedCards();
  render();
}

$("#add-frame").addEventListener("click", async () => {
  const title = prompt("Frame title:", "New Frame");
  if (title == null) return;
  try {
    const frame = await Api.createFrame(title.trim() || "New Frame");
    state.frames.push({ ...frame, cards: [] });
    render();
  } catch (e) {
    alert(e.message);
  }
});

reload().catch((e) => alert("Failed to load board: " + e.message));

// ---- Sound: mute toggle + first-run prompt --------------------------------
const sfxToggle = $(".sfx-toggle");
function renderSfxToggle() {
  sfxToggle.textContent = sfx.isMuted() ? "🔇" : "🔊";
  sfxToggle.setAttribute(
    "aria-label",
    sfx.isMuted() ? "Unmute drag sounds" : "Mute drag sounds"
  );
}
renderSfxToggle();
sfxToggle.addEventListener("click", () => {
  const nowMuted = !sfx.isMuted(); // toggle: mute<->unmute
  sfx.setMuted(nowMuted);
  if (!nowMuted) sfx.resume(); // resume on the user gesture when turning sound on
  renderSfxToggle();
});

// One-time first-run prompt to opt into sound (autoplay policy + respect users).
const sfxPrompt = $("#sfx-prompt");
if (sfxPrompt && !localStorage.getItem("kanban.sfx.prompted")) {
  sfxPrompt.hidden = false;
  sfxPrompt.addEventListener("click", () => {
    sfx.setMuted(false);
    sfx.resume();
    renderSfxToggle();
    localStorage.setItem("kanban.sfx.prompted", "1");
    sfxPrompt.hidden = true;
  });
}

// ---- Theme: dark / light / contrast selector ------------------------------
// The saved theme is applied before first paint by an inline nonce'd script
// in index.html (anti-flash). Here we keep the <select> in sync and persist
// the user's choice, mirroring the sfx preference flow.
const THEMES = ["dark", "light", "contrast", "contrast-dark"];
const themeSelect = $("#theme-select");
function applyTheme(theme) {
  if (!THEMES.includes(theme)) theme = "dark";
  document.documentElement.setAttribute("data-theme", theme);
  if (themeSelect) themeSelect.value = theme;
}
applyTheme(localStorage.getItem("kanban.theme") || "dark");
if (themeSelect) {
  themeSelect.addEventListener("change", () => {
    const theme = themeSelect.value;
    localStorage.setItem("kanban.theme", theme);
    applyTheme(theme);
  });
}

// ---- Board manager --------------------------------------------------------
// The set of available boards is the contents of the catalog folder
// (default `data/boards/`), which Agent1 maintains. When the user picks
// a board, the server copies the seed into a working copy in
// `data/board-<id>.json` and loads that. Subsequent picks re-use the
// same working copy so progress is preserved. `Reset` re-seeds from
// the catalog and discards the working copy.
const boardModal = $("#board-modal");
const boardSwitcherBtn = $("#board-switcher");
const boardNameEl = $("#board-name");
const boardSelectEl = $("#board-select");
const boardOpenBtn = $("#board-open-submit");
const boardResetBtn = $("#board-reset-submit");
const boardModalError = $("#board-modal-error");
const boardModalInfo = $("#board-modal-info");
const boardCatalogDirEl = $("#board-catalog-dir");
const boardWorkingDirEl = $("#board-working-dir");
const boardUploadFileEl = $("#board-upload-file");
const boardUploadBtn = $("#board-upload-submit");
const boardSyncToggleEl = $("#board-sync-toggle");
let syncToggling = false; // guard: don't stomp an in-flight toggle with a refresh

async function refreshBoardSync() {
  if (!boardSyncToggleEl || syncToggling) return;
  try {
    const data = await Api.getBoardSync();
    boardSyncToggleEl.disabled = !data.active;
    boardSyncToggleEl.checked = Boolean(data.sync && data.sync.agent1);
  } catch (err) {
    // Non-fatal: leave the toggle as-is, don't block board loading.
    console.error("Failed to load sync settings:", err);
  }
}

if (boardSyncToggleEl) {
  boardSyncToggleEl.addEventListener("change", async () => {
    const wanted = boardSyncToggleEl.checked;
    const prev = !wanted;
    syncToggling = true;
    try {
      await Api.setBoardSync({ agent1: wanted });
    } catch (err) {
      boardSyncToggleEl.checked = prev; // revert on failure
      console.error("Failed to save sync settings:", err);
      showBoardError(err.message);
    } finally {
      syncToggling = false;
    }
  });
}

function setActiveBoardLabel(name) {
  if (boardNameEl) boardNameEl.textContent = name || "Board";
}

function showBoardError(msg) {
  if (!boardModalError) return;
  boardModalError.textContent = msg;
  boardModalError.hidden = false;
}

function clearBoardError() {
  if (!boardModalError) return;
  boardModalError.textContent = "";
  boardModalError.hidden = true;
}

function showBoardInfo(msg) {
  if (!boardModalInfo) return;
  boardModalInfo.textContent = msg;
  boardModalInfo.hidden = !msg;
}

function renderBoardList(catalog, activeId, currentPath) {
  if (!boardSelectEl) return;
  boardSelectEl.replaceChildren();
  if (!catalog || catalog.length === 0) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "(no boards in the catalog folder)";
    opt.disabled = true;
    opt.selected = true;
    boardSelectEl.appendChild(opt);
    boardSelectEl.disabled = true;
    if (boardOpenBtn) boardOpenBtn.disabled = true;
    if (boardResetBtn) boardResetBtn.disabled = true;
    return;
  }
  boardSelectEl.disabled = false;
  if (boardOpenBtn) boardOpenBtn.disabled = false;
  if (boardResetBtn) boardResetBtn.disabled = false;
  catalog.forEach((b) => {
    const opt = document.createElement("option");
    opt.value = b.id;
    opt.textContent = b.name;
    if (b.id === activeId) opt.selected = true;
    boardSelectEl.appendChild(opt);
  });
  if (currentPath) {
    showBoardInfo(`Edits are saved to: ${currentPath}`);
  } else {
    showBoardInfo("");
  }
}

async function refreshBoardList() {
  const data = await Api.listBoards();
  if (boardCatalogDirEl && data.catalog_dir) {
    boardCatalogDirEl.textContent = data.catalog_dir;
  }
  if (boardWorkingDirEl && data.working_dir) {
    boardWorkingDirEl.textContent = data.working_dir;
  }
  renderBoardList(data.catalog || [], data.active, data.current_path);
}

async function openSelectedBoard() {
  clearBoardError();
  if (!boardSelectEl || !boardSelectEl.value) return;
  const id = boardSelectEl.value;
  try {
    await Api.selectBoard(id);
    await loadActiveBoard(/* reloadBoard= */ true);
    closeBoardModal();
  } catch (err) {
    showBoardError(err.message);
  }
}

async function resetSelectedBoard() {
  clearBoardError();
  if (!boardSelectEl || !boardSelectEl.value) return;
  const id = boardSelectEl.value;
  const name =
    boardSelectEl.options[boardSelectEl.selectedIndex]?.textContent || id;
  if (!confirm(`Discard local edits and re-seed "${name}" from the catalog?`)) {
    return;
  }
  try {
    await Api.resetBoard(id);
    await loadActiveBoard(true);
    await refreshBoardList();
  } catch (err) {
    showBoardError(err.message);
  }
}

async function uploadBoardFile() {
  clearBoardError();
  if (!boardUploadFileEl || !boardUploadFileEl.files.length) {
    showBoardError("Choose a board .json file to upload first.");
    return;
  }
  const file = boardUploadFileEl.files[0];
  try {
    const result = await Api.uploadBoard(file, { activate: false });
    showBoardInfo(`Uploaded "${result.name}" — open it from the list.`);
    boardUploadFileEl.value = "";
    await refreshBoardList();
  } catch (err) {
    showBoardError(err.message);
  }
}

async function loadActiveBoard(reloadBoard = false) {
  const active = await Api.getActiveBoard();
  state.activeBoard = active && active.id ? active : null;
  setActiveBoardLabel(state.activeBoard ? state.activeBoard.name : "");
  refreshBoardSync(); // keep the per-board Agent1 sync toggle in sync
  if (reloadBoard || state.frames.length === 0) {
    state.frames = await Api.getBoard();
    await refreshLinkedCards(); // linked ids are per-board: refresh on switch
    resetFilters(); // a board switch always starts with no filters applied
  }
}

function openBoardModal() {
  if (!boardModal) return;
  clearBoardError();
  showBoardInfo("");
  refreshBoardList().catch((err) => showBoardError(err.message));
  boardModal.classList.remove("hidden");
}

function closeBoardModal() {
  if (!boardModal) return;
  boardModal.classList.add("hidden");
}

if (boardSwitcherBtn) {
  boardSwitcherBtn.addEventListener("click", openBoardModal);
}

if (boardOpenBtn) {
  boardOpenBtn.addEventListener("click", openSelectedBoard);
}

if (boardResetBtn) {
  boardResetBtn.addEventListener("click", resetSelectedBoard);
}

if (boardUploadBtn) {
  boardUploadBtn.addEventListener("click", uploadBoardFile);
}

// Double-click a row to open it without using the Open button.
if (boardSelectEl) {
  boardSelectEl.addEventListener("dblclick", openSelectedBoard);
}

// Backdrop / Close button dismiss the modal.
if (boardModal) {
  boardModal.querySelectorAll("[data-close]").forEach((el) => {
    el.addEventListener("click", closeBoardModal);
  });
}

// ---- Boot -----------------------------------------------------------------
// Load the active board + its frames on first paint. If there is no
// active board yet but the catalog has boards, open the modal so the
// user can pick one. If the catalog itself is empty, show a hint and
// leave the empty board in view.
(async function boot() {
  try {
    const data = await Api.listBoards();
    if (data.active) {
      await loadActiveBoard(true);
      return;
    }
    if (data.catalog && data.catalog.length > 0) {
      setActiveBoardLabel("(choose a board)");
      openBoardModal();
      return;
    }
    setActiveBoardLabel("(no boards in catalog)");
    // Render an empty board rather than the modal: nothing to pick.
    state.frames = await Api.getBoard();
    render();
  } catch (err) {
    setActiveBoardLabel("(error)");
    showBoardError(err.message);
  }
})();
