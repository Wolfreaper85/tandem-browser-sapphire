/**
 * SapphireBackend — Connects Wingman chat to Sapphire AI
 * Implements ChatBackend interface (see src/chat/interfaces.ts)
 *
 * Communication is handled by the Sapphire plugin's bridge thread (tandem.py):
 *   - Wingman → Sapphire: robin messages forwarded to Sapphire (with busy-check)
 *   - Sapphire → Wingman: AI replies mirrored into the Wingman panel
 *   - All messages are posted to Tandem /chat, Tandem's IPC displays them
 *
 * This backend just shows "connected" — no polling needed (avoids duplicates).
 */
class SapphireBackend {
  constructor() {
    this.id = 'sapphire';
    this.name = 'Sapphire';
    this.icon = '💎';

    this._connected = false;
    this._tandemApi = 'http://127.0.0.1:8765';

    this._messageCallbacks = [];
    this._typingCallbacks = [];
    this._connectionCallbacks = [];
  }

  async connect() {
    try {
      // Check if Tandem is running (local HTTP — no SSL issues)
      const res = await fetch(`${this._tandemApi}/status`);
      if (res.ok) {
        // Bridge thread in tandem.py handles actual Sapphire communication
        this._setConnected(true);
      } else {
        this._setConnected(false);
      }
    } catch (e) {
      console.warn('[SapphireBackend] Connection check failed:', e.message);
      this._setConnected(false);
    }
  }

  async disconnect() {
    this._setConnected(false);
  }

  isConnected() {
    return this._connected;
  }

  async sendMessage(text) {
    // Messages typed in Wingman are posted to /chat as "robin" by wingman.js.
    // The bridge thread picks them up and forwards to Sapphire.
    // Reply comes back as "wingman" via /chat — Tandem IPC displays it.
    // Nothing to do here — no duplicate display needed.
    if (!text) return;
  }

  onMessage(cb) { this._messageCallbacks.push(cb); }
  onTyping(cb) { this._typingCallbacks.push(cb); }
  onConnectionChange(cb) { this._connectionCallbacks.push(cb); }

  // ── Private ────────────────────────────────────

  _setConnected(connected) {
    if (this._connected !== connected) {
      this._connected = connected;
      for (const cb of this._connectionCallbacks) cb(connected);
    }
  }

  _emit(type, data) {
    if (type === 'message' || type === 'historyReload') {
      for (const cb of this._messageCallbacks) cb(data, type);
    } else if (type === 'typing') {
      for (const cb of this._typingCallbacks) cb(data);
    }
  }
}
