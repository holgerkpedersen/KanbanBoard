/*
 * Shared board API client: CSRF handling + the /api/frames and /api/cards
 * calls. Extracted from main.js so /tree-view (tree_view.js) can edit cards
 * through the exact same endpoints as the board view, without loading the
 * board renderer.
 *
 * UMD-style wrapper (see search.js): a browser global `BoardApi` plus a
 * Node `module.exports`, so the client can be unit-tested in Node.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.BoardApi = api;
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // ---- CSRF ---------------------------------------------------------------
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
      headers: {
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRF-Token": currentCsrfToken(),
      },
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

  // ---- Endpoints ----------------------------------------------------------
  const Api = {
    getBoard: () => api("GET", "/api/frames/board"),
    getTree: () => api("GET", "/api/frames/tree"),
    createFrame: (title) => api("POST", "/api/frames", { title }),
    renameFrame: (id, title) => api("PUT", `/api/frames/${id}`, { title }),
    deleteFrame: (id) => api("DELETE", `/api/frames/${id}`),
    reorderFrames: (order) => api("POST", "/api/frames/reorder", { order }),
    createCard: (frameId, card) =>
      api("POST", "/api/cards", { frame_id: frameId, ...card }),
    updateCard: (id, card) => api("PUT", `/api/cards/${id}`, card),
    deleteCard: (id) => api("DELETE", `/api/cards/${id}`),
    workCard: (id) => api("POST", `/api/cards/${id}/work`, {}),
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
        headers: {
          "X-Requested-With": "XMLHttpRequest",
          "X-CSRF-Token": currentCsrfToken(),
        },
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

  return {
    Api,
    api,
    currentCsrfToken,
    readCsrfFromCookie,
    syncCsrfFromResponse,
  };
});
