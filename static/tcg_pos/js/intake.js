/**
 * OpenPOS-TCG: Intake Workstation & Buylist Client Engine
 * Handles keyboard-accessible card intake, live search autocomplete,
 * dynamic pricing evaluation, and inventory batch commits.
 */

(() => {
  "use strict";

  // State Management
  let activeCard = null;
  let currentFinish = "nonfoil";
  let currentCondition = "NM";
  let currentQuantity = 1;
  let stagedQueue = [];

  // DOM Elements
  const searchInput = document.getElementById("card-search-input");
  const clearSearchBtn = document.getElementById("search-clear-btn");
  const dropdown = document.getElementById("autocomplete-dropdown");
  const searchSpinner = document.getElementById("search-spinner");

  // Inspector Elements
  const inspectorEmpty = document.getElementById("inspector-empty");
  const inspectorContent = document.getElementById("inspector-content");
  const cardArtImg = document.getElementById("card-art-img");
  const cardTitle = document.getElementById("card-title");
  const cardTypeLine = document.getElementById("card-type-line");
  const badgeRarity = document.getElementById("badge-rarity");
  const badgeSet = document.getElementById("badge-set");
  const badgeCollector = document.getElementById("badge-collector");

  // Benchmarks
  const valMarket = document.getElementById("val-market");
  const valLow = document.getElementById("val-low");
  const valFoil = document.getElementById("val-foil");
  const valEtched = document.getElementById("val-etched");

  // Calculator Displays
  const offerCashVal = document.getElementById("offer-cash-val");
  const offerCreditVal = document.getElementById("offer-credit-val");
  const inputCostBasis = document.getElementById("input-cost-basis");
  const inputSellPrice = document.getElementById("input-sell-price");
  const inputQuantity = document.getElementById("input-quantity");
  const btnQtyMinus = document.getElementById("btn-qty-minus");
  const btnQtyPlus = document.getElementById("btn-qty-plus");
  const btnStageCard = document.getElementById("btn-stage-card");

  // Queue Elements
  const queueTableBody = document.getElementById("queue-table-body");
  const queueEmptyState = document.getElementById("queue-empty-state");
  const queueBadgeCount = document.getElementById("queue-badge-count");
  const btnClearQueue = document.getElementById("btn-clear-queue");
  const summaryTotalCards = document.getElementById("summary-total-cards");
  const summaryTotalCost = document.getElementById("summary-total-cost");
  const summaryTotalRetail = document.getElementById("summary-total-retail");
  const summaryMargin = document.getElementById("summary-margin");
  const btnCommitCash = document.getElementById("btn-commit-cash");
  const btnCommitCredit = document.getElementById("btn-commit-credit");

  // Condition multipliers map
  const COND_MULTS = {
    NM: 1.00,
    LP: 0.85,
    MP: 0.70,
    HP: 0.50,
    DMG: 0.25
  };

  /** Toast Notifications */
  function showToast(message, type = "info") {
    const container = document.getElementById("toast-container");
    if (!container) return;

    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `
      <span>${message}</span>
      <span style="cursor:pointer; margin-left: 0.75rem; font-weight:bold;">&times;</span>
    `;
    toast.querySelector("span:last-child").onclick = () => toast.remove();
    container.appendChild(toast);

    setTimeout(() => {
      toast.style.transition = "opacity 0.3s ease, transform 0.3s ease";
      toast.style.opacity = "0";
      toast.style.transform = "translateX(50px)";
      setTimeout(() => toast.remove(), 300);
    }, 4000);
  }

  /** Currency Formatter */
  function fmt(val) {
    if (val === null || val === undefined || isNaN(val)) return "$0.00";
    return "$" + Number(val).toFixed(2);
  }

  /** Debounce Helper */
  function debounce(fn, delay) {
    let timer = null;
    return function (...args) {
      clearTimeout(timer);
      timer = setTimeout(() => fn.apply(this, args), delay);
    };
  }

  // --- Search & Autocomplete ---

  const performSearch = debounce(async (query) => {
    query = query.trim();
    if (!query || query.length < 2) {
      dropdown.style.display = "none";
      dropdown.innerHTML = "";
      return;
    }

    try {
      if (searchSpinner) searchSpinner.style.display = "inline-block";
      const resp = await fetch(`/tcg/api/search?q=${encodeURIComponent(query)}`);
      const data = await resp.json();

      if (searchSpinner) searchSpinner.style.display = "none";

      if (data.success && data.results && data.results.length > 0) {
        renderAutocomplete(data.results);
      } else {
        dropdown.innerHTML = `<div style="padding: 1rem; color: var(--text-dim); text-align: center;">No matching cards found</div>`;
        dropdown.style.display = "block";
      }
    } catch (err) {
      if (searchSpinner) searchSpinner.style.display = "none";
      console.error("Search error:", err);
    }
  }, 250);

  function renderAutocomplete(cards) {
    dropdown.innerHTML = "";
    cards.slice(0, 10).forEach((card, index) => {
      const item = document.createElement("div");
      item.className = "autocomplete-item" + (index === 0 ? " active" : "");
      
      const thumb = card.image_uri || "/tcg/addons/tcg_pos/static/placeholder_card.png";
      const priceStr = card.market_price ? fmt(card.market_price) : "--";

      item.innerHTML = `
        <img class="autocomplete-thumb" src="${thumb}" alt="${card.name}" onerror="this.src='/tcg/addons/tcg_pos/static/placeholder_card.png'" />
        <div class="autocomplete-details">
          <div class="autocomplete-title">${card.name}</div>
          <div class="autocomplete-sub">
            <span class="badge badge-set">${card.set_code.toUpperCase()}</span>
            <span>#${card.collector_number}</span>
            <span style="text-transform: capitalize;">&bull; ${card.rarity}</span>
          </div>
        </div>
        <div class="autocomplete-price">${priceStr}</div>
      `;

      item.onclick = () => selectCard(card);
      dropdown.appendChild(item);
    });

    dropdown.style.display = "block";
  }

  function selectCard(card) {
    activeCard = card;
    dropdown.style.display = "none";
    dropdown.innerHTML = "";
    searchInput.value = `${card.name} (${card.set_code.toUpperCase()} #${card.collector_number})`;
    clearSearchBtn.style.display = "block";

    // Switch inspector from empty to active
    inspectorEmpty.style.display = "none";
    inspectorContent.style.display = "block";

    // Populate metadata
    cardTitle.textContent = card.name;
    cardTypeLine.textContent = (card.api_metadata && card.api_metadata.type_line) || "Card";
    cardArtImg.src = card.image_uri || "";
    badgeRarity.textContent = card.rarity;
    badgeRarity.className = `badge badge-${card.rarity.toLowerCase()}`;
    badgeSet.textContent = `${card.set_name} (${card.set_code.toUpperCase()})`;
    badgeCollector.textContent = `#${card.collector_number}`;

    // Benchmark Prices
    valMarket.textContent = fmt(card.market_price);
    valLow.textContent = fmt(card.low_price);
    valFoil.textContent = fmt(card.foil_price);
    valEtched.textContent = fmt(card.etched_price);

    // Reset default finish & condition
    setFinish("nonfoil");
    setCondition("NM");
    setQuantity(1);

    // Trigger instant buylist calculation
    updateBuylistCalculation();
  }

  // --- Finish & Condition Matrix ---

  function setFinish(finish) {
    currentFinish = finish.toLowerCase();
    document.querySelectorAll(".btn-finish").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.finish === currentFinish);
    });
    updateBuylistCalculation();
  }

  function setCondition(cond) {
    currentCondition = cond.toUpperCase();
    document.querySelectorAll(".btn-cond").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.cond === currentCondition);
    });
    updateBuylistCalculation();
  }

  function setQuantity(qty) {
    currentQuantity = Math.max(1, parseInt(qty) || 1);
    inputQuantity.value = currentQuantity;
    updateBuylistCalculation();
  }

  // --- Buylist Price Calculation ---

  function resolveApplicablePrice() {
    if (!activeCard) return 0.0;
    if (currentFinish === "foil" || currentFinish === "reverse_holo") {
      return activeCard.foil_price || activeCard.market_price || 0.0;
    }
    if (currentFinish === "etched") {
      return activeCard.etched_price || activeCard.foil_price || activeCard.market_price || 0.0;
    }
    return activeCard.market_price || 0.0;
  }

  async function updateBuylistCalculation() {
    if (!activeCard) return;

    const basePrice = resolveApplicablePrice();
    const mult = COND_MULTS[currentCondition] || 1.0;
    const adjustedValue = basePrice * mult;

    // Fast client-side estimate preview
    const cashOffer = Number((adjustedValue * 0.50).toFixed(2));
    const creditOffer = Number((cashOffer * 1.30).toFixed(2));

    offerCashVal.textContent = fmt(cashOffer * currentQuantity);
    offerCreditVal.textContent = fmt(creditOffer * currentQuantity);

    // Default cost basis to cash offer & sell price to market price
    inputCostBasis.value = cashOffer.toFixed(2);
    inputSellPrice.value = basePrice.toFixed(2);

    // Also dispatch to backend /tcg/api/buylist/calculate to ensure exact parity
    try {
      const resp = await fetch("/tcg/api/buylist/calculate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          market_price: basePrice,
          condition: currentCondition,
          finish: currentFinish,
          quantity: currentQuantity,
          card_name: activeCard.name
        })
      });
      const res = await resp.json();
      if (res.success) {
        offerCashVal.textContent = fmt(res.total_cash_offer);
        offerCreditVal.textContent = fmt(res.total_credit_offer);
        inputCostBasis.value = res.cash_offer.toFixed(2);
      }
    } catch (err) {
      console.warn("Backend buylist calculation request failed, using client estimate:", err);
    }
  }

  // --- Staging Queue (Cart) ---

  function stageCurrentCard() {
    if (!activeCard) {
      showToast("Please search and select a card first.", "warning");
      return;
    }

    const qty = Math.max(1, parseInt(inputQuantity.value) || 1);
    const costBasis = Math.max(0, parseFloat(inputCostBasis.value) || 0.0);
    const sellPrice = Math.max(0, parseFloat(inputSellPrice.value) || 0.0);
    const basePrice = resolveApplicablePrice();

    const existingIndex = stagedQueue.findIndex(
      (item) =>
        item.provider_card_id === activeCard.provider_card_id &&
        item.finish === currentFinish &&
        item.condition === currentCondition
    );

    if (existingIndex > -1) {
      // Merge into existing row
      const existing = stagedQueue[existingIndex];
      const totalQty = existing.quantity + qty;
      const newCost = ((existing.quantity * existing.cost_basis) + (qty * costBasis)) / totalQty;

      existing.quantity = totalQty;
      existing.cost_basis = Number(newCost.toFixed(4));
      existing.sell_price = sellPrice;
      showToast(`Updated ${activeCard.name} in staged queue (+${qty}).`, "info");
    } else {
      // Create new staged queue entry matching SinglesInventory schema
      const stagedItem = {
        game: activeCard.game || "mtg",
        provider_card_id: activeCard.provider_card_id,
        name: activeCard.name,
        clean_name: activeCard.clean_name,
        set_code: activeCard.set_code,
        set_name: activeCard.set_name,
        collector_number: activeCard.collector_number,
        rarity: activeCard.rarity,
        finish: currentFinish,
        condition: currentCondition,
        quantity: qty,
        cost_basis: costBasis,
        sell_price: sellPrice,
        market_price: activeCard.market_price,
        low_price: activeCard.low_price,
        foil_price: activeCard.foil_price,
        etched_price: activeCard.etched_price,
        image_path: activeCard.cached_image_path,
        image_uri: activeCard.image_uri,
        api_metadata: activeCard.api_metadata || {}
      };
      stagedQueue.push(stagedItem);
      showToast(`Added ${qty}x ${activeCard.name} to staged queue.`, "success");
    }

    renderQueue();
    // Refocus search for rapid scanning
    searchInput.focus();
    searchInput.select();
  }

  function removeFromQueue(index) {
    if (index >= 0 && index < stagedQueue.length) {
      const removed = stagedQueue.splice(index, 1)[0];
      showToast(`Removed ${removed.name} from queue.`, "info");
      renderQueue();
    }
  }

  function clearQueue() {
    if (stagedQueue.length === 0) return;
    if (confirm("Are you sure you want to clear all staged items?")) {
      stagedQueue = [];
      renderQueue();
      showToast("Staged queue cleared.", "info");
    }
  }

  function renderQueue() {
    queueTableBody.innerHTML = "";

    if (stagedQueue.length === 0) {
      queueEmptyState.style.display = "flex";
      queueBadgeCount.textContent = "0 Cards";
      summaryTotalCards.textContent = "0";
      summaryTotalCost.textContent = "$0.00";
      summaryTotalRetail.textContent = "$0.00";
      summaryMargin.textContent = "$0.00 (0%)";
      btnCommitCash.disabled = true;
      btnCommitCredit.disabled = true;
      return;
    }

    queueEmptyState.style.display = "none";
    let totalCards = 0;
    let totalCost = 0.0;
    let totalRetail = 0.0;

    stagedQueue.forEach((item, index) => {
      totalCards += item.quantity;
      const subtotalCost = item.quantity * item.cost_basis;
      const subtotalRetail = item.quantity * item.sell_price;
      totalCost += subtotalCost;
      totalRetail += subtotalRetail;

      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>
          <strong>${item.name}</strong>
          <div style="font-size: 0.72rem; color: var(--text-dim); text-transform: uppercase;">
            ${item.set_code} #${item.collector_number}
          </div>
        </td>
        <td><span class="badge badge-set">${item.set_code.toUpperCase()}</span></td>
        <td>
          <span style="text-transform: capitalize;">${item.finish}</span> /
          <strong style="color: ${getCondColor(item.condition)};">${item.condition}</strong>
        </td>
        <td><strong style="font-size: 0.95rem;">${item.quantity}</strong></td>
        <td>${fmt(item.cost_basis)}</td>
        <td>${fmt(item.sell_price)}</td>
        <td><strong style="color: var(--accent-green);">${fmt(subtotalCost)}</strong></td>
        <td>
          <button class="btn-del-row" data-index="${index}" title="Remove item">&times;</button>
        </td>
      `;
      queueTableBody.appendChild(tr);
    });

    // Attach row delete handlers
    document.querySelectorAll(".btn-del-row").forEach((btn) => {
      btn.onclick = () => removeFromQueue(parseInt(btn.dataset.index));
    });

    // Update Summary Card
    const marginDol = totalRetail - totalCost;
    const marginPct = totalRetail > 0 ? ((marginDol / totalRetail) * 100).toFixed(1) : 0;

    queueBadgeCount.textContent = `${totalCards} ${totalCards === 1 ? "Card" : "Cards"}`;
    summaryTotalCards.textContent = totalCards;
    summaryTotalCost.textContent = fmt(totalCost);
    summaryTotalRetail.textContent = fmt(totalRetail);
    summaryMargin.textContent = `${fmt(marginDol)} (${marginPct}%)`;

    btnCommitCash.disabled = false;
    btnCommitCredit.disabled = false;
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

  // --- Batch Commit to Inventory ---

  async function commitBatch(payoutType = "cash") {
    if (stagedQueue.length === 0) return;

    const actionTitle = payoutType === "cash" ? "Direct Purchase (Cash)" : "Trade-In (Store Credit)";
    btnCommitCash.disabled = true;
    btnCommitCredit.disabled = true;

    try {
      const resp = await fetch("/tcg/api/intake/commit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(stagedQueue)
      });

      const res = await resp.json();
      if (res.success) {
        showToast(`Committed ${res.updated_count} items via ${actionTitle}!`, "success");
        stagedQueue = [];
        renderQueue();
        if (searchInput) searchInput.focus();
      } else {
        showToast(`Commit failed: ${res.error || "Unknown server error"}`, "error");
        btnCommitCash.disabled = false;
        btnCommitCredit.disabled = false;
      }
    } catch (err) {
      console.error("Batch commit error:", err);
      showToast("Network error executing intake commit.", "error");
      btnCommitCash.disabled = false;
      btnCommitCredit.disabled = false;
    }
  }

  // --- Event Listeners & Hotkeys ---

  document.addEventListener("DOMContentLoaded", () => {
    // Search input typing
    searchInput.addEventListener("input", (e) => {
      const val = e.target.value;
      clearSearchBtn.style.display = val ? "block" : "none";
      performSearch(val);
    });

    clearSearchBtn.addEventListener("click", () => {
      searchInput.value = "";
      clearSearchBtn.style.display = "none";
      dropdown.style.display = "none";
      searchInput.focus();
    });

    // Keyboard navigation in search dropdown
    searchInput.addEventListener("keydown", (e) => {
      const items = dropdown.querySelectorAll(".autocomplete-item");
      if (dropdown.style.display === "block" && items.length > 0) {
        let activeIdx = Array.from(items).findIndex((i) => i.classList.contains("active"));

        if (e.key === "ArrowDown") {
          e.preventDefault();
          if (activeIdx > -1) items[activeIdx].classList.remove("active");
          activeIdx = (activeIdx + 1) % items.length;
          items[activeIdx].classList.add("active");
          items[activeIdx].scrollIntoView({ block: "nearest" });
        } else if (e.key === "ArrowUp") {
          e.preventDefault();
          if (activeIdx > -1) items[activeIdx].classList.remove("active");
          activeIdx = (activeIdx - 1 + items.length) % items.length;
          items[activeIdx].classList.add("active");
          items[activeIdx].scrollIntoView({ block: "nearest" });
        } else if (e.key === "Enter") {
          e.preventDefault();
          if (activeIdx > -1) items[activeIdx].click();
        } else if (e.key === "Escape") {
          dropdown.style.display = "none";
        }
      } else if (e.key === "Enter" && !dropdown.style.display) {
        // Direct scan / search on Enter
        e.preventDefault();
        performSearch(searchInput.value);
      }
    });

    // Close autocomplete on click outside
    document.addEventListener("click", (e) => {
      if (!e.target.closest(".search-wrapper")) {
        dropdown.style.display = "none";
      }
    });

    // Matrix button click listeners
    document.querySelectorAll(".btn-finish").forEach((btn) => {
      btn.addEventListener("click", () => setFinish(btn.dataset.finish));
    });

    document.querySelectorAll(".btn-cond").forEach((btn) => {
      btn.addEventListener("click", () => setCondition(btn.dataset.cond));
    });

    // Stepper controls
    btnQtyMinus.addEventListener("click", () => setQuantity(currentQuantity - 1));
    btnQtyPlus.addEventListener("click", () => setQuantity(currentQuantity + 1));
    inputQuantity.addEventListener("change", (e) => setQuantity(e.target.value));

    // Staging and Queue buttons
    btnStageCard.addEventListener("click", stageCurrentCard);
    btnClearQueue.addEventListener("click", clearQueue);
    btnCommitCash.addEventListener("click", () => commitBatch("cash"));
    btnCommitCredit.addEventListener("click", () => commitBatch("credit"));

    // Global keyboard hotkeys
    document.addEventListener("keydown", (e) => {
      const isInput = ["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName);

      if (e.key === "/" && !isInput) {
        e.preventDefault();
        searchInput.focus();
        searchInput.select();
      } else if (e.key === " " && !isInput && activeCard) {
        e.preventDefault();
        stageCurrentCard();
      } else if (!isInput && activeCard) {
        // Finish hotkeys: 1 (Nonfoil), 2 (Foil), 3 (Etched)
        if (e.key === "1") setFinish("nonfoil");
        if (e.key === "2") setFinish("foil");
        if (e.key === "3") setFinish("etched");

        // Condition hotkeys: q (NM), w (LP), e (MP), r (HP), t (DMG)
        if (e.key.toLowerCase() === "q") setCondition("NM");
        if (e.key.toLowerCase() === "w") setCondition("LP");
        if (e.key.toLowerCase() === "e") setCondition("MP");
        if (e.key.toLowerCase() === "r") setCondition("HP");
        if (e.key.toLowerCase() === "t") setCondition("DMG");
      }
    });

    renderQueue();
  });
})();
