# Tandem Browser

Tandem Browser is a first-party OpenClaw companion browser with a local HTTP API
at `http://127.0.0.1:8765`. Use this skill when an agent needs to browse, inspect,
interact with pages, analyze SPAs, or coordinate browser work without touching
Robin's active tab unnecessarily.

## Setup

Always read the API token first. Bearer auth is required for normal Tandem API
routes. Query-string token auth was removed.

```bash
API="http://127.0.0.1:8765"
TOKEN="$(cat ~/.tandem/api-token)"
AUTH_HEADER="Authorization: Bearer $TOKEN"
JSON_HEADER="Content-Type: application/json"

json_get_tab_id() {
  python3 -c 'import json,sys; print(json.load(sys.stdin)["tab"]["id"])'
}

# Optional sanity check. /status is public, but keep using the bearer token
# for all normal API work.
curl -sS "$API/status"
```

## Golden Rules

| Do | Do not |
| --- | --- |
| Open new work in a separate tab with `POST /tabs/open` and `focus:false` | Do not start with `POST /navigate` for a new target URL |
| Focus the new tab before snapshot/devtools/browser actions, because those routes act on the active tab | Do not assume snapshot or page routes target a background tab automatically |
| Use `GET /snapshot?compact=true` first for page analysis | Do not start with screenshots when a snapshot is enough |
| Use `@eN` refs or `POST /find` for interaction | Do not default to raw CSS click/type flows if refs or locators can do the job |
| Close temporary tabs with `POST /tabs/close` when done | Do not leave Wingman tabs open after the task ends |
| Use `POST /execute-js` with `window.scrollTo(...)` for SPA lazy loading | Do not rely on `POST /scroll` for SPA content loading |
| Use `GET /devtools/network?type=XHR` to inspect live SPA/API traffic | Do not guess hidden APIs if the network log already shows them |
| Warn Robin with `POST /wingman-alert` for captchas, login walls, or hard blockers | Do not keep retrying a blocked flow silently |

## Page Awareness — What Is The User Looking At?

Before answering questions or taking action, check what the user is currently
looking at. One call gives you everything:

```bash
curl -sS "$API/active-tab/context" \
  -H "$AUTH_HEADER"
```

Response shape:

```json
{
  "ready": true,
  "activeTab": {
    "id": "tab-14",
    "url": "https://example.com/article",
    "title": "Article title",
    "loading": false,
    "viewport": {
      "scrollTop": 500,
      "scrollHeight": 8000,
      "clientHeight": 900
    },
    "pageTextExcerpt": "First 1500 characters of visible page text..."
  },
  "tabs": [
    { "id": "tab-3",  "url": "https://discord.com/...", "title": "Discord", "active": false },
    { "id": "tab-14", "url": "https://example.com/...", "title": "Article title", "active": true }
  ]
}
```

Use `pageTextExcerpt` to answer questions about the current page without a
separate `/page-content` call. For deeper analysis, use `/snapshot?compact=true`
or `/page-content` with `X-Tab-Id: <id>` targeting.

### Staying Aware Without Polling

Subscribe to `GET /events/stream` (SSE) to get push notifications when the user
switches tabs, navigates, or loads a new page — no polling required:

```bash
curl -sS -N "$API/events/stream" \
  -H "$AUTH_HEADER" \
  -H "Accept: text/event-stream"
```

Relevant event types:
- `tab-focused` — user switched to a different tab
- `navigation` — tab navigated to a new URL
- `page-loaded` — page finished loading

Each event includes `tabId`, `url`, and `title`. On `tab-focused`, call
`/active-tab/context` to refresh your picture of what the user is seeing.

## Primary Workflow

For new browsing work, follow this pattern:

