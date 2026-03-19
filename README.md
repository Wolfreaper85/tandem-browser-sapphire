# Tandem Browser — Sapphire Edition

A modified version of [Tandem Browser](https://github.com/hydro13/tandem-browser) by Robin Waslander (hydro13), integrated as a plugin for **Sapphire AI**.

Tandem Browser is an Electron-based AI companion browser originally built for OpenClaw. This fork adapts it to work seamlessly with Sapphire, providing your AI persona with full web browsing capabilities through the Wingman chat panel.

## Features

- **AI-Driven Web Browsing** — Sapphire can search the web, navigate pages, read content, click links, and fill forms through 16 tool functions
- **Wingman Chat Bridge** — Two-way chat between Sapphire's AI persona and the Wingman side panel, with typing indicators and busy-check (won't interrupt Sapphire mid-task)
- **Dynamic Persona Names** — Wingman chat displays your active Sapphire persona name (Lexi, Sarah, Alfred, etc.) instead of generic "Wingman"
- **Auto-Launch** — Tandem Browser starts automatically when Sapphire needs it
- **Auto-Install** — First run automatically installs Node.js dependencies
- **All Local** — Everything runs on localhost, no data leaves your machine

## Requirements

- [Sapphire AI](https://github.com/SapphireAI) installed and running
- Internet connection (first run only — downloads Node.js and dependencies automatically)

## Installation

### From Sapphire Plugin Store
Install directly from the Sapphire plugin store (if available).

### Manual Installation
1. Clone this repo into your Sapphire plugins folder:
   ```
   cd /path/to/sapphire/plugins
   git clone https://github.com/Wolfreaper85/tandem-browser-sapphire.git tandem-browser
   ```
2. Restart Sapphire — the plugin will auto-install dependencies on first launch

### What Happens on First Run
1. The plugin checks for Node.js — if not found, downloads a portable copy (~30MB) automatically
2. Runs `npm install` in the `app/` folder to install dependencies (takes 1-2 minutes)
3. Compiles TypeScript with `npm run compile`
4. Launches Tandem Browser
5. Subsequent launches are instant — no re-download needed

## Tool Functions

Once installed, Sapphire gains these browsing tools:

| Tool | Description |
|------|-------------|
| `tandem_search` | Search the web via DuckDuckGo |
| `tandem_browse` | Navigate to a specific URL |
| `tandem_read_page` | Read current page content |
| `tandem_click_link` | Click a link on the page |
| `tandem_fill_form` | Fill out form fields |
| `tandem_submit_form` | Submit a form |
| `tandem_scroll` | Scroll the page |
| `tandem_screenshot` | Take a screenshot |
| `tandem_get_tabs` | List open tabs |
| `tandem_new_tab` | Open a new tab |
| `tandem_close_tab` | Close a tab |
| `tandem_switch_tab` | Switch to a different tab |
| `tandem_back` | Go back in history |
| `tandem_forward` | Go forward in history |
| `tandem_execute_js` | Execute JavaScript on the page |
| `tandem_extract_links` | Extract all links from the page |

## Credits

- **Original Tandem Browser** — [Robin Waslander (hydro13)](https://github.com/hydro13/tandem-browser) — MIT License
- **Sapphire Integration** — [Wolfreaper85](https://github.com/Wolfreaper85) & Claude

## License

MIT — See [LICENSE](LICENSE) for details.
