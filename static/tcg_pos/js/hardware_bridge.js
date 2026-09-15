/**
 * OpenPOS TCG Addon - Universal Hardware Event Bridge
 * File: static/tcg_pos/js/hardware_bridge.js
 *
 * Establishes an auto-reconnecting Server-Sent Events (SSE) stream to
 * /hardware/events/stream, normalizes physical scanner tokens (NFC UIDs,
 * 1D barcodes, 2D QR payloads), and dispatches 'openpos:hardware-scan' DOM events.
 * Provides synthesized Web Audio API sound chimes for instant user feedback.
 */

class OpenPOSHardwareBridge {
  constructor(options = {}) {
    this.streamUrl = options.streamUrl || "/hardware/events/stream";
    this.statusUrl = options.statusUrl || "/hardware/status";
    this.reconnectDelay = options.initialReconnectDelay || 1500;
    this.maxReconnectDelay = options.maxReconnectDelay || 15000;
    this.currentReconnectDelay = this.reconnectDelay;
    
    this.eventSource = null;
    this.connected = false;
    this.audioContext = null;
    this.audioEnabled = true;
    this.statusBadges = new Set();
    
    this.initAudio();
    this.connect();
  }

  /**
   * Initializes Web Audio Context lazily upon first interaction or construction.
   */
  initAudio() {
    try {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (AudioCtx) {
        this.audioContext = new AudioCtx();
      }
    } catch (e) {
      console.warn("[HardwareBridge] Web Audio not supported:", e);
    }
  }

  ensureAudioResumed() {
    if (this.audioContext && this.audioContext.state === "suspended") {
      this.audioContext.resume().catch(() => {});
    }
  }

  /**
   * Plays a pleasant, high-pitch double chime for successful scans.
   */
  chimeSuccess() {
    if (!this.audioEnabled) return;
    this.ensureAudioResumed();
    if (!this.audioContext) return;

    try {
      const now = this.audioContext.currentTime;
      const osc = this.audioContext.createOscillator();
      const gain = this.audioContext.createGain();

      osc.type = "sine";
      // First beep 880Hz (A5), second ramp to 1760Hz (A6)
      osc.frequency.setValueAtTime(880, now);
      osc.frequency.setValueAtTime(1760, now + 0.08);

      gain.gain.setValueAtTime(0.12, now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.22);

      osc.connect(gain);
      gain.connect(this.audioContext.destination);

      osc.start(now);
      osc.stop(now + 0.22);
    } catch (e) {
      console.debug("[HardwareBridge] Audio chime error:", e);
    }
  }

  /**
   * Plays a low-pitch rejection buzz for unknown / not-found items.
   */
  chimeError() {
    if (!this.audioEnabled) return;
    this.ensureAudioResumed();
    if (!this.audioContext) return;

    try {
      const now = this.audioContext.currentTime;
      const osc = this.audioContext.createOscillator();
      const gain = this.audioContext.createGain();

      osc.type = "sawtooth";
      osc.frequency.setValueAtTime(220, now);
      osc.frequency.linearRampToValueAtTime(140, now + 0.25);

      gain.gain.setValueAtTime(0.15, now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.25);

      osc.connect(gain);
      gain.connect(this.audioContext.destination);

      osc.start(now);
      osc.stop(now + 0.25);
    } catch (e) {
      console.debug("[HardwareBridge] Audio error chime error:", e);
    }
  }

  /**
   * Establishes or re-establishes the SSE stream with exponential backoff.
   */
  connect() {
    if (this.eventSource) {
      try {
        this.eventSource.close();
      } catch (e) {}
      this.eventSource = null;
    }

    try {
      this.eventSource = new EventSource(this.streamUrl);

      this.eventSource.onopen = () => {
        this.connected = true;
        this.currentReconnectDelay = this.reconnectDelay;
        console.log("[HardwareBridge] Connected to hardware stream:", this.streamUrl);
        this.updateBadges(true);
      };

      this.eventSource.onerror = (err) => {
        this.connected = false;
        this.updateBadges(false);
        try {
          this.eventSource.close();
        } catch (e) {}
        this.eventSource = null;

        console.warn(`[HardwareBridge] SSE disconnected. Reconnecting in ${this.currentReconnectDelay}ms...`);
        setTimeout(() => this.connect(), this.currentReconnectDelay);
        this.currentReconnectDelay = Math.min(this.currentReconnectDelay * 1.5, this.maxReconnectDelay);
      };

      // General message listener
      this.eventSource.onmessage = (event) => {
        this.handleEventPayload(event.data);
      };

      // Named hardware event listeners
      const eventNames = ["tag_scanned", "barcode_scanned", "qr_scanned", "hardware_token", "message"];
      eventNames.forEach((name) => {
        this.eventSource.addEventListener(name, (event) => {
          this.handleEventPayload(event.data);
        });
      });

    } catch (err) {
      console.error("[HardwareBridge] Initialization error:", err);
      this.connected = false;
      this.updateBadges(false);
      setTimeout(() => this.connect(), this.currentReconnectDelay);
    }
  }

  /**
   * Normalizes incoming event payload and dispatches CustomEvent on window.
   */
  handleEventPayload(rawString) {
    if (!rawString) return;
    try {
      const data = typeof rawString === "string" ? JSON.parse(rawString) : rawString;
      if (data.status === "connected") return; // Handshake ping

      const value = String(data.value || "").trim();
      if (!value) return;

      const detail = {
        eventId: data.event_id || null,
        source: data.source || "hardware_hub",
        tokenType: data.token_type || "raw_string",
        value: value,
        timestamp: data.timestamp || Date.now() / 1000,
        raw: data
      };

      console.debug("[HardwareBridge] Token received:", detail);

      // Dispatch global window CustomEvent
      window.dispatchEvent(
        new CustomEvent("openpos:hardware-scan", {
          detail: detail,
          bubbles: true,
          cancelable: true
        })
      );

    } catch (err) {
      console.debug("[HardwareBridge] Non-JSON stream message:", rawString);
    }
  }

  /**
   * Binds a DOM element to track connection state visually.
   */
  attachStatusBadge(elementOrId) {
    const el = typeof elementOrId === "string" ? document.getElementById(elementOrId) : elementOrId;
    if (el) {
      this.statusBadges.add(el);
      this.updateBadgeElement(el, this.connected);
    }
  }

  updateBadges(isConnected) {
    this.statusBadges.forEach((el) => this.updateBadgeElement(el, isConnected));
  }

  updateBadgeElement(el, isConnected) {
    if (!el) return;
    if (isConnected) {
      el.textContent = "Hardware Connected";
      el.classList.add("hardware-online");
      el.classList.remove("hardware-offline");
      el.title = "Connected to /hardware/events/stream";
    } else {
      el.textContent = "Hardware Offline";
      el.classList.add("hardware-offline");
      el.classList.remove("hardware-online");
      el.title = "Awaiting connection to Hardware Hub...";
    }
  }

  isConnected() {
    return this.connected;
  }
}

// Global browser instance
window.hardwareBridge = new OpenPOSHardwareBridge();
