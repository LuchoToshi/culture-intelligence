// Culture Intelligence interaction layer: keyboard row navigation, hover/focus
// cross-highlighting, pin/watch/peek on the signal registry, the pin-compare
// panel with drag-reorder, and saved views. No framework, no build step --
// this file is loaded as-is. It is a no-op on pages with no `table.index`.
(function () {
  "use strict";

  var STORE = { pinned: "ci-pinned", watch: "ci-watchlist", views: "ci-saved-views" };
  var MAX_PINNED = 3;

  function readStore(key, fallback) {
    try {
      var raw = window.localStorage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch (e) {
      return fallback;
    }
  }
  function writeStore(key, value) {
    try {
      window.localStorage.setItem(key, JSON.stringify(value));
    } catch (e) {
      /* private browsing or storage full: state just doesn't persist */
    }
  }
  function getPinned() { return readStore(STORE.pinned, []); }
  function setPinned(ids) { writeStore(STORE.pinned, ids); }
  function getWatch() { return readStore(STORE.watch, []); }
  function setWatch(ids) { writeStore(STORE.watch, ids); }
  function getViews() { return readStore(STORE.views, []); }
  function setViews(views) { writeStore(STORE.views, views); }

  // ---- 1. Keyboard row navigation (every table.index on the page) --------
  function initKeynav() {
    var tables = document.querySelectorAll("table.index");
    if (!tables.length) return;

    tables.forEach(function (table) {
      var rows = table.querySelectorAll("tbody tr[data-row-id]");
      rows.forEach(function (row, i) {
        row.tabIndex = i === 0 ? 0 : -1;
      });
    });

    function focusableRows(table) {
      return Array.prototype.slice.call(table.querySelectorAll("tbody tr[data-row-id]"));
    }
    function moveFocus(table, from, delta) {
      var rows = focusableRows(table);
      if (!rows.length) return;
      var idx = rows.indexOf(from);
      var next = rows[Math.max(0, Math.min(rows.length - 1, idx + delta))];
      rows.forEach(function (r) { r.tabIndex = -1; });
      next.tabIndex = 0;
      next.focus();
    }

    document.addEventListener("keydown", function (ev) {
      var target = ev.target;
      var tag = target.tagName;
      if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;

      var row = target.closest && target.closest("tr[data-row-id]");
      var table = row && row.closest("table.index");
      if (!row || !table) return;

      if (ev.key === "j" || ev.key === "ArrowDown") {
        ev.preventDefault();
        moveFocus(table, row, 1);
      } else if (ev.key === "k" || ev.key === "ArrowUp") {
        ev.preventDefault();
        moveFocus(table, row, -1);
      } else if (ev.key === "Enter") {
        var link = row.querySelector(".cell-primary a");
        if (link) link.click();
      } else if (ev.key === "p") {
        var pinBtn = row.querySelector(".pin-btn");
        if (pinBtn) pinBtn.click();
      } else if (ev.key === "w") {
        var watchBtn = row.querySelector(".watch-btn");
        if (watchBtn) watchBtn.click();
      } else if (ev.key === "+") {
        var peekBtn = row.querySelector(".peek-btn");
        if (peekBtn) peekBtn.click();
      }
    });
  }

  // ---- 2. Pin / watch / peek (registry rows only: table has .pin-btn) ----
  var peekCache = {};

  function reflectToggleState(row) {
    var id = row.getAttribute("data-row-id");
    var pinBtn = row.querySelector(".pin-btn");
    var watchBtn = row.querySelector(".watch-btn");
    if (pinBtn) {
      var pinned = getPinned().indexOf(id) !== -1;
      pinBtn.setAttribute("aria-pressed", String(pinned));
      pinBtn.textContent = pinned ? "●" : "○";
    }
    if (watchBtn) {
      var watched = getWatch().indexOf(id) !== -1;
      watchBtn.setAttribute("aria-pressed", String(watched));
      watchBtn.textContent = watched ? "⚑" : "⚐";
    }
  }

  function togglePin(id) {
    var pinned = getPinned();
    var at = pinned.indexOf(id);
    if (at !== -1) {
      pinned.splice(at, 1);
    } else if (pinned.length < MAX_PINNED) {
      pinned.push(id);
    } else {
      return; // full: ignore rather than silently evicting a slot
    }
    setPinned(pinned);
  }

  function toggleWatch(id) {
    var watched = getWatch();
    var at = watched.indexOf(id);
    if (at !== -1) watched.splice(at, 1);
    else watched.push(id);
    setWatch(watched);
  }

  function fetchPeek(id) {
    if (peekCache[id]) return Promise.resolve(peekCache[id]);
    return fetch("/signals/" + id + "/peek")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        peekCache[id] = data.peek;
        return data.peek;
      });
  }

  function renderPeek(cell, items) {
    cell.textContent = "";
    if (!items.length) {
      var empty = document.createElement("p");
      empty.className = "muted";
      empty.textContent = "No evidence yet.";
      cell.appendChild(empty);
      return;
    }
    var list = document.createElement("ul");
    list.className = "peek-list";
    items.forEach(function (item) {
      var li = document.createElement("li");
      var when = document.createElement("span");
      when.className = "mono muted";
      when.textContent = item.when;
      li.appendChild(when);
      li.appendChild(document.createTextNode(" " + item.source_name + ": " + item.title));
      list.appendChild(li);
    });
    cell.appendChild(list);
  }

  function initPinWatchPeek() {
    var rows = document.querySelectorAll("table.index tbody tr[data-row-id]");
    rows.forEach(reflectToggleState);

    document.addEventListener("click", function (ev) {
      var row = ev.target.closest && ev.target.closest("tr[data-row-id]");
      if (!row) return;
      var id = row.getAttribute("data-row-id");

      if (ev.target.classList.contains("pin-btn")) {
        togglePin(id);
        reflectToggleState(row);
        renderComparePanel();
      } else if (ev.target.classList.contains("watch-btn")) {
        toggleWatch(id);
        reflectToggleState(row);
      } else if (ev.target.classList.contains("peek-btn")) {
        var expanded = ev.target.getAttribute("aria-expanded") === "true";
        var peekRow = row.parentElement.querySelector('tr.peek-row[data-peek-for="' + id + '"]');
        if (!peekRow) return;
        if (expanded) {
          peekRow.hidden = true;
          ev.target.setAttribute("aria-expanded", "false");
          return;
        }
        ev.target.setAttribute("aria-expanded", "true");
        peekRow.hidden = false;
        var cell = peekRow.querySelector("td");
        cell.textContent = "Loading...";
        fetchPeek(id).then(function (items) { renderPeek(cell, items); });
      }
    });
  }

  // ---- 3. Cross-highlight (city / source, delegated per table) -----------
  function initCrossHighlight() {
    document.querySelectorAll("table.index").forEach(function (table) {
      function apply(target, on) {
        var attr = target.hasAttribute("data-city") ? "city" : "source";
        var value = target.getAttribute("data-" + attr);
        if (!value) return;
        var selector = "[data-" + attr + '="' + value.replace(/"/g, '\\"') + '"]';
        table.querySelectorAll(selector).forEach(function (el) {
          el.classList.toggle("xh", on);
        });
      }
      table.addEventListener("mouseover", function (ev) {
        var t = ev.target.closest("[data-city], [data-source]");
        if (t) apply(t, true);
      });
      table.addEventListener("mouseout", function (ev) {
        var t = ev.target.closest("[data-city], [data-source]");
        if (t) apply(t, false);
      });
      table.addEventListener("focusin", function (ev) {
        var t = ev.target.closest("[data-city], [data-source]");
        if (t) apply(t, true);
      });
      table.addEventListener("focusout", function (ev) {
        var t = ev.target.closest("[data-city], [data-source]");
        if (t) apply(t, false);
      });
    });
  }

  // ---- 4. Compare panel + drag-reorder ------------------------------------
  function buildMeter(value, accent) {
    var span = document.createElement("span");
    span.className = "meter" + (accent && value >= 4 ? " hot" : "");
    if (!value) {
      span.className = "muted";
      span.textContent = "-";
      return span;
    }
    span.setAttribute("role", "img");
    span.setAttribute("aria-label", value + " of 5");
    var cells = document.createElement("span");
    cells.className = "cells";
    cells.setAttribute("aria-hidden", "true");
    for (var i = 0; i < 5; i++) {
      var cell = document.createElement("span");
      cell.className = "cell" + (i < value ? " on" : "");
      cells.appendChild(cell);
    }
    span.appendChild(cells);
    return span;
  }

  function findRowData(id) {
    var row = document.querySelector('table.index tbody tr[data-row-id="' + id + '"]');
    if (!row) return null;
    return {
      id: id,
      name: row.getAttribute("data-name"),
      edi: parseInt(row.getAttribute("data-edi"), 10) || 0,
      ado: parseInt(row.getAttribute("data-ado"), 10) || 0,
      sat: parseInt(row.getAttribute("data-sat"), 10) || 0,
    };
  }

  function makeReorderable(container, onReorder) {
    var dragged = null;
    container.addEventListener("dragstart", function (ev) {
      var slot = ev.target.closest("[data-slot]");
      if (!slot) return;
      dragged = slot;
      ev.dataTransfer.effectAllowed = "move";
    });
    container.addEventListener("dragover", function (ev) {
      var slot = ev.target.closest("[data-slot]");
      if (!slot || slot === dragged) return;
      ev.preventDefault();
      container.querySelectorAll(".insert-mark").forEach(function (m) { m.remove(); });
      var mark = document.createElement("div");
      mark.className = "insert-mark";
      slot.parentElement.insertBefore(mark, slot);
    });
    container.addEventListener("drop", function (ev) {
      ev.preventDefault();
      container.querySelectorAll(".insert-mark").forEach(function (m) { m.remove(); });
      var target = ev.target.closest("[data-slot]");
      if (!target || !dragged || target === dragged) return;
      var order = Array.prototype.map.call(container.querySelectorAll("[data-slot]"), function (s) { return s.dataset.slot; });
      var from = order.indexOf(dragged.dataset.slot);
      var to = order.indexOf(target.dataset.slot);
      order.splice(to, 0, order.splice(from, 1)[0]);
      onReorder(order);
      dragged = null;
    });
    container.addEventListener("dragend", function () {
      container.querySelectorAll(".insert-mark").forEach(function (m) { m.remove(); });
      dragged = null;
    });
    container.addEventListener("click", function (ev) {
      var btn = ev.target.closest("[data-move]");
      if (!btn) return;
      var slot = btn.closest("[data-slot]");
      var order = Array.prototype.map.call(container.querySelectorAll("[data-slot]"), function (s) { return s.dataset.slot; });
      var idx = order.indexOf(slot.dataset.slot);
      var to = idx + (btn.dataset.move === "left" ? -1 : 1);
      if (to < 0 || to >= order.length) return;
      order.splice(to, 0, order.splice(idx, 1)[0]);
      onReorder(order);
    });
  }

  function renderComparePanel() {
    var panel = document.getElementById("compare-panel");
    if (!panel) return;
    var pinned = getPinned();
    if (!pinned.length) {
      panel.hidden = true;
      panel.innerHTML = "";
      return;
    }
    panel.hidden = false;
    panel.innerHTML = "";
    var heading = document.createElement("div");
    heading.className = "compare-heading";
    heading.textContent = "Comparing " + pinned.length + " signal" + (pinned.length !== 1 ? "s" : "");
    panel.appendChild(heading);

    var slots = document.createElement("div");
    slots.className = "compare-slots";
    pinned.forEach(function (id, i) {
      var data = findRowData(id);
      var slot = document.createElement("div");
      slot.className = "compare-slot";
      slot.setAttribute("draggable", "true");
      slot.dataset.slot = id;

      var name = document.createElement("div");
      name.className = "compare-name";
      if (data) {
        var link = document.createElement("a");
        link.href = "/signals/" + id;
        link.textContent = data.name;
        name.appendChild(link);
      } else {
        var span = document.createElement("span");
        span.className = "muted";
        span.textContent = "Signal #" + id + " (not shown in this filter)";
        name.appendChild(span);
      }
      slot.appendChild(name);

      if (data) {
        [["Editorial", data.edi, false], ["Adoption", data.ado, false], ["Saturation", data.sat, true]].forEach(function (row) {
          var line = document.createElement("div");
          line.className = "compare-row";
          var label = document.createElement("span");
          label.textContent = row[0];
          line.appendChild(label);
          line.appendChild(buildMeter(row[1], row[2]));
          slot.appendChild(line);
        });
      }

      var moves = document.createElement("div");
      moves.className = "compare-moves";
      if (i > 0) {
        var left = document.createElement("button");
        left.type = "button";
        left.dataset.move = "left";
        left.setAttribute("aria-label", "Move earlier");
        left.textContent = "◂";
        moves.appendChild(left);
      }
      var unpin = document.createElement("button");
      unpin.type = "button";
      unpin.className = "unpin";
      unpin.textContent = "Unpin";
      unpin.addEventListener("click", function () {
        togglePin(id);
        var row = document.querySelector('table.index tbody tr[data-row-id="' + id + '"]');
        if (row) reflectToggleState(row);
        renderComparePanel();
      });
      moves.appendChild(unpin);
      if (i < pinned.length - 1) {
        var right = document.createElement("button");
        right.type = "button";
        right.dataset.move = "right";
        right.setAttribute("aria-label", "Move later");
        right.textContent = "▸";
        moves.appendChild(right);
      }
      slot.appendChild(moves);
      slots.appendChild(slot);
    });
    panel.appendChild(slots);

    makeReorderable(slots, function (order) {
      setPinned(order);
      renderComparePanel();
    });
  }

  // ---- 5. Saved views ------------------------------------------------------
  function renderSavedViews() {
    var mount = document.getElementById("saved-views");
    if (!mount) return;
    var views = getViews();
    mount.innerHTML = "";

    if (views.length) {
      var list = document.createElement("div");
      list.className = "views-list";
      views.forEach(function (view, i) {
        var chip = document.createElement("div");
        chip.className = "view-chip";
        chip.setAttribute("draggable", "true");
        chip.dataset.slot = String(i);

        var link = document.createElement("a");
        link.href = "/signals?" + view.query;
        link.textContent = view.name;
        chip.appendChild(link);

        var moves = document.createElement("span");
        moves.className = "view-moves";
        if (i > 0) {
          var left = document.createElement("button");
          left.type = "button"; left.dataset.move = "left"; left.textContent = "◂";
          left.setAttribute("aria-label", "Move " + view.name + " earlier");
          moves.appendChild(left);
        }
        var remove = document.createElement("button");
        remove.type = "button";
        remove.textContent = "×";
        remove.setAttribute("aria-label", "Remove saved view " + view.name);
        remove.addEventListener("click", function () {
          var next = getViews().slice();
          next.splice(i, 1);
          setViews(next);
          renderSavedViews();
        });
        moves.appendChild(remove);
        if (i < views.length - 1) {
          var right = document.createElement("button");
          right.type = "button"; right.dataset.move = "right"; right.textContent = "▸";
          right.setAttribute("aria-label", "Move " + view.name + " later");
          moves.appendChild(right);
        }
        chip.appendChild(moves);
        list.appendChild(chip);
      });
      mount.appendChild(list);
      makeReorderable(list, function (order) {
        var next = order.map(function (i) { return views[parseInt(i, 10)]; });
        setViews(next);
        renderSavedViews();
      });
    }

    var form = document.createElement("form");
    form.className = "save-view-form";
    form.innerHTML =
      '<input type="text" class="plain" placeholder="Name this view" maxlength="40" required>' +
      '<button type="submit">Save current view</button>';
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var input = form.querySelector("input");
      var name = input.value.trim();
      if (!name) return;
      var next = getViews().concat([{ name: name, query: mount.dataset.currentQuery }]);
      setViews(next);
      input.value = "";
      renderSavedViews();
    });
    mount.appendChild(form);
  }

  function init() {
    if (!document.querySelector("table.index")) return;
    initKeynav();
    initPinWatchPeek();
    initCrossHighlight();
    renderComparePanel();
    renderSavedViews();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
