/**
 * OpenPOS-TCG: Modernized Intake Workstation & Buylist Client Engine
 * File: static/tcg_pos/js/intake.js
 *
 * Implements:
 *   1. Zero-lag Web Audio API Synthesizer.
 *   2. Rapid Entry Matrix: [ Set ] [ # ] direct lookup & game switching.
 *   3. Ergonomic Condition Grading Matrix (NM, LP, MP, HP, DMG) with hotkeys [Q, W, E, R, T].
 *   4. Dynamic Cash vs. Store Credit (+30% Bonus) Trade-In Payout Calculations.
 *   5. Core Customer Store Credit Deposit Bridge.
 */

(() => {
  "use strict";

  // State Management
  let activeCard = null;
  let currentFinish = "nonfoil";
  let currentCondition = "NM";
  let currentQuantity = 1;
  let stagedQueue = [];
  let attachedCustomer = null;

  // DOM Elements - Search & Rapid Entry
  const searchInput = document.getElementById("card-search-input");
  const clearSearchBtn = document.getElementById("search-clear-btn");
  const dropdown = document.getElementById("autocomplete-dropdown");
  const searchSpinner = document.getElementById("search-spinner");
  const intakeGameSelect = document.getElementById("intake-game-select");

  const quickSetCodeInput = document.getElementById("quickSetCodeInput");
  const quickCollectorNumInput = document.getElementById("quickCollectorNumInput");
  const btnQuickLookup = document.getElementById("btnQuickLookup");

  // Inspector Elements
  const inspectorEmpty = document.getElementById("inspector-empty");
  const inspectorContent = document.getElementById("inspector-content");
  const cardArtImg = document.getElementById("card-art-img");
  const cardTitle = document.getElementById("card-title");
  const cardTypeLine = document.getElementById("card-type-line");
  const badgeGame = document.getElementById("badge-game");
  const badgeRarity = document.getElementById("badge-rarity");
  const badgeSet = document.getElementById("badge-set");
  const badgeCollector = document.getElementById("badge-collector");
  const badgeHp = document.getElementById("badge-hp");
  const badgeStage = document.getElementById("badge-stage");
  const badgeType = document.getElementById("badge-type");

  // Benchmarks
  const valMarket = document.getElementById("val-market");
  const valLow = document.getElementById("val-low");
  const valFoil = document.getElementById("val-foil");
  const valEtched = document.getElementById("val-etched");
  const labelFoil = document.getElementById("label-foil");
  const labelEtched = document.getElementById("label-etched");
  const finishBtnGroup = document.getElementById("finish-btn-group");

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
  const summaryTotalCreditPayout = document.getElementById("summary-total-credit-payout");
  const summaryTotalRetail = document.getElementById("summary-total-retail");
  const summaryMargin = document.getElementById("summary-margin");
  const btnCommitCash = document.getElementById("btn-commit-cash");
  const btnCommitCredit = document.getElementById("btn-commit-credit");

  // Customer Attach for Trade-In
  const intakeCustomerInput = document.getElementById("intakeCustomerInput");
  const btnIntakeLookupCustomer = document.getElementById("btnIntakeLookupCustomer");
  const intakeCustomerLabel = document.getElementById("intakeCustomerLabel");

  // Hardware Bridge & Tag Pairing Elements
  const inputTagId = document.getElementById("input-tag-id");
  const btnPairTag = document.getElementById("btn-pair-tag");
  const hardwareBadge = document.getElementById("hardwareStatusBadge");
  let isPairingTag = false;

  // Multipliers map
  const COND_MULTS = {
    NM: 1.00,
    LP: 0.85,
    MP: 0.70,
    HP: 0.50,
    DMG: 0.25
  };
  const CREDIT_BONUS = 0.30; // 30% store credit bonus

  // =========================================================================
  // 1. Web Audio API Synthesizer
  // =========================================================================
  let globalAudioCtx = null;
  let isBeeping = false;

  function getAudioContext() {
    if (!globalAudioCtx) {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (AudioContextClass) globalAudioCtx = new AudioContextClass();
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
    } catch(e) {
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
    } catch(e) {
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
      osc.frequency.setValueAtTime(880, ctx.currentTime);
      osc.frequency.exponentialRampToValueAtTime(440, ctx.currentTime + 0.5);
      gain.gain.setValueAtTime(0.45, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.5);
      osc.start();
      osc.stop(ctx.currentTime + 0.5);
    } catch (e) {}
  }

  // Pre-warm audio on gesture
  const unlockAudio = () => {
    getAudioContext();
    window.removeEventListener("click", unlockAudio);
    window.removeEventListener("keydown", unlockAudio);
  };
  window.addEventListener("click", unlockAudio);
  window.addEventListener("keydown", unlockAudio);

  // =========================================================================
  // 2. Toast Notifications & Helpers
  // =========================================================================
  function showToast(message, type = "info") {
    const container = document.getElementById("toast-container");
    if (!container) return;

    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `<span>${message}</span>`;
    container.appendChild(toast);

    setTimeout(() => {
      toast.style.transition = "opacity 0.3s ease, transform 0.3s ease";
      toast.style.opacity = "0";
      toast.style.transform = "translateX(50px)";
      setTimeout(() => toast.remove(), 300);
    }, 3800);
  }

  function fmt(val) {
    if (val === null || val === undefined || isNaN(val)) return "$0.00";
    return "$" + Number(val).toFixed(2);
  }

  function debounce(fn, delay) {
    let timer = null;
    return function (...args) {
      clearTimeout(timer);
      timer = setTimeout(() => fn.apply(this, args), delay);
    };
  }

  // =========================================================================
  // 3. Hardware Bridge & Scan Pairing
  // =========================================================================
  function setPairingState(active) {
    isPairingTag = active;
    if (!btnPairTag) return;
    if (isPairingTag) {
      btnPairTag.innerHTML = `<span>⏳ Tap Tag / Scan Barcode...</span>`;
      btnPairTag.style.background = "rgba(16, 185, 129, 0.2)";
      btnPairTag.style.borderColor = "rgba(16, 185, 129, 0.6)";
      btnPairTag.style.color = "#10B981";
      if (inputTagId) {
        inputTagId.placeholder = "Listening for hardware scan event...";
        inputTagId.focus();
      }
    } else {
      btnPairTag.innerHTML = `<span>📡 Pair Hardware Tag</span>`;
      btnPairTag.style.background = "rgba(56, 189, 248, 0.12)";
      btnPairTag.style.borderColor = "rgba(56, 189, 248, 0.35)";
      btnPairTag.style.color = "var(--tcg-accent-sky)";
      if (inputTagId) {
        inputTagId.placeholder = "Click 'Pair Hardware Tag' or scan/type 14-char UID or barcode...";
      }
    }
  }

  window.addEventListener("openpos:hardware-scan", (event) => {
    const detail = event.detail || {};
    const token = (detail.value || "").trim();
    if (!token) return;

    console.log("[Intake] Hardware scan intercepted:", detail);
    if (inputTagId) {
      inputTagId.value = token;
      playBeep();
      showToast(`Hardware tag paired: ${token}`, "success");
      setPairingState(false);
    }
  });

  if (window.hardwareBridge && hardwareBadge) {
    window.hardwareBridge.attachStatusBadge(hardwareBadge);
  }

  // =========================================================================
  // 4. Search & Rapid Direct Lookup
  // =========================================================================
  const performSearch = debounce(async (query) => {
    query = query.trim();
    if (!query || query.length < 2) {
      dropdown.style.display = "none";
      dropdown.innerHTML = "";
      return;
    }

    try {
      if (searchSpinner) searchSpinner.style.display = "inline-block";
      const game = intakeGameSelect ? intakeGameSelect.value : "mtg";
      const resp = await fetch(`/tcg/api/search?q=${encodeURIComponent(query)}&game=${encodeURIComponent(game)}`);
      const data = await resp.json();

      if (searchSpinner) searchSpinner.style.display = "none";

      if (data.success && data.results && data.results.length > 0) {
        renderAutocomplete(data.results);
      } else {
        dropdown.innerHTML = `<div style="padding: 1rem; color: var(--tcg-text-muted); text-align: center;">No matching cards found</div>`;
        dropdown.style.display = "block";
      }
    } catch (err) {
      if (searchSpinner) searchSpinner.style.display = "none";
      console.error("Search error:", err);
    }
  }, 220);

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

  // Rapid direct lookup: [ Set ] [ # ]
  async function performQuickLookup() {
    const setCode = (quickSetCodeInput.value || "").trim().toLowerCase();
    const collectorNum = (quickCollectorNumInput.value || "").trim();
    if (!setCode || !collectorNum) {
      showToast("Please enter both Set Code and Collector #.", "error");
      return;
    }

    const game = intakeGameSelect ? intakeGameSelect.value : "mtg";
    try {
      if (btnQuickLookup) btnQuickLookup.textContent = "Loading...";
      const resp = await fetch(`/tcg/api/lookup/${encodeURIComponent(setCode)}/${encodeURIComponent(collectorNum)}?game=${encodeURIComponent(game)}`);
      const data = await resp.json();

      if (data.success && data.card) {
        playBeep();
        selectCard(data.card);
        showToast(`Loaded ${data.card.name} (${setCode.toUpperCase()} #${collectorNum})`, "success");
      } else {
        playErrorBeep();
        showToast(`Card not found for ${setCode.toUpperCase()} #${collectorNum}`, "error");
      }
    } catch (err) {
      console.error("[QuickLookup] Error:", err);
      playErrorBeep();
      showToast("Network fault looking up card.", "error");
    } finally {
      if (btnQuickLookup) btnQuickLookup.textContent = "Lookup";
    }
  }

  if (btnQuickLookup) {
    btnQuickLookup.addEventListener("click", performQuickLookup);
  }
  [quickSetCodeInput, quickCollectorNumInput].forEach(el => {
    if (el) {
      el.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          performQuickLookup();
        }
      });
    }
  });

  // =========================================================================
  // 5. Card Selection & Inspector
  // =========================================================================
  function updateFinishOptions(game) {
    if (!finishBtnGroup) return;
    const isPokemon = (game || "").toLowerCase() === "pokemon";
    if (isPokemon) {
      finishBtnGroup.innerHTML = `
        <button type="button" class="btn-matrix btn-finish active" data-finish="nonfoil">Nonfoil (Regular)</button>
        <button type="button" class="btn-matrix btn-finish" data-finish="holo">Holo (Holofoil)</button>
        <button type="button" class="btn-matrix btn-finish" data-finish="reverse_holo">Reverse Holo</button>
        <button type="button" class="btn-matrix btn-finish" data-finish="first_edition">1st Edition</button>
      `;
      if (labelFoil) labelFoil.textContent = "Holo / Reverse";
      if (labelEtched) labelEtched.textContent = "Specialty / 1st Ed";
    } else {
      finishBtnGroup.innerHTML = `
        <button type="button" class="btn-matrix btn-finish active" data-finish="nonfoil">Nonfoil (Regular)</button>
        <button type="button" class="btn-matrix btn-finish" data-finish="foil">Foil / Holo</button>
        <button type="button" class="btn-matrix btn-finish" data-finish="etched">Etched / Specialty</button>
      `;
      if (labelFoil) labelFoil.textContent = "Foil / Holo";
      if (labelEtched) labelEtched.textContent = "Etched / Specialty";
    }
    finishBtnGroup.querySelectorAll(".btn-finish").forEach((btn) => {
      btn.addEventListener("click", () => setFinish(btn.dataset.finish));
    });
    setFinish("nonfoil");
  }

  function selectCard(card) {
    activeCard = card;
    dropdown.style.display = "none";
    dropdown.innerHTML = "";
    searchInput.value = `${card.name} (${card.set_code.toUpperCase()} #${card.collector_number})`;
    clearSearchBtn.style.display = "block";

    // Populate quick inputs for consistency
    if (quickSetCodeInput) quickSetCodeInput.value = card.set_code.toUpperCase();
    if (quickCollectorNumInput) quickCollectorNumInput.value = card.collector_number;

    inspectorEmpty.style.display = "none";
    inspectorContent.style.display = "block";

    const cardGame = (card.game || (intakeGameSelect ? intakeGameSelect.value : "mtg")).toLowerCase();
    if (intakeGameSelect && intakeGameSelect.value !== cardGame) {
      intakeGameSelect.value = cardGame;
      updateFinishOptions(cardGame);
    }

    cardTitle.textContent = card.name;
    const meta = card.api_metadata || {};

    if (badgeGame) {
      badgeGame.textContent = cardGame.toUpperCase();
      badgeGame.style.background = cardGame === "pokemon" ? "#ef4444" : "#6366f1";
    }

    if (cardGame === "pokemon") {
      cardTypeLine.textContent = meta.category || "Pokémon Card";
      if (badgeHp) {
        badgeHp.style.display = meta.hp ? "inline-block" : "none";
        badgeHp.textContent = meta.hp ? `${meta.hp} HP` : "";
      }
      if (badgeStage) {
        badgeStage.style.display = meta.stage ? "inline-block" : "none";
        badgeStage.textContent = meta.stage || "";
      }
      if (badgeType) {
        const types = meta.types || [];
        badgeType.style.display = types.length > 0 ? "inline-block" : "none";
        badgeType.textContent = types.join(" / ");
      }
    } else {
      cardTypeLine.textContent = card.type_line || meta.type_line || "Magic: The Gathering Single";
      if (badgeHp) badgeHp.style.display = "none";
      if (badgeStage) badgeStage.style.display = "none";
      if (badgeType) badgeType.style.display = "none";
    }

    badgeRarity.textContent = card.rarity || "Common";
    badgeSet.textContent = `${card.set_name || card.set_code.toUpperCase()} (${card.set_code.toUpperCase()})`;
    badgeCollector.textContent = `#${card.collector_number}`;

    const artUrl = card.cached_image_path || card.image_uri || "/tcg/addons/tcg_pos/static/placeholder_card.png";
    cardArtImg.src = artUrl;

    valMarket.textContent = fmt(card.market_price);
    valLow.textContent = fmt(card.low_price);
    valFoil.textContent = fmt(card.foil_price);
    valEtched.textContent = fmt(card.etched_price);

    setCondition("NM");
    setQuantity(1);
  }

  function setFinish(finish) {
    currentFinish = finish;
    if (finishBtnGroup) {
      finishBtnGroup.querySelectorAll(".btn-finish").forEach((btn) => {
        btn.classList.toggle("active", btn.dataset.finish === finish);
      });
    }
    recalculatePrices();
  }

  function setCondition(cond) {
    currentCondition = cond;
    document.querySelectorAll(".btn-cond").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.cond === cond);
    });
    recalculatePrices();
  }

  function setQuantity(qty) {
    qty = Math.max(1, parseInt(qty) || 1);
    currentQuantity = qty;
    inputQuantity.value = qty;
  }

  function resolveApplicablePrice() {
    if (!activeCard) return 0.0;
    const isFoil = ["foil", "holo", "reverse_holo"].includes(currentFinish);
    const isSpecialty = ["etched", "first_edition"].includes(currentFinish);

    if (isSpecialty && activeCard.etched_price) return activeCard.etched_price;
    if (isFoil && activeCard.foil_price) return activeCard.foil_price;
    if (activeCard.market_price) return activeCard.market_price;
    if (activeCard.low_price) return activeCard.low_price;
    return 0.0;
  }

  function recalculatePrices() {
    if (!activeCard) return;

    const basePrice = resolveApplicablePrice();
    const condMultiplier = COND_MULTS[currentCondition] || 1.0;
    const conditionAdjustedPrice = basePrice * condMultiplier;

    const cashOffer = conditionAdjustedPrice * 0.50;
    const creditOffer = conditionAdjustedPrice * 0.50 * (1.0 + CREDIT_BONUS);

    offerCashVal.textContent = fmt(cashOffer);
    offerCreditVal.textContent = fmt(creditOffer);

    inputCostBasis.value = cashOffer.toFixed(2);
    inputSellPrice.value = conditionAdjustedPrice.toFixed(2);
  }

  // =========================================================================
  // 6. Staging & Staged Buylist Batch
  // =========================================================================
  function stageCurrentCard() {
    if (!activeCard) {
      playErrorBeep();
      showToast("No card currently selected for staging.", "error");
      return;
    }

    const qty = Math.max(1, parseInt(inputQuantity.value) || 1);
    const costBasis = Math.max(0, parseFloat(inputCostBasis.value) || 0.0);
    const sellPrice = Math.max(0, parseFloat(inputSellPrice.value) || 0.0);
    const tagId = inputTagId ? inputTagId.value.trim() : "";

    const existingIndex = stagedQueue.findIndex(
      (item) =>
        item.provider_card_id === activeCard.provider_card_id &&
        item.finish === currentFinish &&
        item.condition === currentCondition
    );

    if (existingIndex > -1) {
      const existing = stagedQueue[existingIndex];
      const totalQty = existing.quantity + qty;
      const newCost = ((existing.quantity * existing.cost_basis) + (qty * costBasis)) / totalQty;

      existing.quantity = totalQty;
      existing.cost_basis = Number(newCost.toFixed(4));
      existing.sell_price = sellPrice;
      showToast(`Updated ${activeCard.name} in staged queue (+${qty}).`, "info");
    } else {
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
        custom_tag_id: tagId || null,
        api_metadata: activeCard.api_metadata || {}
      };
      stagedQueue.push(stagedItem);
      if (inputTagId) inputTagId.value = "";
      setPairingState(false);
      showToast(`Added ${qty}x ${activeCard.name} to staged queue.`, "success");
    }

    playBeep();
    renderQueue();
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
      queueEmptyState.style.display = "block";
      queueBadgeCount.textContent = "0 Cards";
      summaryTotalCards.textContent = "0";
      summaryTotalCost.textContent = "$0.00";
      summaryTotalCreditPayout.textContent = "$0.00";
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
          <strong>${escapeHtml(item.name)}</strong>
          ${item.custom_tag_id ? `<span style="font-family: monospace; font-size: 0.68rem; color: var(--tcg-accent-sky); background: rgba(56, 189, 248, 0.15); padding: 0.1rem 0.35rem; border-radius: 4px; margin-left: 0.35rem;">🏷️ ${escapeHtml(item.custom_tag_id)}</span>` : ""}
          <div style="font-size: 0.72rem; color: var(--tcg-text-muted); text-transform: uppercase;">
            ${escapeHtml(item.set_code)} #${escapeHtml(item.collector_number)}
          </div>
        </td>
        <td><span class="badge badge-set">${escapeHtml(item.set_code.toUpperCase())}</span></td>
        <td>
          <span style="text-transform: capitalize;">${escapeHtml(item.finish)}</span> /
          <strong style="color: ${getCondColor(item.condition)};">${escapeHtml(item.condition)}</strong>
        </td>
        <td style="text-align: center;"><strong style="font-size: 0.92rem; font-family:'JetBrains Mono',monospace;">${item.quantity}</strong></td>
        <td style="text-align: right; font-family:'JetBrains Mono',monospace;">${fmt(item.cost_basis)}</td>
        <td style="text-align: right; font-family:'JetBrains Mono',monospace;">${fmt(item.sell_price)}</td>
        <td style="text-align: center;">
          <button type="button" class="btn-del-row" data-index="${index}" title="Remove item" style="background:none; border:none; color:var(--tcg-accent-rose); cursor:pointer; font-size:1.15rem; line-height:1;">&times;</button>
        </td>
      `;
      queueTableBody.appendChild(tr);
    });

    document.querySelectorAll(".btn-del-row").forEach((btn) => {
      btn.onclick = () => removeFromQueue(parseInt(btn.dataset.index));
    });

    // Calculations
    const totalCreditPayout = totalCost * (1.0 + CREDIT_BONUS);
    const marginDol = totalRetail - totalCost;
    const marginPct = totalRetail > 0 ? ((marginDol / totalRetail) * 100).toFixed(1) : 0;

    queueBadgeCount.textContent = `${totalCards} ${totalCards === 1 ? "Card" : "Cards"}`;
    summaryTotalCards.textContent = totalCards;
    summaryTotalCost.textContent = fmt(totalCost);
    summaryTotalCreditPayout.textContent = fmt(totalCreditPayout);
    summaryTotalRetail.textContent = fmt(totalRetail);
    summaryMargin.textContent = `${fmt(marginDol)} (${marginPct}%)`;

    btnCommitCash.disabled = false;
    btnCommitCredit.disabled = false;
  }

  function getCondColor(cond) {
    switch (cond) {
      case "NM": return "#10B981";
      case "LP": return "#3B82F6";
      case "MP": return "#EAB308";
      case "HP": return "#F97316";
      case "DMG": return "#EF4444";
      default: return "#F8FAFC";
    }
  }

  // =========================================================================
  // 7. Customer Lookup for Trade-In
  // =========================================================================
  async function performIntakeCustomerLookup() {
    const q = (intakeCustomerInput.value || "").trim();
    if (!q) return;

    try {
      const resp = await fetch(`/tcg/api/register/customer?q=${encodeURIComponent(q)}`);
      const data = await resp.json();

      if (data.found && data.customer) {
        attachedCustomer = data.customer;
        const balance = Number(data.customer.store_credit_balance || data.customer.balance || 0);
        intakeCustomerLabel.textContent = `${data.customer.name} (Balance: $${balance.toFixed(2)})`;
        intakeCustomerLabel.style.color = "var(--tcg-accent-emerald)";
        showToast(`Trade-in customer attached: ${data.customer.name}`, "success");
      } else {
        playErrorBeep();
        attachedCustomer = null;
        intakeCustomerLabel.textContent = "Not Found";
        intakeCustomerLabel.style.color = "var(--tcg-accent-rose)";
        showToast(`Customer not found for "${q}"`, "error");
      }
    } catch (err) {
      console.error("Intake customer lookup error:", err);
      showToast("Customer service error.", "error");
    }
  }

  if (btnIntakeLookupCustomer) {
    btnIntakeLookupCustomer.addEventListener("click", performIntakeCustomerLookup);
  }
  if (intakeCustomerInput) {
    intakeCustomerInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        performIntakeCustomerLookup();
      }
    });
  }

  // =========================================================================
  // 8. Batch Commit Payout (Cash vs Store Credit)
  // =========================================================================
  async function commitBatch(payoutType = "cash") {
    if (stagedQueue.length === 0) return;

    if (payoutType === "store_credit" && !attachedCustomer) {
      playErrorBeep();
      showToast("Please lookup and attach a customer account for Store Credit payout.", "error");
      if (intakeCustomerInput) intakeCustomerInput.focus();
      return;
    }

    const actionTitle = payoutType === "cash" ? "Cash Buyout" : "Store Credit Trade-In";
    btnCommitCash.disabled = true;
    btnCommitCredit.disabled = true;

    const calcCash = stagedQueue.reduce((sum, i) => sum + (i.cost_basis * i.quantity), 0);
    const totalPayout = payoutType === "store_credit" ? (calcCash * (1.0 + CREDIT_BONUS)) : calcCash;

    const payload = {
      items: stagedQueue,
      payout_type: payoutType === "store_credit" ? "store_credit" : "cash",
      customer_id: attachedCustomer ? attachedCustomer.id : null,
      total_payout: totalPayout
    };

    try {
      const resp = await fetch("/tcg/api/intake/commit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });

      const res = await resp.json();
      if (res.success) {
        playChime();
        const payoutFormatted = fmt(totalPayout);
        showToast(`Committed ${res.updated_count} items via ${actionTitle}! Total Payout: ${payoutFormatted}`, "success");
        stagedQueue = [];
        renderQueue();
        if (searchInput) searchInput.focus();
      } else {
        playErrorBeep();
        showToast(`Commit failed: ${res.error || "Unknown server error"}`, "error");
        btnCommitCash.disabled = false;
        btnCommitCredit.disabled = false;
      }
    } catch (err) {
      console.error("Batch commit error:", err);
      playErrorBeep();
      showToast("Network error executing intake commit.", "error");
      btnCommitCash.disabled = false;
      btnCommitCredit.disabled = false;
    }
  }

  // =========================================================================
  // 9. Event Listeners & Hotkeys
  // =========================================================================
  document.addEventListener("DOMContentLoaded", () => {
    if (intakeGameSelect) {
      intakeGameSelect.addEventListener("change", (e) => {
        const game = e.target.value;
        updateFinishOptions(game);
        if (searchInput) {
          searchInput.placeholder = game === "pokemon"
            ? "Search Pokémon cards by name, set, or collector number (e.g. Pikachu, swsh3 136)..."
            : "Search card by name or scan barcode (e.g. Sol Ring, Black Lotus)...";
        }
      });
      updateFinishOptions(intakeGameSelect.value);
    }

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
        e.preventDefault();
        performSearch(searchInput.value);
      }
    });

    document.addEventListener("click", (e) => {
      if (!e.target.closest(".search-wrapper")) {
        dropdown.style.display = "none";
      }
    });

    document.querySelectorAll(".btn-cond").forEach((btn) => {
      btn.addEventListener("click", () => setCondition(btn.dataset.cond));
    });

    btnQtyMinus.addEventListener("click", () => setQuantity(currentQuantity - 1));
    btnQtyPlus.addEventListener("click", () => setQuantity(currentQuantity + 1));
    inputQuantity.addEventListener("change", (e) => setQuantity(e.target.value));

    if (btnPairTag) {
      btnPairTag.addEventListener("click", () => setPairingState(!isPairingTag));
    }
    btnStageCard.addEventListener("click", stageCurrentCard);
    btnClearQueue.addEventListener("click", clearQueue);
    btnCommitCash.addEventListener("click", () => commitBatch("cash"));
    btnCommitCredit.addEventListener("click", () => commitBatch("store_credit"));

    // Hotkeys:
    //   /       : focus search
    //   Space   : stage card
    //   1-4     : finish selection
    //   Q,W,E,R,T : condition grading (NM, LP, MP, HP, DMG)
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
        const isPokemon = (intakeGameSelect ? intakeGameSelect.value : (activeCard.game || "")).toLowerCase() === "pokemon";
        if (e.key === "1") setFinish("nonfoil");
        if (e.key === "2") setFinish(isPokemon ? "holo" : "foil");
        if (e.key === "3") setFinish(isPokemon ? "reverse_holo" : "etched");
        if (e.key === "4" && isPokemon) setFinish("first_edition");

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
