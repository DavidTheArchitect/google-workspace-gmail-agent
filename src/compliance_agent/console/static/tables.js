"use strict";

// Progressive table enhancement: search, column sort, and pagination for any
// table marked data-enhance. Tables render fully server-side; this only helps.
(function () {
  function enhance(table) {
    const body = table.tBodies[0];
    if (!body) {
      return;
    }
    const rows = () =>
      Array.from(body.rows).filter((row) => !row.querySelector(".empty-row"));
    if (rows().length === 0) {
      return;
    }
    const pageSize = Number(table.dataset.pageSize) || 20;
    const state = { query: "", page: 0 };
    const wrap = table.closest(".table-wrap") || table;

    const toolbar = document.createElement("div");
    toolbar.className = "table-toolbar";
    const label = document.createElement("label");
    label.className = "visually-hidden";
    label.textContent = "Filter rows";
    const search = document.createElement("input");
    search.type = "search";
    search.placeholder = "Filter rows…";
    search.setAttribute("aria-label", "Filter table rows");
    label.append(search);
    const count = document.createElement("span");
    count.className = "table-count";
    count.setAttribute("aria-live", "polite");
    const pager = document.createElement("span");
    pager.className = "table-pager";
    const prev = pagerButton("Previous page", "‹");
    const status = document.createElement("span");
    const next = pagerButton("Next page", "›");
    pager.append(prev, status, next);
    toolbar.append(label, search, count, pager);
    wrap.parentNode.insertBefore(toolbar, wrap);

    function pagerButton(name, text) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "button quiet";
      button.setAttribute("aria-label", name);
      button.textContent = text;
      return button;
    }

    function matching() {
      return rows().filter(
        (row) => !state.query || row.textContent.toLowerCase().includes(state.query),
      );
    }

    function render() {
      const visible = matching();
      const pages = Math.max(1, Math.ceil(visible.length / pageSize));
      state.page = Math.min(state.page, pages - 1);
      const start = state.page * pageSize;
      const slice = new Set(visible.slice(start, start + pageSize));
      rows().forEach((row) => row.classList.toggle("row-hidden", !slice.has(row)));
      count.textContent = state.query
        ? `${visible.length} of ${rows().length} rows`
        : `${rows().length} rows`;
      const paged = visible.length > pageSize;
      pager.classList.toggle("row-hidden", !paged);
      if (paged) {
        status.textContent = `Page ${state.page + 1} of ${pages}`;
        prev.disabled = state.page === 0;
        next.disabled = state.page >= pages - 1;
      }
    }

    search.addEventListener("input", () => {
      state.query = search.value.trim().toLowerCase();
      state.page = 0;
      render();
    });
    prev.addEventListener("click", () => {
      state.page -= 1;
      render();
    });
    next.addEventListener("click", () => {
      state.page += 1;
      render();
    });

    Array.from(table.tHead?.rows[0]?.cells || []).forEach((header, index) => {
      const kind = header.dataset.sort;
      if (!kind) {
        return;
      }
      header.tabIndex = 0;
      header.setAttribute("role", "button");
      const sort = () => {
        const ascending = header.getAttribute("aria-sort") !== "ascending";
        Array.from(table.tHead.rows[0].cells).forEach((cell) =>
          cell.removeAttribute("aria-sort"),
        );
        header.setAttribute("aria-sort", ascending ? "ascending" : "descending");
        const keyed = rows().map((row) => {
          const cell = row.cells[index];
          const raw = cell?.dataset.value ?? cell?.textContent.trim() ?? "";
          const key = kind === "num" ? Number(raw) || 0 : raw.toLowerCase();
          return { row, key };
        });
        keyed.sort((a, b) => {
          if (a.key < b.key) {
            return ascending ? -1 : 1;
          }
          if (a.key > b.key) {
            return ascending ? 1 : -1;
          }
          return 0;
        });
        keyed.forEach((entry) => body.append(entry.row));
        state.page = 0;
        render();
      };
      header.addEventListener("click", sort);
      header.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          sort();
        }
      });
    });

    render();
  }

  function init() {
    document.querySelectorAll("table[data-enhance]").forEach(enhance);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
