/**
 * OpenPOS TCG Addon - Register Checkout Client Engine
 * File: static/tcg_pos/js/register.js
 */

(() => {
  "use strict";

  // State
  let cart = [];
  const TAX_RATE = 0.0825; // 8.25% default sales tax

  // DOM Elements
  const searchInput = document.getElementById("registerSearchInput");
  const searchDropdown = document.getElementById("searchAutocompleteDropdown");
  const cartTableBody = document.getElementById("cartTableBody");
  const cartEmptyState = document.getElementById("cartEmptyState");
  const cartItemsCountBadge = document.getElementById("cartItemsCountBadge");
  const subtotalDisplay = document.getElementById("subtotalDisplay");
  const taxDisplay = document.getElementById("taxDisplay");
  const totalDisplay = document.getElementById("totalDisplay");
  const btnCompleteCheckout = document.getElementById("btnCompleteCheckout");
  const btnClearCart = document.getElementById("btnClearCart");
  const hardwareBadge = document.getElementById("hardwareStatusBadge");
  const toastContainer = document.getElementById("toastContainer");

  // ---------------------------------------------------------------------------
  // 1. Toast Notification Helper
  // ---------------------------------------------------------------------------
  function showToast(message, isError = false) {
    if (!toastContainer) return;
    const toast = document.createElement("div");
    toast.className = "toast" + (isError ? " toast-error" : "");
    toast.innerHTML = `
      <span>${isError ? "⚠️" : "✓"}</span>
      <span>${message}</span>
    `;
    toastContainer.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = "0";
      toast.style.transition = "opacity 0.3s ease";
      setTimeout(() => toast.remove(), 300);
    }, 3500);
  }

  // ---------------------------------------------------------------------------
  // 2. Cart Operations
  // ---------------------------------------------------------------------------
  function addToCart(item, qtyToAdd = 1) {
    const existingIndex = cart.findIndex((c) => c.id === item.id);
    if (existingIndex > -1) {
      cart[existingIndex].quantity += qtyToAdd;
    } else {
      cart.push({
        id: item.id,
        name: item.name,
        set_code: item.set_code || "",
        collector_number: item.collector_number || "",
        finish: item.finish || "nonfoil",
        condition: item.condition || "NM",
        sell_price: Number(item.sell_price || 0),
        custom_tag_id: item.custom_tag_id || null,
        available_quantity: Number(item.quantity || 1),
        quantity: qtyToAdd
      });
    }
    renderCart();
  }

  function updateItemQuantity(itemId, newQty) {
    const index = cart.findIndex((c) => c.id === itemId);
    if (index === -1) return;

    if (newQty <= 0) {
      cart.splice(index, 1);
    } else {
      cart[index].quantity = newQty;
    }
    renderCart();
  }

  function renderCart() {
    cartTableBody.innerHTML = "";

    if (cart.length === 0) {
      cartEmptyState.style.display = "block";
      cartItemsCountBadge.textContent = "0 Items";
      btnCompleteCheckout.disabled = true;
      subtotalDisplay.textContent = "$0.00";
      taxDisplay.textContent = "$0.00";
      totalDisplay.textContent = "$0.00";
      return;
    }

    cartEmptyState.style.display = "none";
    btnCompleteCheckout.disabled = false;

    let subtotal = 0;
    let totalItems = 0;

    cart.forEach((item) => {
      const lineTotal = item.sell_price * item.quantity;
      subtotal += lineTotal;
      totalItems += item.quantity;

      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>
          <div class="cart-item-title">${escapeHtml(item.name)}</div>
          <div class="cart-item-meta">
            ${escapeHtml(item.set_code.toUpperCase())} #${escapeHtml(item.collector_number)} &bull;
            <strong>${escapeHtml(item.condition)}</strong> / ${escapeHtml(item.finish.toUpperCase())}
            ${item.custom_tag_id ? `<span class="cart-tag-pill" title="Hardware Token">${escapeHtml(item.custom_tag_id)}</span>` : ""}
          </div>
        </td>
        <td style="font-weight: 600;">$${item.sell_price.toFixed(2)}</td>
        <td>
          <div class="qty-stepper">
            <button type="button" class="stepper-btn btn-qty-minus" data-id="${item.id}">&minus;</button>
            <span class="stepper-val">${item.quantity}</span>
            <button type="button" class="stepper-btn btn-qty-plus" data-id="${item.id}">&plus;</button>
          </div>
        </td>
        <td style="font-weight: 700; color: var(--accent-sky);">$${lineTotal.toFixed(2)}</td>
        <td style="text-align: right;">
          <button type="button" class="btn-remove-item" data-id="${item.id}" title="Remove item">&times;</button>
        </td>
      `;

      tr.querySelector(".btn-qty-minus").onclick = () => updateItemQuantity(item.id, item.quantity - 1);
      tr.querySelector(".btn-qty-plus").onclick = () => updateItemQuantity(item.id, item.quantity + 1);
      tr.querySelector(".btn-remove-item").onclick = () => updateItemQuantity(item.id, 0);

      cartTableBody.appendChild(tr);
    });

    const tax = subtotal * TAX_RATE;
    const total = subtotal + tax;

    cartItemsCountBadge.textContent = `${totalItems} ${totalItems === 1 ? "Item" : "Items"}`;
    subtotalDisplay.textContent = `$${subtotal.toFixed(2)}`;
    taxDisplay.textContent = `$${tax.toFixed(2)}`;
    totalDisplay.textContent = `$${total.toFixed(2)}`;
  }

  function escapeHtml(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  // ---------------------------------------------------------------------------
  // 3. Hardware Scan Resolution Workflow
  // ---------------------------------------------------------------------------
  async function resolveAndAddToCart(identifier) {
    if (!identifier) return;

    try {
      const resp = await fetch(`/tcg/api/resolve?identifier=${encodeURIComponent(identifier)}`);
      const data = await resp.json();

      if (data.found && data.item) {
        if (window.hardwareBridge) window.hardwareBridge.chimeSuccess();
        addToCart(data.item, 1);
        showToast(`Added ${data.item.name} (${data.item.set_code.toUpperCase()}) to cart.`, false);
      } else {
        if (window.hardwareBridge) window.hardwareBridge.chimeError();
        showToast(`No single matching identifier: "${identifier}"`, true);
      }
    } catch (err) {
      console.error("[Register] Item resolution error:", err);
      if (window.hardwareBridge) window.hardwareBridge.chimeError();
      showToast(`Resolution network fault for "${identifier}"`, true);
    }
  }

  // Bind Global Hardware Event Listener
  window.addEventListener("openpos:hardware-scan", (event) => {
    const detail = event.detail || {};
    const token = detail.value;
    console.log("[Register] Hardware scan intercepted:", detail);
    if (token) {
      resolveAndAddToCart(token);
    }
  });

  // Attach hardware bridge status badge
  if (window.hardwareBridge && hardwareBadge) {
    window.hardwareBridge.attachStatusBadge(hardwareBadge);
  }

  // ---------------------------------------------------------------------------
  // 4. Manual Search & Autocomplete
  // ---------------------------------------------------------------------------
  let searchDebounce = null;

  searchInput.addEventListener("input", () => {
    clearTimeout(searchDebounce);
    const q = searchInput.value.trim();
    if (q.length < 2) {
      searchDropdown.style.display = "none";
      return;
    }

    searchDebounce = setTimeout(async () => {
      try {
        const resp = await fetch(`/tcg/api/register/search?q=${encodeURIComponent(q)}&limit=8`);
        const data = await resp.json();
        if (data.success && data.results && data.results.length > 0) {
          renderSearchResults(data.results);
        } else {
          searchDropdown.style.display = "none";
        }
      } catch (err) {
        console.error("[Register] Search query error:", err);
      }
    }, 200);
  });

  searchInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      const val = searchInput.value.trim();
      if (val) {
        searchDropdown.style.display = "none";
        resolveAndAddToCart(val);
        searchInput.value = "";
      }
    } else if (e.key === "Escape") {
      searchDropdown.style.display = "none";
    }
  });

  function renderSearchResults(items) {
    searchDropdown.innerHTML = "";
    items.forEach((item) => {
      const div = document.createElement("div");
      div.className = "autocomplete-item";
      div.innerHTML = `
        <div>
          <div style="font-weight: 600; color: var(--text-main);">${escapeHtml(item.name)}</div>
          <div style="font-size: 0.75rem; color: var(--text-muted);">
            ${escapeHtml(item.set_code.toUpperCase())} #${escapeHtml(item.collector_number)} &bull; ${escapeHtml(item.condition)} / ${escapeHtml(item.finish)}
          </div>
        </div>
        <div style="font-weight: 700; color: var(--accent-sky); font-size: 0.95rem;">
          $${Number(item.sell_price || 0).toFixed(2)}
        </div>
      `;
      div.onclick = () => {
        addToCart(item, 1);
        searchDropdown.style.display = "none";
        searchInput.value = "";
        searchInput.focus();
      };
      searchDropdown.appendChild(div);
    });
    searchDropdown.style.display = "block";
  }

  // Close dropdown on outside click
  document.addEventListener("click", (e) => {
    if (!searchDropdown.contains(e.target) && e.target !== searchInput) {
      searchDropdown.style.display = "none";
    }
  });

  // ---------------------------------------------------------------------------
  // 5. Tender Modal — Split Payment Engine
  // ---------------------------------------------------------------------------

  const tenderModal         = document.getElementById("tenderModal");
  const tenderAmountDue     = document.getElementById("tenderAmountDue");
  const tenderCashInput     = document.getElementById("tenderCash");
  const tenderCardInput     = document.getElementById("tenderCard");
  const tenderCardRef       = document.getElementById("tenderCardRef");
  const tenderCreditInput   = document.getElementById("tenderStoreCredit");
  const tenderCreditRef     = document.getElementById("tenderCreditRef");
  const tenderTotalDisplay  = document.getElementById("tenderTotalDisplay");
  const tenderRemaining     = document.getElementById("tenderRemainingDisplay");
  const tenderChangeGroup   = document.getElementById("tenderChangeDueGroup");
  const tenderChangeDisplay = document.getElementById("tenderChangeDueDisplay");
  const tenderQuickFills    = document.getElementById("tenderQuickFills");
  const btnConfirmTender    = document.getElementById("btnConfirmTender");
  const btnCancelTender     = document.getElementById("btnCancelTender");
  const btnCloseTenderModal = document.getElementById("btnCloseTenderModal");

  let _grandTotal = 0;

  function _calcGrandTotal() {
    let sub = 0;
    cart.forEach(item => { sub += item.sell_price * item.quantity; });
    const tax = sub * TAX_RATE;
    return Math.round((sub + tax) * 100) / 100;
  }

  function openTenderModal() {
    if (cart.length === 0) return;
    _grandTotal = _calcGrandTotal();

    // Reset inputs
    tenderCashInput.value   = "";
    tenderCardInput.value   = "";
    tenderCardRef.value     = "";
    tenderCreditInput.value = "";
    tenderCreditRef.value   = "";

    tenderAmountDue.textContent = `$${_grandTotal.toFixed(2)}`;

    // Quick-fill cash buttons
    tenderQuickFills.innerHTML = "";
    const amounts = [
      _grandTotal,
      Math.ceil(_grandTotal),
      Math.ceil(_grandTotal / 5) * 5,
      Math.ceil(_grandTotal / 10) * 10,
      Math.ceil(_grandTotal / 20) * 20,
    ];
    const seen = new Set();
    amounts.forEach(amt => {
      if (seen.has(amt) || amt <= 0) return;
      seen.add(amt);
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "tender-quick-btn";
      btn.textContent = `Cash $${amt.toFixed(2)}`;
      btn.onclick = () => {
        tenderCashInput.value = amt.toFixed(2);
        recalcTender();
        tenderCashInput.focus();
      };
      tenderQuickFills.appendChild(btn);
    });

    recalcTender();
    tenderModal.style.display = "flex";
    setTimeout(() => tenderCashInput.focus(), 80);
  }

  function closeTenderModal() {
    tenderModal.style.display = "none";
  }

  function recalcTender() {
    const cash   = parseFloat(tenderCashInput.value)   || 0;
    const card   = parseFloat(tenderCardInput.value)   || 0;
    const credit = parseFloat(tenderCreditInput.value) || 0;
    const total  = Math.round((cash + card + credit) * 100) / 100;
    const remaining = Math.round((_grandTotal - total) * 100) / 100;
    const change    = remaining < 0 ? Math.abs(remaining) : 0;

    tenderTotalDisplay.textContent = `$${total.toFixed(2)}`;
    tenderRemaining.textContent    = `$${Math.max(0, remaining).toFixed(2)}`;

    if (change > 0) {
      tenderChangeGroup.style.display = "";
      tenderChangeDisplay.textContent = `$${change.toFixed(2)}`;
    } else {
      tenderChangeGroup.style.display = "none";
    }

    btnConfirmTender.disabled = total < _grandTotal - 0.001;
  }

  [tenderCashInput, tenderCardInput, tenderCreditInput].forEach(el => {
    el.addEventListener("input", recalcTender);
  });

  btnCancelTender.addEventListener("click", closeTenderModal);
  btnCloseTenderModal.addEventListener("click", closeTenderModal);
  tenderModal.addEventListener("click", (e) => {
    if (e.target === tenderModal) closeTenderModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && tenderModal.style.display !== "none") closeTenderModal();
  });

  // Quick-select bill button handlers
  document.querySelectorAll(".btn-quick-bill").forEach(btn => {
    btn.addEventListener("click", () => {
      const addAmt = parseFloat(btn.getAttribute("data-amount")) || 0;
      const currentVal = parseFloat(tenderCashInput.value) || 0;
      tenderCashInput.value = (currentVal + addAmt).toFixed(2);
      recalcTender();
      tenderCashInput.focus();
    });
  });

  const optPrintReceipt = document.getElementById("optPrintReceipt");
  const optKickDrawer   = document.getElementById("optKickDrawer");

  async function confirmCheckout() {
    const cash   = parseFloat(tenderCashInput.value)   || 0;
    const card   = parseFloat(tenderCardInput.value)   || 0;
    const credit = parseFloat(tenderCreditInput.value) || 0;

    if (cash + card + credit < _grandTotal - 0.001) {
      showToast("Insufficient tender — please enter the full amount.", true);
      return;
    }

    btnConfirmTender.disabled = true;
    btnConfirmTender.innerHTML = `<span>⏳</span> Settling...`;

    const payload = {
      items: cart.map(item => ({
        id: item.id,
        quantity: item.quantity,
        unit_price: item.sell_price,
      })),
      tenders: {
        cash: cash,
        card: card,
        store_credit: credit,
      },
      tax_rate: TAX_RATE,
      discount: 0.0,
      notes: "",
      options: {
        print_receipt: optPrintReceipt ? optPrintReceipt.checked : true,
        kick_drawer: optKickDrawer ? optKickDrawer.checked : false,
      },
    };

    try {
      const resp = await fetch("/tcg/api/checkout/submit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await resp.json();

      if (data.success) {
        if (window.hardwareBridge) window.hardwareBridge.chimeSuccess();
        closeTenderModal();
        cart = [];
        renderCart();

        const txnNum = data.transaction?.transaction_number || data.transaction?.receipt_number || data.receipt_number || "";
        const receiptLink = data.receipt_url
          ? `<a class="toast-receipt-link" href="${data.receipt_url}" target="_blank">View Receipt</a>`
          : "";
        const changeMsg = data.change_due > 0 ? ` Change: $${data.change_due.toFixed(2)}.` : "";
        showToast(`Sale complete! Receipt #${txnNum}.${changeMsg} ${receiptLink}`, false);

        if (optPrintReceipt && optPrintReceipt.checked && data.receipt_url) {
          window.open(`${data.receipt_url}?autoprint=1`, "_blank");
        }

        if (data.hardware_warnings && data.hardware_warnings.length) {
          data.hardware_warnings.forEach(w => console.warn("[Register] HW warning:", w));
        }
      } else {
        if (window.hardwareBridge) window.hardwareBridge.chimeError();
        showToast(data.error || "Settlement failed. Please try again.", true);
      }
    } catch (err) {
      console.error("[Register] Checkout submit fault:", err);
      if (window.hardwareBridge) window.hardwareBridge.chimeError();
      showToast("Network fault during settlement.", true);
    } finally {
      btnConfirmTender.disabled = false;
      btnConfirmTender.innerHTML = `<span>&#10003;</span> Confirm &amp; Settle`;
    }
  }

  btnConfirmTender.addEventListener("click", confirmCheckout);

  [tenderCashInput, tenderCardInput, tenderCreditInput].forEach(el => {
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !btnConfirmTender.disabled) confirmCheckout();
    });
  });

  // ---------------------------------------------------------------------------
  // 6. Main Checkout Button -> Opens Tender Modal
  // ---------------------------------------------------------------------------
  btnCompleteCheckout.addEventListener("click", openTenderModal);

  btnClearCart.addEventListener("click", () => {
    if (cart.length === 0) return;
    cart = [];
    renderCart();
    showToast("Cart cleared.", false);
  });

  // Initial render
  renderCart();
})();