```bash
# 1. Open a separate tab without stealing Robin's focus immediately.
OPEN_JSON="$(curl -sS -X POST "$API/tabs/open" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d '{"url":"https://example.com","focus":false,"source":"wingman"}')"

TAB_ID="$(printf '%s' "$OPEN_JSON" | json_get_tab_id)"

# 2. Focus that tab before using snapshot, page-content, devtools, find, click,
#    fill, screenshot, or other active-tab routes.
curl -sS -X POST "$API/tabs/focus" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d "{\"tabId\":\"$TAB_ID\"}"

# 3. Analyze the page with a compact accessibility snapshot.
curl -sS "$API/snapshot?compact=true" \
  -H "$AUTH_HEADER"

# 4. Interact by ref or locator.
curl -sS -X POST "$API/find" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d '{"by":"text","value":"Sign in"}'

# 5. Clean up the temporary tab when finished.
curl -sS -X POST "$API/tabs/close" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d "{\"tabId\":\"$TAB_ID\"}"
```

## Snapshot / Ref System

`GET /snapshot` returns an accessibility-tree snapshot with stable refs such as
`@e1`, `@e2`, and `@e3`. Those refs are the preferred interaction surface.

Use the snapshot first, then interact by ref:

```bash
# Get a compact snapshot.
curl -sS "$API/snapshot?compact=true" \
  -H "$AUTH_HEADER"

# Click a ref from the snapshot.
curl -sS -X POST "$API/snapshot/click" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d '{"ref":"@e2"}'

# Fill a ref from the snapshot.
curl -sS -X POST "$API/snapshot/fill" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d '{"ref":"@e3","value":"hello@example.com"}'

# Read text from a ref.
curl -sS "$API/snapshot/text?ref=@e4" \
  -H "$AUTH_HEADER"
```

Use semantic locators when you do not want to manually parse refs:

```bash
# Supported locator strategies: role, text, placeholder, label, testid
curl -sS -X POST "$API/find" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d '{"by":"label","value":"Email"}'

curl -sS -X POST "$API/find/click" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d '{"by":"text","value":"Continue"}'

curl -sS -X POST "$API/find/fill" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d '{"by":"label","value":"Password","fillValue":"correct horse battery staple"}'

curl -sS -X POST "$API/find/all" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d '{"by":"role","value":"button"}'
```

## Core Endpoints by Use Case

### Safe Tab Lifecycle

```bash
# Open a new tab. For background work, prefer focus:false.
curl -sS -X POST "$API/tabs/open" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d '{"url":"https://example.com","focus":false,"source":"wingman"}'

# List tabs and groups.
curl -sS "$API/tabs/list" \
  -H "$AUTH_HEADER"

# Focus a tab before active-tab operations.
curl -sS -X POST "$API/tabs/focus" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d '{"tabId":"tab-123"}'

# Close a temporary tab after use.
curl -sS -X POST "$API/tabs/close" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d '{"tabId":"tab-123"}'
```

### Page Analysis and Basic Browser Actions

```bash
# Preferred page analysis.
curl -sS "$API/snapshot?compact=true" \
  -H "$AUTH_HEADER"

# Broader text extraction.
curl -sS "$API/page-content" \
  -H "$AUTH_HEADER"

# Raw outerHTML for full DOM inspection.
curl -sS "$API/page-html" \
  -H "$AUTH_HEADER"

# Execute JS in the active tab.
curl -sS -X POST "$API/execute-js" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d '{"code":"document.title"}'

# Wait for page load or a selector.
curl -sS -X POST "$API/wait" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d '{"selector":"main","timeout":10000}'

# Fallback CSS-driven click/type routes. Prefer refs/locators when possible.
curl -sS -X POST "$API/click" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d '{"selector":"button[type=\"submit\"]"}'

curl -sS -X POST "$API/type" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d '{"selector":"input[name=\"q\"]","text":"OpenClaw","clear":true}'

# Screenshot only when a visual artifact is actually needed.
curl -sS "$API/screenshot" \
  -H "$AUTH_HEADER" \
  -o screenshot.png

# Human escalation.
curl -sS -X POST "$API/wingman-alert" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d '{"title":"Human help needed","body":"Captcha or login wall encountered."}'
```

### Sessions and Same-Origin API Relay

