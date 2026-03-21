/**
 * Search Mode Toggle — cycles between Quick, Normal, and Detailed search.
 *
 * Quick:    DuckDuckGo AI assist — instant answers (3 tool calls max)
 * Normal:   Standard search snippets (6 tool calls max)
 * Detailed: Clicks into pages for thorough answers (10 tool calls max)
 */
(() => {
  const API = 'http://localhost:8765';
  const toggleBtn = document.getElementById('search-mode-toggle');
  const iconEl = document.getElementById('search-mode-icon');
  const labelEl = document.getElementById('search-mode-label');

  if (!toggleBtn || !iconEl || !labelEl) return;

  const MODES = ['quick', 'normal', 'detailed'];
  const MODE_CONFIG = {
    quick: {
      icon: '\u26A1',       // lightning bolt
      label: 'Quick',
      cssClass: 'mode-quick',
      title: 'Quick — instant AI-assisted answers (click to cycle)'
    },
    normal: {
      icon: '\uD83D\uDD0D', // magnifying glass
      label: 'Normal',
      cssClass: 'mode-normal',
      title: 'Normal — search snippet answers (click to cycle)'
    },
    detailed: {
      icon: '\uD83D\uDCDA', // books
      label: 'Detailed',
      cssClass: 'mode-detailed',
      title: 'Detailed — clicks into pages for thorough answers (click to cycle)'
    }
  };

  let currentMode = 'detailed';

  function updateUI(mode) {
    currentMode = mode;
    const config = MODE_CONFIG[mode] || MODE_CONFIG.detailed;

    iconEl.textContent = config.icon;
    labelEl.textContent = config.label;
    toggleBtn.title = config.title;

    // Remove all mode classes, add current
    toggleBtn.classList.remove('mode-quick', 'mode-normal', 'mode-detailed');
    toggleBtn.classList.add(config.cssClass);

    // Update the step dots
    MODES.forEach((m, i) => {
      const dot = toggleBtn.querySelector(`.step-dot[data-mode="${m}"]`);
      if (dot) {
        dot.classList.toggle('active', m === mode);
      }
    });
  }

  // Fetch initial state from Tandem API
  async function loadMode() {
    try {
      const res = await fetch(`${API}/search-mode`);
      if (res.ok) {
        const data = await res.json();
        if (MODES.includes(data.mode)) {
          updateUI(data.mode);
        }
      }
    } catch {
      // API not ready yet — keep default
    }
  }

  // Cycle through modes on click: quick -> normal -> detailed -> quick
  toggleBtn.addEventListener('click', async () => {
    const currentIndex = MODES.indexOf(currentMode);
    const nextMode = MODES[(currentIndex + 1) % MODES.length];
    try {
      const res = await fetch(`${API}/search-mode`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: nextMode })
      });
      if (res.ok) {
        updateUI(nextMode);
      }
    } catch (err) {
      console.warn('[search-mode] Failed to toggle:', err);
    }
  });

  // Initialize
  updateUI('detailed');
  loadMode();
  setTimeout(loadMode, 3000);
  setTimeout(loadMode, 8000);
})();
