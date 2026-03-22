// Injection Alert UI — subscribes to SSE injection alerts and shows modal overlays
// Matches upstream hydro13 v0.64/v0.65 visual behavior

(function () {
  'use strict';

  const API_BASE = 'http://127.0.0.1:8765/api';
  let eventSource = null;

  function connect() {
    if (eventSource) return;
    eventSource = new EventSource(`${API_BASE}/security/injection-alerts`);

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.connected) return; // handshake
        showInjectionModal(data);
      } catch { /* ignore parse errors */ }
    };

    eventSource.onerror = () => {
      eventSource.close();
      eventSource = null;
      // Reconnect after 5 seconds
      setTimeout(connect, 5000);
    };
  }

  function showInjectionModal(alert) {
    // Remove any existing modal
    const existing = document.getElementById('injection-alert-overlay');
    if (existing) existing.remove();

    const isBlocked = alert.type === 'blocked';
    const color = isBlocked ? '#ef4444' : '#f59e0b';
    const borderColor = isBlocked ? 'rgba(239,68,68,0.6)' : 'rgba(245,158,11,0.6)';
    const bgColor = isBlocked ? 'rgba(239,68,68,0.08)' : 'rgba(245,158,11,0.08)';
    const icon = isBlocked ? '🛡️' : '⚠️';
    const title = isBlocked ? 'Content Blocked' : 'Injection Warning';
    const subtitle = isBlocked
      ? 'Prompt injection detected — content was NOT sent to your AI.'
      : 'Suspicious patterns detected — content was sent with warnings.';

    const findingsHtml = (alert.findings || [])
      .slice(0, 5)
      .map(f => `<li style="margin-bottom:4px;font-size:12px;color:#cbd5e1;">${escapeHtml(f)}</li>`)
      .join('');

    const overlay = document.createElement('div');
    overlay.id = 'injection-alert-overlay';
    overlay.style.cssText = `
      position: fixed; inset: 0; z-index: 99999;
      background: rgba(0,0,0,0.5); backdrop-filter: blur(4px);
      display: flex; align-items: center; justify-content: center;
      animation: fadeIn 0.2s ease;
    `;

    overlay.innerHTML = `
      <div style="
        background: #1a1a2e; border: 1px solid ${borderColor};
        border-radius: 12px; padding: 24px; max-width: 440px; width: 90%;
        box-shadow: 0 8px 32px rgba(0,0,0,0.5);
      ">
        <div style="display:flex; align-items:center; gap:10px; margin-bottom:16px;">
          <span style="font-size:28px;">${icon}</span>
          <div>
            <div style="font-size:16px; font-weight:700; color:${color};">${title}</div>
            <div style="font-size:12px; color:#8892a4; margin-top:2px;">
              Risk score: ${alert.riskScore}/100 &middot; ${escapeHtml(alert.domain || 'unknown')}
            </div>
          </div>
        </div>

        <p style="font-size:13px; color:#a0aec0; margin-bottom:12px; line-height:1.5;">
          ${subtitle}
        </p>

        ${findingsHtml ? `
        <div style="background:${bgColor}; border:1px solid ${borderColor}; border-radius:8px; padding:10px; margin-bottom:16px;">
          <div style="font-size:11px; font-weight:600; color:${color}; margin-bottom:6px; text-transform:uppercase; letter-spacing:0.05em;">
            Detected patterns
          </div>
          <ul style="list-style:none; padding:0; margin:0;">${findingsHtml}</ul>
        </div>` : ''}

        <div style="display:flex; gap:8px; justify-content:flex-end;">
          ${isBlocked ? `
          <button id="injection-override-btn" style="
            background: transparent; border: 1px solid rgba(239,68,68,0.3);
            color: #ef4444; padding: 8px 16px; border-radius: 6px;
            cursor: pointer; font-size: 12px; transition: all 0.15s;
          ">Override (5 min)</button>` : ''}
          <button id="injection-close-btn" style="
            background: rgba(255,255,255,0.1); border: none;
            color: #e2e8f0; padding: 8px 20px; border-radius: 6px;
            cursor: pointer; font-size: 12px; font-weight: 500;
          ">Close</button>
        </div>
      </div>
    `;

    document.body.appendChild(overlay);

    // Close button
    document.getElementById('injection-close-btn').addEventListener('click', () => {
      overlay.remove();
    });

    // Override button (blocked only)
    const overrideBtn = document.getElementById('injection-override-btn');
    if (overrideBtn) {
      overrideBtn.addEventListener('click', () => {
        showOverrideConfirmation(alert.domain, overlay);
      });
    }

    // Auto-dismiss warnings after 30 seconds
    if (!isBlocked) {
      setTimeout(() => { if (overlay.parentNode) overlay.remove(); }, 30000);
    }
  }

  function showOverrideConfirmation(domain, overlay) {
    const btn = document.getElementById('injection-override-btn');
    if (!btn) return;

    btn.textContent = 'Are you sure?';
    btn.style.background = 'rgba(239,68,68,0.15)';
    btn.style.borderColor = 'rgba(239,68,68,0.5)';

    btn.onclick = async () => {
      try {
        const token = await window.tandemAPI?.getToken?.() || '';
        const resp = await fetch(`${API_BASE}/security/injection-override`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`,
          },
          body: JSON.stringify({ domain, confirmed: true }),
        });
        const result = await resp.json();
        if (result.ok) {
          btn.textContent = 'Overridden (5 min)';
          btn.style.color = '#4ade80';
          btn.style.borderColor = 'rgba(74,222,128,0.3)';
          btn.disabled = true;
          setTimeout(() => { if (overlay.parentNode) overlay.remove(); }, 2000);
        }
      } catch (err) {
        btn.textContent = 'Error — try again';
      }
    };
  }

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  // Start listening
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', connect);
  } else {
    connect();
  }
})();