```bash
# Create an isolated session. Optional url opens a tab in that partition.
curl -sS -X POST "$API/sessions/create" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d '{"name":"research","url":"https://example.com"}'

# List sessions.
curl -sS "$API/sessions/list" \
  -H "$AUTH_HEADER"

# Switch the active named session.
curl -sS -X POST "$API/sessions/switch" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d '{"name":"research"}'

# Save/load session state.
curl -sS -X POST "$API/sessions/state/save" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d '{"name":"research-state"}'

curl -sS -X POST "$API/sessions/state/load" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d '{"name":"research-state"}'

# Same-origin fetch from inside the tab context.
# Important: no Authorization/Cookie/Origin/Referer headers allowed here.
curl -sS -X POST "$API/sessions/fetch" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d '{"tabId":"tab-123","url":"/api/me","method":"GET"}'
```

### DevTools and Network Inspection

```bash
# DevTools health.
curl -sS "$API/devtools/status" \
  -H "$AUTH_HEADER"

# Live network entries. Use type=XHR or type=Fetch for SPA API traffic.
curl -sS "$API/devtools/network?type=XHR&limit=50" \
  -H "$AUTH_HEADER"

# Fetch the response body of a recorded request.
curl -sS "$API/devtools/network/REQUEST_ID/body" \
  -H "$AUTH_HEADER"

# Console entries and errors.
curl -sS "$API/devtools/console?limit=100" \
  -H "$AUTH_HEADER"

curl -sS "$API/devtools/console/errors?limit=50" \
  -H "$AUTH_HEADER"

# DOM query helpers.
curl -sS -X POST "$API/devtools/dom/query" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d '{"selector":"main a","maxResults":10}'

curl -sS -X POST "$API/devtools/dom/xpath" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d '{"expression":"//button[contains(.,\"Continue\")]","maxResults":10}'

# Evaluate via CDP Runtime.
curl -sS -X POST "$API/devtools/evaluate" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d '{"expression":"window.location.href"}'

# Storage and performance.
curl -sS "$API/devtools/storage" \
  -H "$AUTH_HEADER"

curl -sS "$API/devtools/performance" \
  -H "$AUTH_HEADER"
```

### Network Mocking and Request Discovery

```bash
# Simple network log.
curl -sS "$API/network/log?limit=100" \
  -H "$AUTH_HEADER"

# Discovered API endpoints and domains.
curl -sS "$API/network/apis" \
  -H "$AUTH_HEADER"

curl -sS "$API/network/domains" \
  -H "$AUTH_HEADER"

# HAR export.
curl -sS "$API/network/har?limit=100" \
  -H "$AUTH_HEADER" \
  -o tandem-network.har

# Add a mock/route rule.
curl -sS -X POST "$API/network/mock" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d '{"pattern":"*://api.example.com/*","status":200,"body":"{\"ok\":true}","headers":{"content-type":"application/json"}}'

# List and remove mock rules.
curl -sS "$API/network/mocks" \
  -H "$AUTH_HEADER"

curl -sS -X POST "$API/network/unmock" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d '{"id":"rule-123"}'
```

### Agent Workflow / Coordination Endpoints

```bash
# Inspect existing tasks.
curl -sS "$API/tasks" \
  -H "$AUTH_HEADER"

curl -sS "$API/tasks/TASK_ID" \
  -H "$AUTH_HEADER"

# Approval-gated JS execution.
curl -sS -X POST "$API/execute-js/confirm" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d '{"code":"document.body.innerText.slice(0, 500)"}'

# Emergency stop and tab locks.
curl -sS -X POST "$API/emergency-stop" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d '{}'

curl -sS -X POST "$API/tab-locks/acquire" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d '{"tabId":"tab-123","agentId":"openclaw-main"}'

curl -sS -X POST "$API/tab-locks/release" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d '{"tabId":"tab-123","agentId":"openclaw-main"}'
```

## SPA Tips

Use these rules on dynamic apps such as Discord, Slack, GitHub dashboards,
single-page admin panels, or React/Vue/Next interfaces:

```bash
# Lazy loading or infinite scroll:
# prefer execute-js with window.scrollTo(), not /scroll.
curl -sS -X POST "$API/execute-js" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d '{"code":"window.scrollTo({ top: document.body.scrollHeight, behavior: \"smooth\" })"}'

# Inspect real API traffic:
curl -sS "$API/devtools/network?type=XHR&limit=100" \
  -H "$AUTH_HEADER"

# If /page-content is weak, too short, or obviously raw for the SPA,
# fall back to direct JS extraction.
curl -sS -X POST "$API/execute-js" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d '{"code":"document.body.innerText"}'
```

