/*
 * Tree View page controller (/tree-view).
 *
 * The tree view shows the same cards as the board, so it has the same
 * capabilities: search, tag / date-range / integrated filters, and the card
 * editor (create, edit, delete, move between frames). All of these operate on
 * the shared in-memory board through the same endpoints the board view uses,
 * so a change made here is immediately visible on the board — and vice versa.
 *
 * Data source: GET /api/frames/tree, which nests cards by parent_id. The flat
 * card list is derived from that payload (frame_id travels on every node), so
 * filters can test any card, not only roots.
 *
 * This file is ES5-only (string concatenation, var, no arrow functions): the
 * webviews rendering this page are IE-mode, and tests/test_tree_view_depth.py
 * pins the template's inline script to ES5 as well.
 */
(function () {
  "use strict";

  // ---- DOM helpers --------------------------------------------------------
  function $(sel, root) {
    return (root || document).querySelector(sel);
  }

  function escHtml(str) {
    var d = document.createElement("div");
    d.textContent = str == null ? "" : String(str);
    return d.innerHTML;
  }

  // Format an ISO 8601 timestamp for display as a browser-local calendar day.
  // Missing/unparseable dates render as "" so callers can hide them.
  function fmtDate(iso) {
    return window.CardSearch ? CardSearch.localDateOf(iso) || "" : "";
  }

  // ---- State --------------------------------------------------------------
  var state = {
    frames: [], // [{ frame_id, frame_title, parents: [nodes...] }]
    cards: {}, // card id -> flat card dict (from the tree payload)
    query: "",
    filters: {
      createdFrom: "",
      createdTo: "",
      updatedFrom: "",
      updatedTo: "",
      tags: [],
      integrated: false
    },
    showDescriptions: localStorage.getItem("kanban.showDescriptions") !== "0",
    linkedCardIds: [] // array of ids; used as a Set when available
  };

  function linkedSet() {
    return typeof Set === "function" ? new Set(state.linkedCardIds) : state.linkedCardIds;
  }

  // Fetch the Agent1-linked card ids. Non-fatal: any failure leaves the
  // previous/empty list in place, so the integrated filter degrades to
  // "nothing is linked" instead of breaking the page.
  function refreshLinkedCards(done) {
    BoardApi.Api.getLinkedCards().then(function (data) {
      state.linkedCardIds = Array.isArray(data.card_ids) ? data.card_ids : [];
      done();
    })["catch"](function () {
      done();
    });
  }

  // ---- Filtering ----------------------------------------------------------
  // Matching rules live in search.js (pure, unit-tested in Node): free-text
  // query AND tag selection AND created/updated date ranges AND the
  // integration toggle. Each inactive group is a no-op.
  function cardVisible(card) {
    return CardSearch.filterCard(card, state.query, state.filters, linkedSet());
  }

  // A parent node is kept when it matches, or when any descendant does —
  // otherwise filtering would hide the context a matching child sits in.
  function nodeVisible(node) {
    if (cardVisible(node)) return true;
    var kids = node.children || [];
    for (var i = 0; i < kids.length; i++) {
      if (nodeVisible(kids[i])) return true;
    }
    return false;
  }

  function filtersActive() {
    return CardSearch.filtersActive(state.filters) || Boolean(state.query.trim());
  }

  // ---- Rendering ----------------------------------------------------------
  var treeRoot = $("#tree-root");
  var loadingEl = $("#loading");
  var errorEl = $("#error");
  var controlsEl = $("#controls");

  function statusClass(s) {
    return "status-" + (s || "open").replace(/ /g, "-");
  }

  // Build a single card node (parent or child).
  function renderCard(node, isChild) {
    var tags = "";
    if (node.tags && node.tags.length) {
      tags =
        "<div>" +
        node.tags
          .map(function (t) {
            return '<span class="tag">' + escHtml(t) + "</span>";
          })
          .join("") +
        "</div>";
    }

    // Shorten text preview to first 120 chars, honouring the shared
    // "show descriptions" toggle used by the board view.
    var textPreview = "";
    if (node.text && !isChild && state.showDescriptions) {
      var shortText =
        node.text.length > 120
          ? escHtml(node.text.substring(0, 120)) + "…"
          : escHtml(node.text);
      textPreview = '<div class="text-preview">' + shortText + "</div>";
    }

    var html = "";
    if (isChild) {
      html += '<li><div class="card-child">';
    } else {
      html += '<li><div class="card-parent" onclick="TreeApp.toggleChildren(this)">';
    }

    var statusBadge =
      '<span class="' + statusClass(node.status) + '">' + (node.status || "open") + "</span>";

    if (isChild) {
      html += '<div class="title">' + escHtml(node.title) + "</div>";
    } else {
      html +=
        '<div class="title"><span class="toggle-icon" aria-hidden="true">▶</span>' +
        escHtml(node.title) +
        " " +
        statusBadge +
        "</div>";
    }

    if (node.system) {
      html += '<div class="meta">System: ' + escHtml(node.system) + "</div>";
    }
    if (!isChild && node.created_at) {
      var created = new Date(node.created_at);
      html +=
        '<div class="meta">Created: ' +
        created.toLocaleDateString() +
        " " +
        created.toLocaleTimeString() +
        "</div>";
    }
    // The card ID is the reference users quote when they talk about a card, so
    // it is shown in full and copyable — same as on the board.
    html +=
      '<div class="card-id-row"><code class="card-id" title="Card ID">' +
      escHtml(node.id) +
      '</code><button class="icon-btn card-copy" type="button" title="Copy card ID">⧉</button></div>';

    html += textPreview;
    html += tags;

    // Card tools: the tree view edits the same cards as the board.
    html +=
      '<div class="card-tools">' +
      '<button class="icon-btn card-work" type="button" title="Work (send to Agent1)" data-card-id="' +
      escHtml(node.id) +
      '">▶</button>' +
      '<button class="icon-btn card-edit" type="button" title="Edit" data-card-id="' +
      escHtml(node.id) +
      '">✎</button>' +
      '<button class="icon-btn card-del" type="button" title="Delete" data-card-id="' +
      escHtml(node.id) +
      '">×</button>' +
      "</div>";

    var kids = node.children || [];
    if (kids.length) {
      // Recurse into children — at EVERY depth, so a card nested under a
      // child card renders too. The old guard (recursion only from
      // top-level nodes) stopped at depth 2 and hid grandchildren entirely.
      html +=
        "<ul>" +
        kids
          .map(function (child) {
            return renderCard(child, true);
          })
          .join("") +
        "</ul>";
    } else if (!isChild) {
      // No children — show a hint.
      html += '<div class="meta" style="font-style:italic;">No child cards</div>';
    }

    html += "</div></li>";
    return html;
  }

  function renderTree() {
    if (!state.frames.length) {
      treeRoot.innerHTML = '<p style="color:var(--muted);">No cards found in the board.</p>';
      return;
    }

    var html = "";
    state.frames.forEach(function (frame) {
      var visibleParents = [];
      (frame.parents || []).forEach(function (parent) {
        if (nodeVisible(parent)) visibleParents.push(parent);
      });

      html += '<div class="frame-section">';
      html += '<h2 class="frame-title">' + escHtml(frame.frame_title) + "</h2>";
      if (visibleParents.length) {
        html +=
          "<ul>" +
          visibleParents
            .map(function (parent) {
              return renderCard(parent, false);
            })
            .join("") +
          "</ul>";
      } else {
        html +=
          '<p style="color:var(--muted);font-style:italic;">' +
          (filtersActive() ? "No matching cards in this frame." : "No parent cards in this frame.") +
          "</p>";
      }
      html += "</div>";
    });

    treeRoot.innerHTML = html;
    renderTagFilter();
    renderIntegratedFilter();
    renderDescriptionFilter();
  }

  // ---- Expand / collapse --------------------------------------------------
  function toggleChildren(el) {
    var icon = el.querySelector(".toggle-icon");
    var ul = el.nextElementSibling;
    if (!ul || !ul.tagName || ul.tagName.toLowerCase() !== "ul") return;

    var isHidden = ul.style.display === "none";
    ul.style.display = isHidden ? "" : "none";
    icon.classList.toggle("expanded", isHidden);
  }

  function expandAll() {
    document.querySelectorAll(".tree > ul, .tree ul ul").forEach(function (ul) {
      ul.style.display = "";
    });
    document.querySelectorAll(".toggle-icon").forEach(function (icon) {
      icon.classList.add("expanded");
    });
  }

  function collapseAll() {
    document.querySelectorAll(".tree > ul, .tree ul ul").forEach(function (ul) {
      ul.style.display = "none";
    });
    document.querySelectorAll(".toggle-icon").forEach(function (icon) {
      icon.classList.remove("expanded");
    });
  }

  // ---- Data ---------------------------------------------------------------
  // Flatten the tree payload into id -> card, so filters and the editor can
  // look any card up by id. frame_id is carried on every node by the server.
  function indexNodes(nodes) {
    (nodes || []).forEach(function (node) {
      state.cards[node.id] = node;
      indexNodes(node.children);
    });
  }

  function loadData(done) {
    BoardApi.Api.getTree().then(function (data) {
      state.frames = Array.isArray(data) ? data : [];
      state.cards = {};
      state.frames.forEach(function (frame) {
        indexNodes(frame.parents);
      });
      done();
    })["catch"](function (err) {
      loadingEl.style.display = "none";
      errorEl.style.display = "block";
      errorEl.textContent = "Failed to load tree: " + err.message;
    });
  }

  function reload() {
    refreshLinkedCards(function () {
      loadData(function () {
        loadingEl.style.display = "none";
        controlsEl.hidden = false;
        renderTree();
        expandAll();
      });
    });
  }

  // ---- Card editor modal --------------------------------------------------
  var cardModal = $("#card-modal");
  var cardForm = $("#card-form");
  var editing = null; // { cardId | null, frameId }

  function allCards() {
    var out = [];
    for (var id in state.cards) {
      if (Object.prototype.hasOwnProperty.call(state.cards, id)) out.push(state.cards[id]);
    }
    return out;
  }

  function openCardModal(frameId, card) {
    editing = { cardId: card ? card.id : null, frameId: frameId };

    $("#card-title").value = card ? card.title : "";
    $("#card-text").value = card ? card.text || "" : "";
    $("#card-tags").value = card ? (card.tags || []).join(", ") : "";
    $("#card-system").value = card ? card.system || "" : "";

    // Status select — the tree view shows status badges, so it edits them too.
    var statusSelect = $("#card-status");
    if (statusSelect) {
      statusSelect.value = card && card.status ? card.status : "open";
    }

    // Parent-card dropdown: every card on the board except the card being
    // edited and its own descendants (a card cannot be its own ancestor).
    var parentSelect = $("#card-parent");
    if (parentSelect) {
      var currentParentId = card && card.parent_id ? card.parent_id : "";
      var banned = {};
      if (card) {
        banned[card.id] = true;
        (function markDescendants(node) {
          var kids = node.children || [];
          for (var i = 0; i < kids.length; i++) {
            banned[kids[i].id] = true;
            markDescendants(kids[i]);
          }
        })(card);
      }
      var optionsHtml = '<option value="">— None —</option>';
      allCards().forEach(function (c) {
        if (banned[c.id]) return;
        var selected = c.id === currentParentId ? " selected" : "";
        optionsHtml +=
          '<option value="' + escHtml(c.id) + '"' + selected + ">" + escHtml(c.title) + "</option>";
      });
      parentSelect.innerHTML = optionsHtml;
    }

    // Frame select: moving a card to another frame is the tree view's
    // equivalent of drag & drop on the board.
    var frameSelect = $("#card-frame");
    if (frameSelect) {
      var currentFrameId = card && card.frame_id ? card.frame_id : frameId;
      var fOpts = "";
      state.frames.forEach(function (f) {
        var sel = f.frame_id === currentFrameId ? " selected" : "";
        fOpts += '<option value="' + escHtml(f.frame_id) + '"' + sel + ">" + escHtml(f.frame_title) + "</option>";
      });
      frameSelect.innerHTML = fOpts;
    }

    // Existing cards show their read-only ID, so it can be copied without
    // leaving the editor. New cards have no ID yet, so the field stays hidden.
    var idField = $("#card-id-field");
    var idInput = $("#card-id");
    if (idField && idInput) {
      var hasId = Boolean(card && card.id);
      idInput.value = hasId ? card.id : "";
      idField.hidden = !hasId;
    }

    var dates = $("#card-dates");
    if (dates) {
      var created = fmtDate(card && card.created_at);
      var updated = fmtDate(card && card.updated_at);
      if (created || updated) {
        dates.textContent =
          "Created: " + (created || "unknown") + "   Updated: " + (updated || "unknown");
        dates.hidden = false;
      } else {
        dates.hidden = true;
      }
    }

    $("#card-modal-title").textContent = card ? "Edit card" : "New card";
    cardModal.classList.remove("hidden");
    $("#card-title").focus();
  }

  function closeCardModal() {
    cardModal.classList.add("hidden");
    editing = null;
  }

  if (cardModal) {
    cardModal.addEventListener("click", function (e) {
      if (e.target.hasAttribute("data-close")) closeCardModal();
    });
  }

  var cardIdCopyBtn = $("#card-id-copy");
  if (cardIdCopyBtn && window.CardId) {
    cardIdCopyBtn.addEventListener("click", function () {
      var value = $("#card-id").value;
      CardId.copyText(value, navigator, document).then(function (ok) {
        var previous = cardIdCopyBtn.textContent;
        cardIdCopyBtn.textContent = ok ? "Copied" : "Copy failed";
        setTimeout(function () {
          cardIdCopyBtn.textContent = previous;
        }, 1200);
      });
    });
  }

  if (cardForm) {
    cardForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var title = $("#card-title").value.trim();
      var text = $("#card-text").value.trim();
      var tags = $("#card-tags")
        .value.split(",")
        .map(function (t) {
          return t.trim();
        })
        .filter(Boolean);
      var system = $("#card-system").value.trim();
      var status = ($("#card-status") && $("#card-status").value) || "open";
      var parentId = ($("#card-parent") && $("#card-parent").value) || "";
      var frameId = ($("#card-frame") && $("#card-frame").value) || (editing && editing.frameId);
      if (!title) return;

      var request;
      if (editing && editing.cardId) {
        request = BoardApi.Api.updateCard(editing.cardId, {
          title: title,
          text: text,
          tags: tags,
          system: system,
          status: status,
          parent_id: parentId
        }).then(function () {
          // Frame changes go through the move endpoint: it keeps the frame's
          // card order in sync and tells Agent1's sync pipeline (the PUT
          // payload has no ordering semantics).
          var card = state.cards[editing.cardId];
          if (card && frameId && frameId !== card.frame_id) {
            return BoardApi.Api.moveCard(editing.cardId, frameId, null);
          }
        });
      } else {
        request = BoardApi.Api.createCard(frameId, {
          title: title,
          text: text,
          tags: tags,
          system: system,
          status: status,
          parent_id: parentId
        });
      }

      request
        .then(function () {
          closeCardModal();
          reload();
        })
        ["catch"](function (err) {
          alert(err.message);
        });
    });
  }

  // ---- Card tools (event delegation: the tree is re-rendered as string HTML)
  function cardIdFromEvent(e) {
    var btn = e.target.closest ? e.target.closest("[data-card-id]") : null;
    return btn ? btn.getAttribute("data-card-id") : null;
  }

  if (treeRoot) {
    treeRoot.addEventListener("click", function (e) {
      // Copy button: copy the card ID, confirm briefly.
      var copyBtn = e.target.closest ? e.target.closest(".card-copy") : null;
      if (copyBtn) {
        e.stopPropagation();
        var id = copyBtn.parentNode.querySelector(".card-id").textContent;
        if (window.CardId) {
          CardId.copyText(id, navigator, document).then(function (ok) {
            copyBtn.title = ok ? "Copied" : "Copy failed";
            setTimeout(function () {
              copyBtn.title = "Copy card ID";
            }, 1200);
          });
        }
        return;
      }

      var workBtn = e.target.closest ? e.target.closest(".card-work") : null;
      if (workBtn) {
        var workId = workBtn.getAttribute("data-card-id");
        var workCard = state.cards[workId];
        if (!workCard) return;
        BoardApi.Api.workCard(workId)
          .then(function () {
            alert('Sent "' + workCard.title + '" to Agent1.');
          })
          ["catch"](function (err) {
            alert(err.message);
          });
        return;
      }

      var editBtn = e.target.closest ? e.target.closest(".card-edit") : null;
      if (editBtn) {
        var cardId = editBtn.getAttribute("data-card-id");
        var card = state.cards[cardId];
        if (card) openCardModal(card.frame_id, card);
        return;
      }

      var delBtn = e.target.closest ? e.target.closest(".card-del") : null;
      if (delBtn) {
        var delId = delBtn.getAttribute("data-card-id");
        var target = state.cards[delId];
        if (!target) return;
        if (!confirm('Delete card "' + target.title + '"?')) return;
        BoardApi.Api.deleteCard(delId)
          .then(reload)
          ["catch"](function (err) {
            alert(err.message);
          });
      }
    });
  }

  // ---- Search + filters ---------------------------------------------------
  var searchInput = $("#search");
  if (searchInput) {
    searchInput.addEventListener("input", function () {
      state.query = searchInput.value;
      renderTree();
    });
  }

  var DATE_FILTER_INPUTS = [
    ["filter-created-from", "createdFrom"],
    ["filter-created-to", "createdTo"],
    ["filter-updated-from", "updatedFrom"],
    ["filter-updated-to", "updatedTo"]
  ];

  DATE_FILTER_INPUTS.forEach(function (pair) {
    var input = document.getElementById(pair[0]);
    if (!input) return;
    input.addEventListener("change", function () {
      state.filters[pair[1]] = input.value || "";
      renderTree();
    });
  });

  var integratedFilterBtn = $("#filter-integrated");
  if (integratedFilterBtn) {
    integratedFilterBtn.addEventListener("click", function () {
      state.filters.integrated = !state.filters.integrated;
      renderTree();
    });
  }

  function renderIntegratedFilter() {
    if (!integratedFilterBtn) return;
    var on = Boolean(state.filters.integrated);
    integratedFilterBtn.setAttribute("aria-pressed", String(on));
    integratedFilterBtn.classList.toggle("active", on);
  }

  var showDescriptionsBtn = $("#show-descriptions");
  if (showDescriptionsBtn) {
    showDescriptionsBtn.addEventListener("click", function () {
      state.showDescriptions = !state.showDescriptions;
      localStorage.setItem("kanban.showDescriptions", state.showDescriptions ? "1" : "0");
      renderTree();
    });
  }

  function renderDescriptionFilter() {
    if (!showDescriptionsBtn) return;
    var on = Boolean(state.showDescriptions);
    showDescriptionsBtn.setAttribute("aria-pressed", String(on));
    showDescriptionsBtn.classList.toggle("active", on);
  }

  var clearFiltersBtn = $("#clear-filters");
  if (clearFiltersBtn) {
    clearFiltersBtn.addEventListener("click", function () {
      state.query = "";
      if (searchInput) searchInput.value = "";
      DATE_FILTER_INPUTS.forEach(function (pair) {
        state.filters[pair[1]] = "";
        var input = document.getElementById(pair[0]);
        if (input) input.value = "";
      });
      state.filters.tags = [];
      state.filters.integrated = false;
      renderTree();
    });
  }

  // One chip per tag used anywhere on the board; clicking a chip toggles it.
  function renderTagFilter() {
    var box = $("#tag-filter");
    if (!box) return;
    var tags = [];
    allCards().forEach(function (c) {
      (c.tags || []).forEach(function (t) {
        if (t && t.trim() && tags.indexOf(t) === -1) tags.push(t);
      });
    });
    state.filters.tags = state.filters.tags.filter(function (t) {
      return tags.indexOf(t) !== -1;
    });
    tags.sort(function (a, b) {
      return a.localeCompare(b);
    });
    box.replaceChildren ? box.replaceChildren() : (box.innerHTML = "");
    if (!tags.length) return;

    var label = document.createElement("span");
    label.className = "filter-label";
    label.textContent = "Tags:";
    box.appendChild(label);

    tags.forEach(function (tag) {
      var chip = document.createElement("button");
      chip.className = "tag-chip";
      chip.type = "button";
      chip.textContent = tag;
      chip.setAttribute("aria-pressed", String(state.filters.tags.indexOf(tag) !== -1));
      chip.addEventListener("click", function () {
        var i = state.filters.tags.indexOf(tag);
        if (i >= 0) state.filters.tags.splice(i, 1);
        else state.filters.tags.push(tag);
        renderTree();
      });
      box.appendChild(chip);
    });
  }

  // ---- Add card -----------------------------------------------------------
  var addCardBtn = $("#add-card");
  if (addCardBtn) {
    addCardBtn.addEventListener("click", function () {
      var frame = state.frames.length ? state.frames[0] : null;
      if (!frame) {
        alert("Create a frame on the board first.");
        return;
      }
      openCardModal(frame.frame_id, null);
    });
  }

  // ---- Boot ---------------------------------------------------------------
  reload();

  // Expose what the inline markup calls (onclick="TreeApp.toggleChildren")
  // and the tests.
  window.TreeApp = {
    state: state,
    cardVisible: cardVisible,
    nodeVisible: nodeVisible,
    renderTree: renderTree,
    reload: reload,
    toggleChildren: toggleChildren,
    expandAll: expandAll,
    collapseAll: collapseAll,
    openCardModal: openCardModal,
    closeCardModal: closeCardModal
  };
})();
