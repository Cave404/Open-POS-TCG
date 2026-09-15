/**
 * OpenPOS-TCG: Inventory Management Client Engine
 * Handles data filtration, pagination, hardware token binding (HID Wedge),
 * and thermal sleeve label printing.
 */

(() => {
  "use strict";

  // State
  let currentPage = 1;
  let totalPages = 1;
  let activeEditItem = null;
  let wedgeBuffer = "";
  let wedgeTimer = null;

  // DOM Elements
  const searchInput = document.getElementById("inv-search-input");
  const filterGame = document.getElementById("filter-game");
  const filterSet = document.getElementById("filter-set");
  const filterCondition = document.getElementById("filter-condition");
  const filterFinish = document.getElementById("filter-finish");
  const filterInStock = document.getElementById("filter-in-stock");
  const btnRefresh = document.getElementById("btn-refresh-inv");

  const tableBody = document.getElementById("inv-table-body");
  const emptyState = document.getElementById("inv-empty-state");
  const paginationInfo = document.getElementById("pagination-info");
  const btnPrevPage = document.getElementById("btn-prev-page");
  const btnNextPage = document.getElementById("btn-next-page");

  // Tag Modal Elements
  const tagModal = document.getElementById("tag-modal");
  const tagItemTitle = document.getElementById("tag-item-title");
  const tagInputVal = document.getElementById("tag-input-val");
  const btnSaveTag = document.getElementById("btn-save-tag");
  const btnCloseTagModal = document.getElementById("btn-close-tag-modal");
  const tagStatusMessage = document.getElementById("tag-status-message");

  // Label Modal Elements
  const labelModal = document.getElementById("label-modal");
  const labelPreviewArea = document.getElementById("label-preview-area");
  const btnTriggerPrint = document.getElementById("btn-trigger-print");
  const btnCloseLabelModal = document.getElementById("btn-close-label-modal");

  // Edit Modal Elements
  const editModal = document.getElementById("edit-modal");
  const editItemTitle = document.getElementById("edit-item-title");
  const editInputPrice = document.getElementById("edit-input-price");
  const editInputQty = document.getElementById("edit-input-qty");
  const editInputSku = document.getElementById("edit-input-sku");
  const btnSaveEdit = document.getElementById("btn-save-edit");
  const btnCloseEditModal = document.getElementById("btn-close-edit-modal");

  function fmt(val) {
    if (val === null || val === undefined || isNaN(val)) return "$0.00";
    return "$" + Number(val).toFixed(2);
  }

  // --- Data Loading ---

  async function loadInventory(page = 1) {
    currentPage = page;
    const params = new URLSearchParams({
      page: currentPage,
      limit: 25,
      game: filterGame.value,
      set_code: filterSet.value.trim().toLowerCase(),
      condition: filterCondition.value,
      finish: filterFinish.value,
      search: searchInput.value.trim(),
      in_stock_only: filterInStock.checked ? "true" : "false"
    });

    try {
      tableBody.innerHTML = `<tr><td colspan="10" style="text-align: center; padding: 2rem; color: var(--text-dim);">Loading inventory records...</td></tr>`;
      const resp = await fetch(`/tcg/api/inventory?${params.toString()}`);
      const data = await resp.json();

      if (data.success) {
        totalPages = data.total_pages || 1;
        renderTable(data.items, data.total);
        updatePagination(data.page, data.total_pages, data.total);
      } else {
        tableBody.innerHTML = `<tr><td colspan="10" style="text-align: center; color: var(--accent-red); padding: 2rem;">Error: ${data.error}</td></tr>`;
      }
    } catch (err) {
      console.error("Inventory fetch error:", err);
      tableBody.innerHTML = `<tr><td colspan="10" style="text-align: center; color: var(--accent-red); padding: 2rem;">Failed to fetch inventory from server.</td></tr>`;
    }
  }

  function renderTable(items, total) {
    tableBody.innerHTML = "";

    if (!items || items.length === 0) {
      emptyState.style.display = "block";
      return;
    }
    emptyState.style.display = "none";

    items.forEach((item) => {
      const tr = document.createElement("tr");
      const thumb = item.image_uri || "/tcg/addons/tcg_pos/static/placeholder_card.png";
      const skuDisplay = item.sku || `TCG-${item.id}`;
      const tagDisplay = item.custom_tag_id
        ? `<span class="tag-pill" title="Hardware Token">${item.custom_tag_id}</span>`
        : `<span class="tag-empty">Unassigned</span>`;

      tr.innerHTML = `
        <td>
          <img class="table-thumb" src="${thumb}" alt="${item.name}" onerror="this.src='/tcg/addons/tcg_pos/static/placeholder_card.png'" />
        </td>
        <td>
          <strong>${item.name}</strong>
          <div style="font-size: 0.72rem; color: var(--text-dim); font-family: monospace;">SKU: ${skuDisplay}</div>
        </td>
        <td>
          <span class="badge badge-set">${item.set_code.toUpperCase()}</span>
          <span style="font-size: 0.8rem; color: var(--text-muted); margin-left: 0.2rem;">#${item.collector_number}</span>
        </td>
        <td><span style="text-transform: capitalize;">${item.finish}</span></td>
        <td><strong style="color: ${getCondColor(item.condition)};">${item.condition}</strong></td>
        <td>
          <span style="font-weight: 700; font-size: 0.95rem; color: ${item.quantity > 0 ? 'var(--text-main)' : 'var(--accent-red)'};">
            ${item.quantity}
          </span>
        </td>
        <td>${fmt(item.cost_basis)}</td>
        <td><strong>${fmt(item.sell_price)}</strong></td>
        <td>${tagDisplay}</td>
        <td>
          <div class="actions-cell">
            <button class="btn-icon btn-edit-action" title="Quick Edit" data-id="${item.id}">&#9998;</button>
            <button class="btn-icon btn-tag-action" title="Bind NFC / Barcode Tag" data-id="${item.id}">&#127991;</button>
            <button class="btn-icon btn-print-action" title="Print Sleeve Label" data-id="${item.id}">&#128424;</button>
            <button class="btn-icon btn-delete-action" title="Delete SKU" data-id="${item.id}">&times;</button>
          </div>
        </td>
      `;

      // Event handlers
      tr.querySelector(".btn-edit-action").onclick = () => openEditModal(item);
      tr.querySelector(".btn-tag-action").onclick = () => openTagModal(item);
      tr.querySelector(".btn-print-action").onclick = () => openLabelModal(item);
      tr.querySelector(".btn-delete-action").onclick = () => deleteItem(item);

      tableBody.appendChild(tr);
    });
  }

  function getCondColor(cond) {
    switch (cond) {
      case "NM": return "#34d399";
      case "LP": return "#60a5fa";
      case "MP": return "#fbbf24";
      case "HP": return "#fb923c";
      case "DMG": return "#f87171";
      default: return "#f8fafc";
    }
  }

  function updatePagination(page, maxPages, total) {
    paginationInfo.textContent = `Page ${page} of ${maxPages} (${total} total records)`;
    btnPrevPage.disabled = page <= 1;
    btnNextPage.disabled = page >= maxPages;
  }

  // --- Quick Edit Modal ---

  function openEditModal(item) {
    activeEditItem = item;
    editItemTitle.textContent = `${item.name} (${item.set_code.toUpperCase()} #${item.collector_number} ${item.condition}/${item.finish})`;
    editInputPrice.value = item.sell_price.toFixed(2);
    editInputQty.value = item.quantity;
    editInputSku.value = item.sku || "";
    editModal.style.display = "flex";
  }

  async function saveEdit() {
    if (!activeEditItem) return;

    const payload = {
      sell_price: parseFloat(editInputPrice.value) || 0.0,
      quantity: parseInt(editInputQty.value) || 0,
      sku: editInputSku.value.trim() || null
    };

    try {
      const resp = await fetch(`/tcg/api/inventory/${activeEditItem.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await resp.json();

      if (data.success) {
        editModal.style.display = "none";
        loadInventory(currentPage);
      } else {
        alert("Update failed: " + (data.error || "Unknown error"));
      }
    } catch (err) {
      console.error("Save edit error:", err);
      alert("Network error updating item.");
    }
  }

  // --- Hardware Tag / Wedge Listener Modal ---

  function openTagModal(item) {
    activeEditItem = item;
    tagItemTitle.textContent = `${item.name} (${item.set_code.toUpperCase()} #${item.collector_number})`;
    tagInputVal.value = item.custom_tag_id || "";
    tagStatusMessage.textContent = "Listening for NFC tap or barcode scanner wedge...";
    tagStatusMessage.style.color = "var(--text-muted)";
    tagModal.style.display = "flex";

    // Focus input so scanner keystrokes land here
    tagInputVal.focus();
    tagInputVal.select();
  }

  async function saveTag() {
    if (!activeEditItem) return;

    const tagVal = tagInputVal.value.trim() || null;
    try {
      const resp = await fetch(`/tcg/api/inventory/${activeEditItem.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ custom_tag_id: tagVal })
      });
      const data = await resp.json();

      if (data.success) {
        tagModal.style.display = "none";
        loadInventory(currentPage);
      } else {
        tagStatusMessage.textContent = data.error || "Error binding tag.";
        tagStatusMessage.style.color = "var(--accent-red)";
      }
    } catch (err) {
      console.error("Save tag error:", err);
      tagStatusMessage.textContent = "Network error communicating with server.";
      tagStatusMessage.style.color = "var(--accent-red)";
    }
  }

  // --- Label Preview & Thermal Print ---

  async function openLabelModal(item) {
    try {
      const resp = await fetch(`/tcg/api/items/${item.id}/label-payload`);
      const payload = await resp.json();

      renderLabelPreview(payload);
      labelModal.style.display = "flex";
    } catch (err) {
      console.error("Failed to load label payload:", err);
      alert("Failed to load label data.");
    }
  }

  function renderLabelPreview(payload) {
    // Generate QR visual code placeholder (scalable vector pattern)
    labelPreviewArea.innerHTML = `
      <div id="printable-label-area" class="sleeve-label-preview">
        <div class="label-qr-col">
          <svg width="70" height="70" viewBox="0 0 100 100" fill="#000">
            <!-- QR code representation -->
            <rect x="5" y="5" width="30" height="30" fill="#000" />
            <rect x="10" y="10" width="20" height="20" fill="#fff" />
            <rect x="15" y="15" width="10" height="10" fill="#000" />
            <rect x="65" y="5" width="30" height="30" fill="#000" />
            <rect x="70" y="10" width="20" height="20" fill="#fff" />
            <rect x="75" y="15" width="10" height="10" fill="#000" />
            <rect x="5" y="65" width="30" height="30" fill="#000" />
            <rect x="10" y="70" width="20" height="20" fill="#fff" />
            <rect x="15" y="75" width="10" height="10" fill="#000" />
            <rect x="42" y="42" width="16" height="16" fill="#000" />
            <rect x="45" y="10" width="10" height="10" fill="#000" />
            <rect x="45" y="75" width="10" height="15" fill="#000" />
            <rect x="75" y="45" width="15" height="10" fill="#000" />
          </svg>
        </div>
        <div class="label-info-col">
          <div>
            <div class="label-card-title">${payload.display_name}</div>
            <div class="label-meta-line">${payload.set_code} #${payload.collector_number} &bull; ${payload.rarity.toUpperCase()}</div>
            <div class="label-meta-line"><strong>${payload.condition}</strong> / ${payload.finish.toUpperCase()}</div>
          </div>
          <div>
            <div class="label-price-line">$${payload.sell_price.toFixed(2)}</div>
            <div class="label-meta-line" style="font-size: 5.5pt; font-family: monospace;">${payload.barcode_payload}</div>
          </div>
        </div>
      </div>
    `;
  }

  // --- Deletion ---

  async function deleteItem(item) {
    if (confirm(`Permanently remove '${item.name}' (${item.condition}/${item.finish}) from inventory?`)) {
      try {
        const resp = await fetch(`/tcg/api/inventory/${item.id}`, { method: "DELETE" });
        const data = await resp.json();
        if (data.success) {
          loadInventory(currentPage);
        } else {
          alert("Delete failed: " + data.error);
        }
      } catch (err) {
        console.error("Delete error:", err);
      }
    }
  }

  // --- Event Listeners & Wedge Handling ---

  document.addEventListener("DOMContentLoaded", () => {
    loadInventory(1);

    // Filter controls
    searchInput.addEventListener("input", () => loadInventory(1));
    filterGame.addEventListener("change", () => loadInventory(1));
    filterSet.addEventListener("input", () => loadInventory(1));
    filterCondition.addEventListener("change", () => loadInventory(1));
    filterFinish.addEventListener("change", () => loadInventory(1));
    filterInStock.addEventListener("change", () => loadInventory(1));
    btnRefresh.addEventListener("click", () => loadInventory(currentPage));

    // Pagination
    btnPrevPage.addEventListener("click", () => {
      if (currentPage > 1) loadInventory(currentPage - 1);
    });
    btnNextPage.addEventListener("click", () => {
      if (currentPage < totalPages) loadInventory(currentPage + 1);
    });

    // Edit modal
    btnSaveEdit.addEventListener("click", saveEdit);
    btnCloseEditModal.addEventListener("click", () => editModal.style.display = "none");

    // Tag modal
    btnSaveTag.addEventListener("click", saveTag);
    btnCloseTagModal.addEventListener("click", () => tagModal.style.display = "none");

    // Label modal
    btnTriggerPrint.addEventListener("click", () => window.print());
    btnCloseLabelModal.addEventListener("click", () => labelModal.style.display = "none");

    // Global HID wedge keystroke capture (when tag modal is open)
    document.addEventListener("keydown", (e) => {
      if (tagModal.style.display === "flex") {
        if (e.key === "Enter") {
          e.preventDefault();
          tagStatusMessage.textContent = "Token captured! Saving...";
          tagStatusMessage.style.color = "var(--accent-green)";
          saveTag();
        } else if (e.key === "Escape") {
          tagModal.style.display = "none";
        }
      } else if (editModal.style.display === "flex" && e.key === "Escape") {
        editModal.style.display = "none";
      } else if (labelModal.style.display === "flex" && e.key === "Escape") {
        labelModal.style.display = "none";
      }
    });

    // Close modals on backdrop click
    [editModal, tagModal, labelModal].forEach((m) => {
      m.addEventListener("click", (e) => {
        if (e.target === m) m.style.display = "none";
      });
    });
  });
})();
