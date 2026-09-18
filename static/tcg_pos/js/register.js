/**
 * OpenPOS TCG Addon - Modernized Register Checkout Client Engine
 * File: static/tcg_pos/js/register.js
 *
 * Implements:
 *   1. Zero-lag Web Audio API Synthesizer (Scan Beep, Error Beep, Settle Chime).
 *   2. Keyboard Hotkey Navigation (F2 Scan, F4 Customer, F8 Checkout, Esc Clear).
 *   3. 65%/35% Split-Pane Reactive Card Inspector.
 *   4. OpenPOS Core Customer Account & Store Credit Bridge.
 *   5. CFD DOM Event Emitters (openpos:cart-update, openpos:transaction-settled).
 */

(() => {
  "use strict";

  // =========================================================================
  // State
  // =========================================================================
  let cart = [];
  let inspectedItem = null;
  let attachedCustomer = null;
  let appliedStoreCredit = 0.0;
  const TAX_RATE = 0.0825; // 8.25% default sales tax

  // =========================================================================
  // DOM Elements
  // =========================================================================
  const searchInput = document.getElementById("registerSearchInput");
  const searchBoxContainer = document.getElementById("searchBoxContainer");
  const searchDropdown = document.getElementById("searchAutocompleteDropdown");
  const btnClearSearch = document.getElementById("btnClearSearch");

  const cartTableBody = document.getElementById("cartTableBody");
  const cartEmptyState = document.getElementById("cartEmptyState");
  const cartItemsCountBadge = document.getElementById("cartItemsCountBadge");
  const cartUniqueLinesCount = document.getElementById("cartUniqueLinesCount");
  const cartTotalQuantityCount = document.getElementById("cartTotalQuantityCount");
  const btnClearCart = document.getElementById("btnClearCart");

  // Sidebar Inspector Elements
  const inspectorEmptyState = document.getElementById("inspectorEmptyState");
  const inspectorActiveContent = document.getElementById("inspectorActiveContent");
  const inspectorArtImg = document.getElementById("inspectorArtImg");
  const inspectorCardTitle = document.getElementById("inspectorCardTitle");
  const inspectorTypeLine = document.getElementById("inspectorTypeLine");
  const inspectorSetBadge = document.getElementById("inspectorSetBadge");
  const inspectorCollectorBadge = document.getElementById("inspectorCollectorBadge");
  const inspectorRarityBadge = document.getElementById("inspectorRarityBadge");
  const inspectorFinishBadge = document.getElementById("inspectorFinishBadge");
  const inspectorConditionBadge = document.getElementById("inspectorConditionBadge");
  const inspectorGameBadge = document.getElementById("inspectorGameBadge");
  const inspectorBenchMarket = document.getElementById("inspectorBenchMarket");
  const inspectorBenchLow = document.getElementById("inspectorBenchLow");
  const inspectorBenchFoil = document.getElementById("inspectorBenchFoil");
  const inspectorBenchEtched = document.getElementById("inspectorBenchEtched");

  // Sidebar Customer Elements
  const customerDetachedView = document.getElementById("customerDetachedView");
  const customerAttachedView = document.getElementById("customerAttachedView");
  const customerAvatarInitial = document.getElementById("customerAvatarInitial");
  const customerNameDisplay = document.getElementById("customerNameDisplay");
  const customerContactDisplay = document.getElementById("customerContactDisplay");
  const customerCreditDisplay = document.getElementById("customerCreditDisplay");
  const customerLoyaltyPill = document.getElementById("customerLoyaltyPill");
  const btnToggleCustomerLookup = document.getElementById("btnToggleCustomerLookup");
  const btnAttachCustomerAction = document.getElementById("btnAttachCustomerAction");
  const btnDetachCustomer = document.getElementById("btnDetachCustomer");
  const btnQuickApplyCredit = document.getElementById("btnQuickApplyCredit");

  // Customer Modal Elements
  const customerLookupModal = document.getElementById("customerLookupModal");
  const customerSearchQueryInput = document.getElementById("customerSearchQueryInput");
  const btnSubmitCustomerSearch = document.getElementById("btnSubmitCustomerSearch");
  const customerSearchResults = document.getElementById("customerSearchResults");
  const btnCloseCustomerModal = document.getElementById("btnCloseCustomerModal");
  const btnCancelCustomerModal = document.getElementById("btnCancelCustomerModal");

  // Summary & Checkout Elements
  const subtotalDisplay = document.getElementById("subtotalDisplay");
  const taxDisplay = document.getElementById("taxDisplay");
  const totalDisplay = document.getElementById("totalDisplay");
  const storeCreditAppliedRow = document.getElementById("storeCreditAppliedRow");
  const storeCreditAppliedDisplay = document.getElementById("storeCreditAppliedDisplay");
  const btnCompleteCheckout = document.getElementById("btnCompleteCheckout");
  const hardwareBadge = document.getElementById("hardwareStatusBadge");
  const toastContainer = document.getElementById("toastContainer");

  // Tender Modal Elements
  const tenderModal = document.getElementById("tenderModal");
  const tenderAmountDue = document.getElementById("tenderAmountDue");
  const tenderCashInput = document.getElementById("tenderCash");
  const tenderCardInput = document.getElementById("tenderCard");
  const tenderCardRef = document.getElementById("tenderCardRef");
  const tenderCreditInput = document.getElementById("tenderStoreCredit");
  const tenderCreditRef = document.getElementById("tenderCreditRef");
  const modalCreditAvailableLabel = document.getElementById("modalCreditAvailableLabel");
  const tenderTotalDisplay = document.getElementById("tenderTotalDisplay");
  const tenderRemaining = document.getElementById("tenderRemainingDisplay");
  const tenderChangeGroup = document.getElementById("tenderChangeDueGroup");
  const tenderChangeDisplay = document.getElementById("tenderChangeDueDisplay");
  const tenderQuickFills = document.getElementById("tenderQuickFills");
  const btnConfirmTender = document.getElementById("btnConfirmTender");
  const btnCancelTender = document.getElementById("btnCancelTender");
  const btnCloseTenderModal = document.getElementById("btnCloseTenderModal");
  const optPrintReceipt = document.getElementById("optPrintReceipt");
  const optKickDrawer = document.getElementById("optKickDrawer");

  // =========================================================================
  // 1. Web Audio API Zero-Lag Synthesizer
  // =========================================================================
  let globalAudioCtx = null;
  let isBeeping = false;

  function getAudioContext() {
    if (!globalAudioCtx) {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (AudioContextClass) {
        globalAudioCtx = new AudioContextClass();
      }
    }
    if (globalAudioCtx && globalAudioCtx.state === "suspended") {
      globalAudioCtx.resume().catch(() => {});
    }
    return globalAudioCtx;
  }

  function playBeep() {
    if (isBeeping || document.hidden) return;
    isBeeping = true;
    try {
      const ctx = getAudioContext();
      if (!ctx) { isBeeping = false; return; }
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.frequency.value = 850;
      osc.type = "square";
      gain.gain.setValueAtTime(0.35, ctx.currentTime);
      osc.start();
      setTimeout(() => {
        try { osc.stop(); } catch(e) {}
        isBeeping = false;
      }, 100);
    } catch (e) {
      isBeeping = false;
    }
  }

  function playErrorBeep() {
    if (isBeeping || document.hidden) return;
    isBeeping = true;
    try {
      const ctx = getAudioContext();
      if (!ctx) { isBeeping = false; return; }
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.frequency.value = 150;
      osc.type = "sawtooth";
      gain.gain.setValueAtTime(0.4, ctx.currentTime);
      osc.start();
      setTimeout(() => {
        try { osc.stop(); } catch(e) {}
        isBeeping = false;
      }, 600);
    } catch (e) {
      isBeeping = false;
    }
  }

  function playChime() {
    try {
      const ctx = getAudioContext();
      if (!ctx) return;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.type = "sine";
      osc.frequency.setValueAtTime(880, ctx.currentTime); // A5
      osc.frequency.exponentialRampToValueAtTime(440, ctx.currentTime + 0.5);
      gain.gain.setValueAtTime(0.45, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.5);
      osc.start();
      osc.stop(ctx.currentTime + 0.5);
    } catch (e) {}
  }

  // Pre-warm audio on first user gesture
  const unlockAudio = () => {
    getAudioContext();
    window.removeEventListener("click", unlockAudio);
    window.removeEventListener("keydown", unlockAudio);
  };
  window.addEventListener("click", unlockAudio);
  window.addEventListener("keydown", unlockAudio);

  // =========================================================================
  // 2. Toast Feedback Helper
  // =========================================================================
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
    }, 3800);
  }

  // =========================================================================
  // 3. Active Card Inspector Renderer
  // =========================================================================
  function updateCardInspector(item) {
    if (!item) {
      inspectedItem = null;
      if (cart.length > 0) {
        updateCardInspector(cart[cart.length - 1]);
        return;
      }
      inspectorEmptyState.style.display = "block";
      inspectorActiveContent.style.display = "none";
      return;
    }

    inspectedItem = item;
    inspectorEmptyState.style.display = "none";
    inspectorActiveContent.style.display = "block";

    inspectorCardTitle.textContent = item.name || "Unknown Card";
    inspectorTypeLine.textContent = item.type_line || (item.game === "pokemon" ? "Pokémon Card" : "Card Single");
    inspectorSetBadge.textContent = (item.set_code || "TCG").toUpperCase();
    inspectorCollectorBadge.textContent = `#${item.collector_number || "000"}`;
    inspectorRarityBadge.textContent = item.rarity || "Single";
    inspectorFinishBadge.textContent = (item.finish || "nonfoil").toUpperCase();
    inspectorConditionBadge.textContent = (item.condition || "NM").toUpperCase();
    inspectorGameBadge.textContent = (item.game || "MTG").toUpperCase();

    // Artwork preview
    const artUrl = item.image_path || item.image_uri || "/tcg/addons/tcg_pos/static/placeholder_card.png";
    inspectorArtImg.src = artUrl;

    // Pricing benchmarks
    inspectorBenchMarket.textContent = fmtCurrency(item.market_price || item.sell_price || 0);
    inspectorBenchLow.textContent = fmtCurrency(item.low_price || 0);
    inspectorBenchFoil.textContent = fmtCurrency(item.foil_price || 0);
    inspectorBenchEtched.textContent = fmtCurrency(item.etched_price || 0);
  }

  function fmtCurrency(val) {
    if (val === null || val === undefined || isNaN(val) || Number(val) === 0) return "--";
    return "$" + Number(val).toFixed(2);
  }

  // =========================================================================
  // 4. Cart Operations & CFD Event Dispatchers
  // =========================================================================
  function addToCart(item, qtyToAdd = 1) {
    const existingIndex = cart.findIndex((c) => c.id === item.id);
    if (existingIndex > -1) {
      cart[existingIndex].quantity += qtyToAdd;
    } else {
      cart.push({
        id: item.id,
        game: item.game || "mtg",
        name: item.name,
        set_code: item.set_code || "",
        collector_number: item.collector_number || "",
        rarity: item.rarity || "",
        type_line: item.type_line || "",
        finish: item.finish || "nonfoil",
        condition: item.condition || "NM",
        sell_price: Number(item.sell_price || 0),
        market_price: item.market_price || null,
        low_price: item.low_price || null,
        foil_price: item.foil_price || null,
        etched_price: item.etched_price || null,
        custom_tag_id: item.custom_tag_id || null,
        image_path: item.image_path || null,
        image_uri: item.image_uri || null,
        available_quantity: Number(item.quantity || 1),
        quantity: qtyToAdd
      });
    }

    playBeep();
    updateCardInspector(cart[existingIndex > -1 ? existingIndex : cart.length - 1]);
    renderCart();
  }

  function updateItemQuantity(itemId, newQty) {
    const index = cart.findIndex((c) => c.id === itemId);
    if (index === -1) return;

    if (newQty <= 0) {
      const removed = cart.splice(index, 1)[0];
      if (inspectedItem && inspectedItem.id === removed.id) {
        updateCardInspector(cart.length > 0 ? cart[cart.length - 1] : null);
      }
    } else {
      cart[index].quantity = newQty;
      updateCardInspector(cart[index]);
    }
    renderCart();
  }

  function calculateSubtotal() {
    return cart.reduce((sum, i) => sum + (i.sell_price * i.quantity), 0);
  }

  function calculateTax() {
    return calculateSubtotal() * TAX_RATE;
  }

  function calculateGrandTotal() {
    const sub = calculateSubtotal();
    const tax = sub * TAX_RATE;
    const rawTotal = sub + tax - appliedStoreCredit;
    return Math.max(0, Math.round(rawTotal * 100) / 100);
  }

  function renderCart() {
    cartTableBody.innerHTML = "";

    const subtotal = calculateSubtotal();
    const tax = calculateTax();
    const grandTotal = calculateGrandTotal();

    let totalQuantity = 0;
    cart.forEach(i => { totalQuantity += i.quantity; });

    cartUniqueLinesCount.textContent = cart.length;
    cartTotalQuantityCount.textContent = totalQuantity;

    if (cart.length === 0) {
      cartEmptyState.style.display = "block";
      cartItemsCountBadge.textContent = "0 Items";
      btnCompleteCheckout.disabled = true;
      subtotalDisplay.textContent = "$0.00";
      taxDisplay.textContent = "$0.00";
      totalDisplay.textContent = "$0.00";
      storeCreditAppliedRow.style.display = "none";
      updateCardInspector(null);
      dispatchCfdCartUpdate([], 0, 0, 0);
      return;
    }

    cartEmptyState.style.display = "none";
    btnCompleteCheckout.disabled = false;

    cart.forEach((item) => {
      const lineTotal = item.sell_price * item.quantity;
      const tr = document.createElement("tr");
      tr.style.cursor = "pointer";

      const thumbUrl = item.image_path || item.image_uri || "/tcg/addons/tcg_pos/static/placeholder_card.png";
      const condClass = `badge-cond-${item.condition.toUpperCase()}`;
      const isFoil = (item.finish || "").toLowerCase().includes("foil");

      tr.innerHTML = `
        <td>
          <div class="cart-item-col">
            <img class="cart-thumb" src="${escapeHtml(thumbUrl)}" alt="${escapeHtml(item.name)}" onerror="this.src='/tcg/addons/tcg_pos/static/placeholder_card.png'" />
            <div>
              <div class="cart-item-title">${escapeHtml(item.name)}</div>
              <div class="cart-item-meta">
                <span class="badge-set">${escapeHtml(item.set_code.toUpperCase())}</span>
                <span>#${escapeHtml(item.collector_number || "000")}</span> &bull;
                <span class="${condClass}">${escapeHtml(item.condition)}</span>
                ${isFoil ? `<span class="badge-finish-foil">FOIL</span>` : `<span style="text-transform:uppercase; font-size:0.7rem;">${escapeHtml(item.finish)}</span>`}
                ${item.custom_tag_id ? `<span class="cart-tag-pill" title="Hardware Token">🏷️ ${escapeHtml(item.custom_tag_id)}</span>` : ""}
              </div>
            </div>
          </div>
        </td>
        <td style="font-weight: 700; text-align: right; font-family: 'JetBrains Mono', monospace;">$${item.sell_price.toFixed(2)}</td>
        <td style="text-align: center;">
          <div class="qty-stepper">
            <button type="button" class="stepper-btn btn-qty-minus" data-id="${item.id}">&minus;</button>
            <span class="stepper-val">${item.quantity}</span>
            <button type="button" class="stepper-btn btn-qty-plus" data-id="${item.id}">&plus;</button>
          </div>
        </td>
        <td style="font-weight: 800; color: var(--tcg-accent-sky); text-align: right; font-family: 'JetBrains Mono', monospace;">
          $${lineTotal.toFixed(2)}
        </td>
        <td style="text-align: center;">
          <button type="button" class="btn-remove-item" data-id="${item.id}" title="Remove item">&times;</button>
        </td>
      `;

      // Hover / tap to update inspector
      tr.addEventListener("mouseenter", () => updateCardInspector(item));
      tr.addEventListener("click", (e) => {
        if (!e.target.closest(".qty-stepper") && !e.target.closest(".btn-remove-item")) {
          updateCardInspector(item);
        }
      });

      tr.querySelector(".btn-qty-minus").onclick = (e) => {
        e.stopPropagation();
        updateItemQuantity(item.id, item.quantity - 1);
      };
      tr.querySelector(".btn-qty-plus").onclick = (e) => {
        e.stopPropagation();
        updateItemQuantity(item.id, item.quantity + 1);
      };
      tr.querySelector(".btn-remove-item").onclick = (e) => {
        e.stopPropagation();
        updateItemQuantity(item.id, 0);
      };

      cartTableBody.appendChild(tr);
    });

    // Update Totals
    cartItemsCountBadge.textContent = `${totalQuantity} ${totalQuantity === 1 ? "Item" : "Items"}`;
    subtotalDisplay.textContent = `$${subtotal.toFixed(2)}`;
    taxDisplay.textContent = `$${tax.toFixed(2)}`;
    totalDisplay.textContent = `$${grandTotal.toFixed(2)}`;

    if (appliedStoreCredit > 0) {
      storeCreditAppliedRow.style.display = "flex";
      storeCreditAppliedDisplay.textContent = `-$${appliedStoreCredit.toFixed(2)}`;
    } else {
      storeCreditAppliedRow.style.display = "none";
    }

    // CFD Dispatch
    dispatchCfdCartUpdate(cart, subtotal, tax, grandTotal);
  }

  function dispatchCfdCartUpdate(currentCart, subtotal, tax, grandTotal) {
    window.dispatchEvent(new CustomEvent("openpos:cart-update", {
      detail: {
        items: currentCart.map((item) => ({
          id: item.id,
          name: item.name,
          set_code: item.set_code,
          condition: item.condition,
          finish: item.finish,
          quantity: item.quantity,
          sell_price: item.sell_price,
          line_total: item.quantity * item.sell_price,
          image_uri: item.image_path || item.image_uri
        })),
        subtotal: subtotal,
        tax: tax,
        grand_total: grandTotal
      }
    }));
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

  // =========================================================================
  // 5. Hardware Scan Resolution & Wedge Listeners
  // =========================================================================
  async function resolveAndAddToCart(identifier) {
    if (!identifier) return;

    try {
      const resp = await fetch(`/tcg/api/resolve?identifier=${encodeURIComponent(identifier)}`);
      const data = await resp.json();

      if (data.found && data.item) {
        addToCart(data.item, 1);
        showToast(`Added ${data.item.name} (${(data.item.set_code || '').toUpperCase()}) to cart.`, false);
      } else {
        playErrorBeep();
        showToast(`No single matching identifier: "${identifier}"`, true);
      }
    } catch (err) {
      console.error("[Register] Item resolution error:", err);
      playErrorBeep();
      showToast(`Resolution network fault for "${identifier}"`, true);
    }
  }

  window.addEventListener("openpos:hardware-scan", (event) => {
    const detail = event.detail || {};
    const token = detail.value;
    console.log("[Register] Hardware scan intercepted:", detail);
    if (token) {
      resolveAndAddToCart(token);
    }
  });

  if (window.hardwareBridge && hardwareBadge) {
    window.hardwareBridge.attachStatusBadge(hardwareBadge);
  }

  // =========================================================================
  // 6. Manual Search & Autocomplete
  // =========================================================================
  let searchDebounce = null;

  searchInput.addEventListener("input", () => {
    clearTimeout(searchDebounce);
    const q = searchInput.value.trim();
    if (btnClearSearch) btnClearSearch.style.display = q ? "block" : "none";

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
    }, 180);
  });

  searchInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      const val = searchInput.value.trim();
      if (val) {
        searchDropdown.style.display = "none";
        resolveAndAddToCart(val);
        searchInput.value = "";
        if (btnClearSearch) btnClearSearch.style.display = "none";
      }
    } else if (e.key === "Escape") {
      searchDropdown.style.display = "none";
    }
  });

  if (btnClearSearch) {
    btnClearSearch.addEventListener("click", () => {
      searchInput.value = "";
      btnClearSearch.style.display = "none";
      searchDropdown.style.display = "none";
      searchInput.focus();
    });
  }

  function renderSearchResults(items) {
    searchDropdown.innerHTML = "";
    items.forEach((item) => {
      const div = document.createElement("div");
      div.className = "autocomplete-item";
      div.innerHTML = `
        <div>
          <div style="font-weight: 700; color: var(--tcg-text-main);">${escapeHtml(item.name)}</div>
          <div style="font-size: 0.76rem; color: var(--tcg-text-muted); margin-top: 2px;">
            <span class="badge-set">${escapeHtml(item.set_code.toUpperCase())}</span> #${escapeHtml(item.collector_number || "000")} &bull; 
            <strong>${escapeHtml(item.condition)}</strong> / ${escapeHtml(item.finish)}
            ${item.custom_tag_id ? `<span class="cart-tag-pill">🏷️ ${escapeHtml(item.custom_tag_id)}</span>` : ""}
          </div>
        </div>
        <div style="font-weight: 800; color: var(--tcg-accent-sky); font-size: 0.95rem; font-family: 'JetBrains Mono', monospace;">
          $${Number(item.sell_price || 0).toFixed(2)}
        </div>
      `;
      div.onclick = () => {
        addToCart(item, 1);
        searchDropdown.style.display = "none";
        searchInput.value = "";
        if (btnClearSearch) btnClearSearch.style.display = "none";
        searchInput.focus();
      };
      searchDropdown.appendChild(div);
    });
    searchDropdown.style.display = "block";
  }

  document.addEventListener("click", (e) => {
    if (!searchDropdown.contains(e.target) && e.target !== searchInput) {
      searchDropdown.style.display = "none";
    }
  });

  // =========================================================================
  // 7. Customer & Store Credit Integration
  // =========================================================================
  function openCustomerModal() {
    customerLookupModal.style.display = "flex";
    customerSearchQueryInput.value = "";
    customerSearchResults.innerHTML = `
      <div class="customer-search-prompt" style="text-align: center; padding: 2rem 1rem; color: var(--tcg-text-subtle);">
        Scan an NFC loyalty card, tap phone, or enter name / phone / email to search Core customer accounts.
      </div>
    `;
    setTimeout(() => customerSearchQueryInput.focus(), 80);
  }

  function closeCustomerModal() {
    customerLookupModal.style.display = "none";
  }

  async function performCustomerSearch() {
    const q = customerSearchQueryInput.value.trim();
    if (!q) return;

    customerSearchResults.innerHTML = `<div style="padding: 1.5rem; text-align: center; color: var(--tcg-text-muted);">Querying Core customer ledger...</div>`;

    try {
      const resp = await fetch(`/tcg/api/register/customer?q=${encodeURIComponent(q)}`);
      const data = await resp.json();

      if (data.found && data.customer) {
        renderCustomerResults([data.customer]);
      } else {
        customerSearchResults.innerHTML = `<div style="padding: 1.5rem; text-align: center; color: var(--tcg-accent-rose);">No customer found matching "${escapeHtml(q)}".</div>`;
      }
    } catch (err) {
      console.error("[CustomerLookup] Error:", err);
      customerSearchResults.innerHTML = `<div style="padding: 1.5rem; text-align: center; color: var(--tcg-accent-rose);">Customer lookup service unavailable.</div>`;
    }
  }

  function renderCustomerResults(customers) {
    customerSearchResults.innerHTML = "";
    customers.forEach((c) => {
      const div = document.createElement("div");
      div.className = "customer-result-item";
      const credit = Number(c.store_credit_balance || c.balance || 0);
      div.innerHTML = `
        <div>
          <div style="font-weight: 700; color: var(--tcg-text-main);">${escapeHtml(c.name || "Customer")}</div>
          <div style="font-size: 0.76rem; color: var(--tcg-text-muted);">${escapeHtml(c.phone || c.email || "No contact info")}</div>
        </div>
        <div style="text-align: right;">
          <div style="font-size: 0.72rem; color: var(--tcg-text-subtle); text-transform: uppercase;">Store Credit</div>
          <div style="font-family: 'JetBrains Mono', monospace; font-weight: 800; color: var(--tcg-accent-emerald); font-size: 0.95rem;">
            $${credit.toFixed(2)}
          </div>
        </div>
      `;
      div.onclick = () => {
        attachCustomer(c);
        closeCustomerModal();
      };
      customerSearchResults.appendChild(div);
    });
  }

  function attachCustomer(c) {
    attachedCustomer = c;
    const credit = Number(c.store_credit_balance || c.balance || 0);
    customerAvatarInitial.textContent = (c.name || "C").charAt(0).toUpperCase();
    customerNameDisplay.textContent = c.name || "Customer";
    customerContactDisplay.textContent = c.phone || c.email || `ID #${c.id}`;
    customerCreditDisplay.textContent = `$${credit.toFixed(2)}`;

    customerDetachedView.style.display = "none";
    customerAttachedView.style.display = "block";
    showToast(`Attached customer: ${c.name} (Credit: $${credit.toFixed(2)})`, false);

    // If modal credit available label is active
    if (modalCreditAvailableLabel) {
      modalCreditAvailableLabel.textContent = `Available: $${credit.toFixed(2)}`;
    }
  }

  function detachCustomer() {
    attachedCustomer = null;
    appliedStoreCredit = 0.0;
    customerDetachedView.style.display = "block";
    customerAttachedView.style.display = "none";
    if (modalCreditAvailableLabel) {
      modalCreditAvailableLabel.textContent = "Available: $0.00";
    }
    renderCart();
    showToast("Customer detached.", false);
  }

  btnToggleCustomerLookup.addEventListener("click", openCustomerModal);
  btnAttachCustomerAction.addEventListener("click", openCustomerModal);
  btnDetachCustomer.addEventListener("click", detachCustomer);
  btnCloseCustomerModal.addEventListener("click", closeCustomerModal);
  btnCancelCustomerModal.addEventListener("click", closeCustomerModal);

  btnSubmitCustomerSearch.addEventListener("click", performCustomerSearch);
  customerSearchQueryInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      performCustomerSearch();
    }
  });

  btnQuickApplyCredit.addEventListener("click", () => {
    if (!attachedCustomer) return;
    const available = Number(attachedCustomer.store_credit_balance || attachedCustomer.balance || 0);
    const sub = calculateSubtotal();
    const tax = calculateTax();
    const needed = sub + tax;
    appliedStoreCredit = Math.min(available, Math.round(needed * 100) / 100);
    renderCart();
    showToast(`Applied $${appliedStoreCredit.toFixed(2)} store credit to order.`, false);
  });

  // =========================================================================
  // 8. Tender Modal — Split Payment Engine
  // =========================================================================
  let _grandTotal = 0;

  function openTenderModal() {
    if (cart.length === 0) return;
    _grandTotal = calculateGrandTotal();

    tenderCashInput.value = "";
    tenderCardInput.value = "";
    tenderCardRef.value = "";
    tenderCreditInput.value = "";
    tenderCreditRef.value = "";

    // Pre-populate store credit if applied
    if (appliedStoreCredit > 0) {
      tenderCreditInput.value = appliedStoreCredit.toFixed(2);
    }

    if (attachedCustomer) {
      const avail = Number(attachedCustomer.store_credit_balance || attachedCustomer.balance || 0);
      modalCreditAvailableLabel.textContent = `Available: $${avail.toFixed(2)}`;
    } else {
      modalCreditAvailableLabel.textContent = "Available: $0.00";
    }

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
    const cash = parseFloat(tenderCashInput.value) || 0;
    const card = parseFloat(tenderCardInput.value) || 0;
    const credit = parseFloat(tenderCreditInput.value) || 0;
    const total = Math.round((cash + card + credit) * 100) / 100;
    const remaining = Math.round((_grandTotal - total) * 100) / 100;
    const change = remaining < 0 ? Math.abs(remaining) : 0;

    tenderTotalDisplay.textContent = `$${total.toFixed(2)}`;
    tenderRemaining.textContent = `$${Math.max(0, remaining).toFixed(2)}`;

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

  // Quick bill buttons
  document.querySelectorAll(".btn-quick-bill").forEach(btn => {
    btn.addEventListener("click", () => {
      const addAmt = parseFloat(btn.getAttribute("data-amount")) || 0;
      const currentVal = parseFloat(tenderCashInput.value) || 0;
      tenderCashInput.value = (currentVal + addAmt).toFixed(2);
      recalcTender();
      tenderCashInput.focus();
    });
  });

  async function confirmCheckout() {
    const cash = parseFloat(tenderCashInput.value) || 0;
    const card = parseFloat(tenderCardInput.value) || 0;
    const credit = parseFloat(tenderCreditInput.value) || 0;

    if (cash + card + credit < _grandTotal - 0.001) {
      playErrorBeep();
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
      customer_id: attachedCustomer ? attachedCustomer.id : null,
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
        playChime();
        closeTenderModal();

        const txn = data.transaction || {};
        const txnNum = txn.transaction_number || txn.receipt_number || data.receipt_number || "";
        const changeDue = data.change_due || 0;

        // Dispatch CFD transaction settled event
        window.dispatchEvent(new CustomEvent("openpos:transaction-settled", {
          detail: {
            transaction_number: txnNum,
            grand_total: txn.grand_total || _grandTotal,
            change_due: changeDue
          }
        }));

        cart = [];
        appliedStoreCredit = 0.0;
        renderCart();

        const receiptLink = data.receipt_url
          ? `<a class="toast-receipt-link" href="${data.receipt_url}" target="_blank" style="color:var(--tcg-accent-sky); margin-left:8px; text-decoration:underline;">View Receipt</a>`
          : "";
        const changeMsg = changeDue > 0 ? ` Change Due: $${changeDue.toFixed(2)}.` : "";
        showToast(`Sale Complete! Receipt #${txnNum}.${changeMsg} ${receiptLink}`, false);

        if (optPrintReceipt && optPrintReceipt.checked && data.receipt_url) {
          window.open(`${data.receipt_url}?autoprint=1`, "_blank");
        }

        if (data.hardware_warnings && data.hardware_warnings.length) {
          data.hardware_warnings.forEach(w => console.warn("[Register] HW warning:", w));
        }
      } else {
        playErrorBeep();
        showToast(data.error || "Settlement failed. Please check tender amounts.", true);
      }
    } catch (err) {
      console.error("[Register] Checkout submit fault:", err);
      playErrorBeep();
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

  btnCompleteCheckout.addEventListener("click", openTenderModal);

  btnClearCart.addEventListener("click", () => {
    if (cart.length === 0) return;
    if (confirm("Clear all items in cart?")) {
      cart = [];
      appliedStoreCredit = 0.0;
      renderCart();
      showToast("Cart cleared.", false);
    }
  });

  // =========================================================================
  // 9. Keyboard Navigation Shortcuts (F2, F4, F8, Esc)
  // =========================================================================
  document.addEventListener("keydown", (e) => {
    // F2: Auto-focus search input
    if (e.key === "F2") {
      e.preventDefault();
      searchInput.focus();
      searchInput.select();
      if (searchBoxContainer) {
        searchBoxContainer.classList.add("pulse-active");
        setTimeout(() => searchBoxContainer.classList.remove("pulse-active"), 1600);
      }
    }
    // F4: Customer Lookup Modal Toggle
    else if (e.key === "F4") {
      e.preventDefault();
      if (customerLookupModal.style.display !== "none") {
        closeCustomerModal();
      } else {
        openCustomerModal();
      }
    }
    // F8: Tender / Checkout Modal
    else if (e.key === "F8") {
      e.preventDefault();
      if (cart.length > 0 && tenderModal.style.display === "none") {
        openTenderModal();
      }
    }
    // Escape: Close modals, blur inputs, hide dropdowns
    else if (e.key === "Escape") {
      if (tenderModal.style.display !== "none") {
        closeTenderModal();
      } else if (customerLookupModal.style.display !== "none") {
        closeCustomerModal();
      } else if (searchDropdown.style.display !== "none") {
        searchDropdown.style.display = "none";
      } else {
        document.activeElement?.blur();
      }
    }
  });

  // Initial render
  renderCart();
})();