Interpretation tip: `/page-content` uses DOM-settling heuristics and is still
worth trying first, but on complex SPAs you should treat `/devtools/network`
and `POST /execute-js` as the more reliable fallback tools.

## Example Workflow: Simple Inspection

```bash
API="http://127.0.0.1:8765"
TOKEN="$(cat ~/.tandem/api-token)"
AUTH_HEADER="Authorization: Bearer $TOKEN"
JSON_HEADER="Content-Type: application/json"

OPEN_JSON="$(curl -sS -X POST "$API/tabs/open" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d '{"url":"https://example.com","focus":false,"source":"wingman"}')"

TAB_ID="$(printf '%s' "$OPEN_JSON" | json_get_tab_id)"

curl -sS -X POST "$API/tabs/focus" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d "{\"tabId\":\"$TAB_ID\"}"

curl -sS "$API/snapshot?compact=true" \
  -H "$AUTH_HEADER"

curl -sS -X POST "$API/find" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d '{"by":"text","value":"More information"}'

curl -sS -X POST "$API/tabs/close" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d "{\"tabId\":\"$TAB_ID\"}"
```

## Example Workflow: SPA Investigation With Human Escalation

```bash
API="http://127.0.0.1:8765"
TOKEN="$(cat ~/.tandem/api-token)"
AUTH_HEADER="Authorization: Bearer $TOKEN"
JSON_HEADER="Content-Type: application/json"

OPEN_JSON="$(curl -sS -X POST "$API/tabs/open" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d '{"url":"https://app.example.com/dashboard","focus":false,"source":"wingman"}')"

TAB_ID="$(printf '%s' "$OPEN_JSON" | json_get_tab_id)"

curl -sS -X POST "$API/tabs/focus" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d "{\"tabId\":\"$TAB_ID\"}"

curl -sS "$API/snapshot?compact=true" \
  -H "$AUTH_HEADER"

curl -sS -X POST "$API/execute-js" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d '{"code":"window.scrollTo({ top: document.body.scrollHeight, behavior: \"smooth\" })"}'

curl -sS "$API/devtools/network?type=XHR&limit=100" \
  -H "$AUTH_HEADER"

curl -sS -X POST "$API/execute-js" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d '{"code":"document.body.innerText"}'

# If a captcha, login wall, or hard block appears:
curl -sS -X POST "$API/wingman-alert" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d '{"title":"Robin needed","body":"Blocked by login wall or captcha in the SPA flow."}'

curl -sS -X POST "$API/tabs/close" \
  -H "$AUTH_HEADER" \
  -H "$JSON_HEADER" \
  -d "{\"tabId\":\"$TAB_ID\"}"
```

## Error Handling

Common failure cases and the correct reaction:

```bash
# 401 Unauthorized
# Cause: missing or wrong bearer token.
# Fix: re-read ~/.tandem/api-token and retry with Authorization: Bearer <token>.

# 400 from /sessions/fetch
# Cause: cross-origin URL, forbidden headers, unsupported method, or invalid body.
# Fix: keep the fetch same-origin and do not send Authorization/Cookie/Origin/Referer headers.

# "Ref not found" from snapshot routes
# Cause: refs were reset after navigation or page change.
# Fix: call GET /snapshot again and use the fresh @eN refs.

# Empty or weak /page-content on an SPA
# Cause: the page is client-rendered or still loading data.
# Fix: use POST /execute-js with window.scrollTo(), inspect GET /devtools/network?type=XHR,
# and fall back to POST /execute-js with document.body.innerText.

# Captcha, login wall, MFA, blocked action, or unclear human decision
# Fix: send POST /wingman-alert immediately and wait for Robin.
```

## Final Reminder

The one rule that must stay prominent:

```bash
# Do not start new work with /navigate.
# /navigate loads into the active tab and can overwrite Robin's context.
# For new URLs, open a separate tab with /tabs/open, focus it only when needed,
# and close it when the task is done.
```
