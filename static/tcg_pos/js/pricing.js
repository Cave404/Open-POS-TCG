/**
 * OpenPOS-TCG: Market Pricing Engine Client
 * Handles real-time polling of background price sync jobs,
 * parameter dispatching, and price volatility alert reviews.
 */

(() => {
  "use strict";

  // State Management
  let pollingTimer = null;
  let activeJobStatus = "idle";

  // DOM Elements - Config & Actions
  const jobStatusPill = document.getElementById("job-status-pill");
  const gameToggleLabels = document.querySelectorAll(".game-toggle-btn");
  const checkInStockOnly = document.getElementById("check-in-stock-only");
  const selectMaxAge = document.getElementById("select-max-age");
  const checkAutoAdjust = document.getElementById("check-auto-adjust");
  const inputMarginMult = document.getElementById("input-margin-mult");
  const btnStartSync = document.getElementById("btn-start-sync");
  const btnCancelSync = document.getElementById("btn-cancel-sync");

  // DOM Elements - Progress Deck
  const progressDeck = document.getElementById("progress-deck");
  const progressBarFill = document.getElementById("progress-bar-fill");
  const progressPctLabel = document.getElementById("progress-pct-label");
  const currentCardLabel = document.getElementById("current-card-label");
  const statProgressCount = document.getElementById("stat-progress-count");
  const statUpdatedCount = document.getElementById("stat-updated-count");
  const statAlertsCount = document.getElementById("stat-alerts-count");
  const statElapsedTime = document.getElementById("stat-elapsed-time");

  // DOM Elements - Volatility Alerts Table
  const alertsTableBody = document.getElementById("alerts-table-body");
  const alertsEmptyState = document.getElementById("alerts-empty-state");
  const btnRefreshAlerts = document.getElementById("btn-refresh-alerts");

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

  /** Gets currently selected game filter value */
  function getSelectedGame() {
    const checked = document.querySelector('input[name="game-filter"]:checked');
    if (!checked || checked.value === "all") return null;
    return checked.value;
  }

  /** Updates game toggle button active styling */
  function syncGameSelectorUI() {
    gameToggleLabels.forEach((lbl) => {
      const radio = lbl.querySelector('input[type="radio"]');
      if (radio && radio.checked) {
        lbl.classList.add("active");
      } else {
        lbl.classList.remove("active");
      }
    });
  }

  /** Update job status pill */
  function setStatusPill(status) {
    activeJobStatus = (status || "idle").toLowerCase();
    jobStatusPill.className = `status-pill ${activeJobStatus}`;
    jobStatusPill.textContent = activeJobStatus.charAt(0).toUpperCase() + activeJobStatus.slice(1);
  }

  /** Polls background pricing worker status */
  async function pollStatus() {
    try {
      const resp = await fetch("/tcg/api/pricing/status");
      const data = await resp.json();

      if (!data.success) return;

      setStatusPill(data.status);

      if (data.status === "running") {
        progressDeck.style.display = "block";
        btnStartSync.disabled = true;
        btnCancelSync.disabled = false;

        const pct = data.progress_percent || 0.0;
        progressBarFill.style.width = `${pct}%`;
        progressPctLabel.textContent = `${pct.toFixed(1)}%`;

        currentCardLabel.textContent = data.current_card_name || "Synchronizing...";
        statProgressCount.textContent = `${data.processed_items} / ${data.total_items}`;
        statUpdatedCount.textContent = data.updated_items;
        statAlertsCount.textContent = data.volatility_alerts_count;
        statElapsedTime.textContent = `${data.duration_seconds || 0.0}s`;

      } else if (data.status === "completed" || data.status === "cancelled" || data.status === "failed") {
        btnStartSync.disabled = false;
        btnCancelSync.disabled = true;

        if (data.total_items > 0) {
          progressDeck.style.display = "block";
          const pct = data.progress_percent || 100.0;
          progressBarFill.style.width = `${pct}%`;
          progressPctLabel.textContent = `${pct.toFixed(1)}%`;
          statProgressCount.textContent = `${data.processed_items} / ${data.total_items}`;
          statUpdatedCount.textContent = data.updated_items;
          statAlertsCount.textContent = data.volatility_alerts_count;
          statElapsedTime.textContent = `${data.duration_seconds || 0.0}s`;
          currentCardLabel.textContent = data.status === "completed" ? "Batch synchronization finished." : `Job ${data.status}.`;
        }

        // Stop polling interval
        if (pollingTimer) {
          clearInterval(pollingTimer);
          pollingTimer = null;
        }

        // Refresh alerts review table
        loadAlerts();
      } else {
        // Idle state
        btnStartSync.disabled = false;
        btnCancelSync.disabled = true;
      }
    } catch (err) {
      console.error("Failed to poll pricing status:", err);
    }
  }

  /** Starts the continuous status polling loop */
  function startPolling() {
    if (pollingTimer) clearInterval(pollingTimer);
    pollStatus();
    pollingTimer = setInterval(pollStatus, 1500);
  }

  /** Triggers a new market refresh job */
  async function triggerRefresh() {
    const game = getSelectedGame();
    const inStockOnly = checkInStockOnly.checked;
    const maxAgeDays = parseInt(selectMaxAge.value) || 0;
    const autoAdjust = checkAutoAdjust.checked;
    const marginMult = parseFloat(inputMarginMult.value) || 1.0;

    btnStartSync.disabled = true;

    try {
      const resp = await fetch("/tcg/api/pricing/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          game: game,
          in_stock_only: inStockOnly,
          max_age_days: maxAgeDays,
          auto_adjust_sell_price: autoAdjust,
          margin_multiplier: marginMult
        })
      });

      const res = await resp.json();

      if (res.success) {
        showToast("Market price sync job started.", "success");
        setStatusPill("running");
        progressDeck.style.display = "block";
        progressBarFill.style.width = "0%";
        progressPctLabel.textContent = "0.0%";
        currentCardLabel.textContent = "Initializing worker...";
        startPolling();
      } else {
        showToast(res.error || "Failed to start refresh job.", "error");
        btnStartSync.disabled = false;
      }
    } catch (err) {
      console.error("Failed to start refresh:", err);
      showToast("Network error starting refresh job.", "error");
      btnStartSync.disabled = false;
    }
  }

  /** Requests cancellation of active job */
  async function cancelRefresh() {
    btnCancelSync.disabled = true;
    try {
      const resp = await fetch("/tcg/api/pricing/cancel", { method: "POST" });
      const res = await resp.json();
      if (res.success) {
        showToast("Cancellation signal sent to worker.", "info");
      } else {
        showToast(res.message || "Could not cancel job.", "warning");
      }
    } catch (err) {
      console.error("Failed to cancel refresh:", err);
      showToast("Network error requesting cancellation.", "error");
    }
  }

  /** Loads Price Volatility Drift Alerts */
  async function loadAlerts() {
    try {
      const resp = await fetch("/tcg/api/pricing/alerts");
      const data = await resp.json();

      if (!data.success) return;

      alertsTableBody.innerHTML = "";

      if (!data.alerts || data.alerts.length === 0) {
        alertsEmptyState.style.display = "block";
        return;
      }

      alertsEmptyState.style.display = "none";

      data.alerts.forEach((alert) => {
        const tr = document.createElement("tr");
        tr.id = `alert-row-${alert.id}`;

        const drift = alert.drift || {};
        const oldPrice = drift.old !== undefined ? fmt(drift.old) : "--";
        const newPrice = drift.new !== undefined ? fmt(drift.new) : "--";
        const pct = drift.change_pct !== undefined ? drift.change_pct : 0.0;
        const isSpike = pct >= 0;
        const badgeClass = isSpike ? "diff-badge spike" : "diff-badge crash";
        const sign = isSpike ? "+" : "";

        const gameBadgeColor = (alert.game || "").toLowerCase() === "pokemon" ? "#ef4444" : "#6366f1";

        tr.innerHTML = `
          <td>
            <div style="font-weight: 700; color: var(--text-main);">${alert.name}</div>
            <div style="font-size: 0.76rem; color: var(--text-dim); text-transform: uppercase;">
              ${alert.set_code.toUpperCase()} #${alert.collector_number} &bull; ${alert.set_name}
            </div>
          </td>
          <td>
            <span class="badge" style="background: ${gameBadgeColor}; color: #fff; text-transform: uppercase;">
              ${alert.game}
            </span>
          </td>
          <td>
            <span style="text-transform: capitalize; font-weight: 500;">${alert.finish}</span>
            <span class="badge badge-set" style="margin-left: 0.35rem;">${alert.condition}</span>
          </td>
          <td style="color: var(--text-muted); font-variant-numeric: tabular-nums;">${oldPrice}</td>
          <td style="font-weight: 700; color: var(--text-main); font-variant-numeric: tabular-nums;">${newPrice}</td>
          <td>
            <span class="${badgeClass}">${sign}${pct.toFixed(1)}%</span>
          </td>
          <td style="font-weight: 700; color: #34d399; font-variant-numeric: tabular-nums;">${fmt(alert.sell_price)}</td>
          <td class="action-buttons-cell">
            <button type="button" class="btn-apply" data-id="${alert.id}" data-name="${alert.name}" title="Update shelf price to match new market price">
              Apply to Shelf
            </button>
            <button type="button" class="btn-dismiss" data-id="${alert.id}" data-name="${alert.name}" title="Dismiss alert and keep current shelf price">
              Dismiss
            </button>
          </td>
        `;

        tr.querySelector(".btn-apply").onclick = () => applyAlert(alert.id, alert.name);
        tr.querySelector(".btn-dismiss").onclick = () => dismissAlert(alert.id, alert.name);

        alertsTableBody.appendChild(tr);
      });
    } catch (err) {
      console.error("Failed to load volatility alerts:", err);
    }
  }

  /** Applies new market price to live shelf price */
  async function applyAlert(itemId, cardName) {
    try {
      const resp = await fetch(`/tcg/api/pricing/alerts/${itemId}/apply`, {
        method: "POST",
        headers: { "Content-Type": "application/json" }
      });
      const res = await resp.json();
      if (res.success) {
        showToast(`Updated shelf price for ${cardName} to ${fmt(res.new_sell_price)}.`, "success");
        const row = document.getElementById(`alert-row-${itemId}`);
        if (row) row.remove();
        if (alertsTableBody.children.length === 0) {
          alertsEmptyState.style.display = "block";
        }
      } else {
        showToast(res.error || "Failed to apply price.", "error");
      }
    } catch (err) {
      console.error("Failed to apply alert:", err);
      showToast("Network error applying price.", "error");
    }
  }

  /** Dismisses price alert without modifying shelf price */
  async function dismissAlert(itemId, cardName) {
    try {
      const resp = await fetch(`/tcg/api/pricing/alerts/${itemId}/dismiss`, {
        method: "POST"
      });
      const res = await resp.json();
      if (res.success) {
        showToast(`Dismissed price alert for ${cardName}.`, "info");
        const row = document.getElementById(`alert-row-${itemId}`);
        if (row) row.remove();
        if (alertsTableBody.children.length === 0) {
          alertsEmptyState.style.display = "block";
        }
      } else {
        showToast(res.error || "Failed to dismiss alert.", "error");
      }
    } catch (err) {
      console.error("Failed to dismiss alert:", err);
      showToast("Network error dismissing alert.", "error");
    }
  }

  // --- Event Listeners & Initialization ---

  document.addEventListener("DOMContentLoaded", () => {
    // Game radio toggles
    document.querySelectorAll('input[name="game-filter"]').forEach((radio) => {
      radio.addEventListener("change", syncGameSelectorUI);
    });

    // Auto-adjust safety switch toggle enables/disables margin multiplier input
    if (checkAutoAdjust && inputMarginMult) {
      checkAutoAdjust.addEventListener("change", (e) => {
        inputMarginMult.disabled = !e.target.checked;
      });
    }

    // Buttons
    if (btnStartSync) btnStartSync.addEventListener("click", triggerRefresh);
    if (btnCancelSync) btnCancelSync.addEventListener("click", cancelRefresh);
    if (btnRefreshAlerts) btnRefreshAlerts.addEventListener("click", loadAlerts);

    // Initial sync of UI state & data fetch
    syncGameSelectorUI();
    pollStatus();
    loadAlerts();
  });
})();
