# Tandem Browser tool — plugin tool
"""
Tandem Browser — AI can browse the web, take screenshots, read page content,
click elements, fill forms, and interact with web pages through the local
Tandem Browser API.
"""

import json
import logging
import os
import ssl
import subprocess
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# WINGMAN CHAT BRIDGE — Two-way bridge between Tandem Wingman and Sapphire
# - Wingman → Sapphire: forwards robin messages (waits if Sapphire is busy)
# - Sapphire → Wingman: mirrors AI replies into the Wingman panel
# ═══════════════════════════════════════════════════════════
_bridge_thread = None
_bridge_running = False
_has_navigated = False  # Track if AI has navigated in this session
_tool_call_count = 0    # Track tool calls per chat turn
_MAX_TOOL_CALLS = 6     # After this many calls, nudge AI to wrap up

def _start_wingman_bridge():
    """Start the background bridge thread (once)."""
    global _bridge_thread, _bridge_running
    if _bridge_thread is not None:
        return
    _bridge_running = True
    _bridge_thread = threading.Thread(target=_wingman_bridge_loop, daemon=True)
    _bridge_thread.start()
    logger.info("Wingman chat bridge started")

def _wingman_bridge_loop():
    """
    Two-way bridge:
    1. Poll Tandem /chat for new robin messages → forward to Sapphire (with busy-check)
    2. Poll Sapphire /api/status for new messages → mirror AI replies to Wingman
    """
    import re
    import hashlib
    sapphire_url = "https://127.0.0.1:8073"
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    last_tandem_id = 0        # Track Tandem chat messages (for robin → Sapphire)
    last_sapphire_count = 0   # Track Sapphire message count (for Sapphire → Wingman)
    initialized = False       # Don't process anything until both services are synced

    # Content hashes of messages already posted to Tandem — prevents duplicates
    # between PART 1 and PART 2, and prevents replaying old messages on restart.
    posted_hashes = set()

    def _content_hash(text):
        """Short hash of message content for dedup."""
        return hashlib.md5(text[:500].encode("utf-8", errors="replace")).hexdigest()

    # Wait a bit for services to start
    time.sleep(5)

    # File where we write persona name for the Wingman JS to read
    persona_file = Path.home() / ".tandem" / "sapphire-persona.json"

    # ── Sync initial state so we don't replay old messages ──
    # IMPORTANT: Keep retrying until Tandem is reachable — if we proceed
    # with last_tandem_id=0 we'll replay ALL old messages as phantoms.
    logger.info("Wingman bridge: waiting for Tandem to become available...")
    while _bridge_running:
        try:
            _, token = _get_config()
            req = urllib.request.Request(
                "http://127.0.0.1:8765/chat",
                headers={"Authorization": f"Bearer {token}"},
                method="GET"
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                messages = data.get("messages", [])
                if messages:
                    last_tandem_id = max(m.get("id", 0) for m in messages)
            logger.info(f"Wingman bridge: synced Tandem chat at message ID {last_tandem_id}")
            break  # Success — exit retry loop
        except Exception as e:
            logger.debug(f"Wingman bridge: Tandem not ready yet ({e}), retrying in 5s...")
            time.sleep(5)

    # Sync Sapphire message count AND pre-populate posted_hashes with all
    # existing assistant messages so they never get re-posted as phantoms.
    for _attempt in range(12):  # Up to ~60 seconds
        if not _bridge_running:
            break
        try:
            api_key = _get_sapphire_api_key()
            if api_key:
                req = urllib.request.Request(
                    f"{sapphire_url}/api/status",
                    headers={"X-API-Key": api_key},
                    method="GET"
                )
                with urllib.request.urlopen(req, timeout=5, context=ssl_ctx) as resp:
                    status = json.loads(resp.read().decode("utf-8"))
                    last_sapphire_count = status.get("message_count", 0)

                # Pre-populate hashes from ALL existing assistant messages
                try:
                    hist_req = urllib.request.Request(
                        f"{sapphire_url}/api/history",
                        headers={"X-API-Key": api_key},
                        method="GET"
                    )
                    with urllib.request.urlopen(hist_req, timeout=10, context=ssl_ctx) as hist_resp:
                        history = json.loads(hist_resp.read().decode("utf-8"))
                        for msg in history.get("messages", []):
                            if msg.get("role") == "assistant":
                                parts = msg.get("parts", [])
                                text_parts = [p.get("text", "") for p in parts
                                              if isinstance(p, dict) and p.get("type") == "content"]
                                txt = "\n".join(text_parts).strip()
                                if not txt:
                                    txt = msg.get("content", "").strip()
                                if txt:
                                    txt = re.sub(r'<think>.*?</think>', '', txt, flags=re.DOTALL).strip()
                                    if txt:
                                        posted_hashes.add(_content_hash(txt))
                    logger.info(f"Wingman bridge: pre-loaded {len(posted_hashes)} content hashes")
                except Exception as e:
                    logger.debug(f"Wingman bridge: couldn't pre-load history hashes: {e}")

                logger.info(f"Wingman bridge: synced Sapphire at {last_sapphire_count} messages")
                initialized = True
                break
        except Exception as e:
            logger.debug(f"Wingman bridge: Sapphire not ready yet ({e}), retrying...")
            time.sleep(5)

    if not initialized:
        logger.warning("Wingman bridge: couldn't sync Sapphire status after retries")

    while _bridge_running:
        try:
            time.sleep(2)
            if not _is_tandem_running():
                continue

            _, token = _get_config()
            api_key = _get_sapphire_api_key()

            # ── PART 1: Wingman → Sapphire (forward robin messages) ──
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:8765/chat?since_id={last_tandem_id}",
                    headers={"Authorization": f"Bearer {token}"},
                    method="GET"
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    tandem_data = json.loads(resp.read().decode("utf-8"))
            except Exception:
                tandem_data = {"messages": []}

            tandem_msgs = tandem_data.get("messages", [])

            # Handle Tandem chat clear
            if tandem_msgs and last_tandem_id > 0:
                max_id = max(m.get("id", 0) for m in tandem_msgs)
                if max_id < last_tandem_id:
                    logger.info("Wingman bridge: Tandem chat cleared, resetting")
                    last_tandem_id = max_id
                    tandem_msgs = []  # Skip this cycle

            for msg in tandem_msgs:
                msg_id = msg.get("id", 0)
                if msg_id <= last_tandem_id:
                    continue
                last_tandem_id = msg_id

                # Only forward robin messages to Sapphire
                if msg.get("from") != "robin":
                    continue

                user_text = msg.get("text", "").strip()
                if not user_text:
                    continue

                if not api_key:
                    continue

                logger.info(f"Wingman bridge: forwarding '{user_text[:50]}...' to Sapphire")

                # Set typing indicator
                try:
                    typing_body = json.dumps({"typing": True}).encode("utf-8")
                    typing_req = urllib.request.Request(
                        "http://127.0.0.1:8765/chat/typing",
                        data=typing_body,
                        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                        method="POST"
                    )
                    urllib.request.urlopen(typing_req, timeout=3)
                except Exception:
                    pass

                # Wait if Sapphire is busy
                for _ in range(30):
                    try:
                        status_req = urllib.request.Request(
                            f"{sapphire_url}/api/status",
                            headers={"X-API-Key": api_key},
                            method="GET"
                        )
                        with urllib.request.urlopen(status_req, timeout=5, context=ssl_ctx) as resp:
                            s = json.loads(resp.read().decode("utf-8"))
                            if not s.get("is_streaming", False):
                                break
                    except Exception:
                        break
                    time.sleep(2)

                # Send to Sapphire
                reply = _send_to_sapphire(sapphire_url, user_text, ssl_ctx, api_key)

                if reply:
                    reply = re.sub(r'<think>.*?</think>', '', reply, flags=re.DOTALL).strip()

                if reply:
                    posted_hashes.add(_content_hash(reply))
                    _post_to_tandem_chat(token, reply)
                    logger.info(f"Wingman bridge: posted reply '{reply[:80]}...'")
                    # Re-fetch actual Sapphire message count so PART 2 mirror
                    # doesn't re-post the same reply (the old += 2 guess was wrong)
                    try:
                        count_req = urllib.request.Request(
                            f"{sapphire_url}/api/status",
                            headers={"X-API-Key": api_key},
                            method="GET"
                        )
                        with urllib.request.urlopen(count_req, timeout=5, context=ssl_ctx) as count_resp:
                            count_status = json.loads(count_resp.read().decode("utf-8"))
                            last_sapphire_count = count_status.get("message_count", last_sapphire_count + 2)
                            logger.debug(f"Wingman bridge: re-synced Sapphire count to {last_sapphire_count}")
                    except Exception:
                        last_sapphire_count += 2  # Fallback to old heuristic
                else:
                    logger.warning("Wingman bridge: empty reply from Sapphire")

                # Clear typing
                try:
                    typing_body = json.dumps({"typing": False}).encode("utf-8")
                    typing_req = urllib.request.Request(
                        "http://127.0.0.1:8765/chat/typing",
                        data=typing_body,
                        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                        method="POST"
                    )
                    urllib.request.urlopen(typing_req, timeout=3)
                except Exception:
                    pass

            # ── PART 2: Sapphire → Wingman (mirror AI replies) ──
            if not api_key or not initialized:
                # Try to initialize if we haven't yet
                if api_key and not initialized:
                    try:
                        req = urllib.request.Request(
                            f"{sapphire_url}/api/status",
                            headers={"X-API-Key": api_key},
                            method="GET"
                        )
                        with urllib.request.urlopen(req, timeout=5, context=ssl_ctx) as resp:
                            status = json.loads(resp.read().decode("utf-8"))
                            last_sapphire_count = status.get("message_count", 0)
                            initialized = True
                    except Exception:
                        pass
                continue

            try:
                req = urllib.request.Request(
                    f"{sapphire_url}/api/status",
                    headers={"X-API-Key": api_key},
                    method="GET"
                )
                with urllib.request.urlopen(req, timeout=5, context=ssl_ctx) as resp:
                    status = json.loads(resp.read().decode("utf-8"))
            except Exception:
                continue

            msg_count = status.get("message_count", 0)
            is_streaming = status.get("is_streaming", False)

            # Update persona name file so Wingman JS shows the right name
            try:
                chat_settings = status.get("chat_settings", {})
                persona_name = chat_settings.get("persona", "Sapphire") or "Sapphire"
                # Capitalize first letter for display
                persona_display = persona_name.capitalize()
                persona_data = {"persona": persona_display, "username": "You"}
                persona_file.write_text(json.dumps(persona_data))
            except Exception:
                pass

            # Skip if streaming or no new messages
            if is_streaming or msg_count <= last_sapphire_count:
                continue

            # Fetch history for new assistant messages
            try:
                req = urllib.request.Request(
                    f"{sapphire_url}/api/history",
                    headers={"X-API-Key": api_key},
                    method="GET"
                )
                with urllib.request.urlopen(req, timeout=10, context=ssl_ctx) as resp:
                    history = json.loads(resp.read().decode("utf-8"))
            except Exception:
                continue

            messages = history.get("messages", [])
            new_count = msg_count - last_sapphire_count
            last_sapphire_count = msg_count

            if not messages or new_count <= 0:
                continue

            recent = messages[-min(new_count, 10):]

            for msg in recent:
                if msg.get("role") == "assistant":
                    parts = msg.get("parts", [])
                    text_parts = [p.get("text", "") for p in parts
                                  if isinstance(p, dict) and p.get("type") == "content"]
                    reply_text = "\n".join(text_parts).strip()
                    if not reply_text:
                        reply_text = msg.get("content", "").strip()
                    if not reply_text:
                        continue
                    reply_text = re.sub(r'<think>.*?</think>', '', reply_text, flags=re.DOTALL).strip()
                    if not reply_text:
                        continue
                    # Skip if already posted (by PART 1 bridge or from startup history)
                    h = _content_hash(reply_text)
                    if h in posted_hashes:
                        logger.debug(f"Wingman mirror: skipping already-posted message '{reply_text[:50]}...'")
                        continue
                    posted_hashes.add(h)
                    _post_to_tandem_chat(token, reply_text)
                    logger.info(f"Wingman mirror: posted AI reply '{reply_text[:80]}...'")

        except Exception as e:
            logger.error(f"Wingman bridge error: {e}")
            time.sleep(5)


def _get_sapphire_api_key():
    """Read Sapphire's bcrypt password hash for internal API auth."""
    try:
        if os.name == 'nt':
            appdata = os.environ.get('APPDATA')
            if appdata:
                secret_file = Path(appdata) / 'Sapphire' / 'secret_key'
            else:
                secret_file = Path.home() / 'AppData' / 'Roaming' / 'Sapphire' / 'secret_key'
        else:
            secret_file = Path.home() / '.config' / 'sapphire' / 'secret_key'

        if secret_file.exists():
            key = secret_file.read_text().strip()
            if key and key.startswith('$2') and len(key) > 50:
                return key
            logger.warning(f"Wingman bridge: secret_key file exists but doesn't contain valid bcrypt hash")
        else:
            logger.warning(f"Wingman bridge: secret_key file not found at {secret_file}")
    except Exception as e:
        logger.error(f"Wingman mirror: failed to read Sapphire API key: {e}")
    return None


def _post_to_tandem_chat(token, text):
    """Post a message to Tandem's Wingman chat as 'wingman'."""
    body = json.dumps({"text": text, "from": "wingman"}).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:8765/chat",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.error(f"Wingman bridge: failed to post to Tandem chat: {e}")
        return None


def _send_to_sapphire(base_url, text, ssl_ctx, api_key):
    """Send a message to Sapphire's chat API and collect the streamed response."""
    import uuid
    session_id = str(uuid.uuid4())

    body = json.dumps({"text": text}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Session-ID": session_id,
        "X-CSRF-Token": "",
        "X-API-Key": api_key,
    }
    req = urllib.request.Request(
        f"{base_url}/api/chat/stream",
        data=body,
        headers=headers,
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=120, context=ssl_ctx) as resp:
            full_reply = ""
            for line in resp:
                decoded = line.decode("utf-8", errors="replace").strip()
                if not decoded:
                    continue
                if decoded.startswith("data: "):
                    payload = decoded[6:]
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                        text_chunk = chunk.get("text", "") or chunk.get("content", "") or chunk.get("delta", "")
                        full_reply += text_chunk
                    except json.JSONDecodeError:
                        full_reply += payload
                elif not decoded.startswith(":"):
                    full_reply += decoded
            return full_reply.strip()
    except Exception as e:
        logger.error(f"Wingman bridge: Sapphire chat error: {e}")
        return None


# --- Auto-launch Tandem Browser ---
_tandem_process = None
_launch_in_progress = False

def _auto_install_tandem(app_dir):
    """Auto-install npm dependencies and compile TypeScript on first run."""
    if not (app_dir / "package.json").exists():
        return False

    node_modules = app_dir / "node_modules"
    electron_exe = node_modules / "electron" / "dist" / "electron.exe"

    if electron_exe.exists():
        return True  # Already installed

    # Check Node.js is available
    try:
        result = subprocess.run(["node", "--version"], capture_output=True, timeout=10)
        if result.returncode != 0:
            logger.error("Node.js not found. Install Node.js v20+ from https://nodejs.org")
            return False
        logger.info(f"Found Node.js {result.stdout.decode().strip()}")
    except Exception:
        logger.error("Node.js not found. Install Node.js v20+ from https://nodejs.org")
        return False

    logger.info("First run — installing Tandem Browser dependencies (this may take a minute)...")

    try:
        # Run npm install
        install = subprocess.run(
            ["npm", "install"],
            cwd=str(app_dir),
            capture_output=True,
            timeout=300,  # 5 minute timeout
            shell=True
        )
        if install.returncode != 0:
            logger.error(f"npm install failed: {install.stderr.decode()[:500]}")
            return False
        logger.info("npm install complete")

        # Compile TypeScript
        compile_result = subprocess.run(
            ["npm", "run", "compile"],
            cwd=str(app_dir),
            capture_output=True,
            timeout=120,
            shell=True
        )
        if compile_result.returncode != 0:
            logger.error(f"TypeScript compile failed: {compile_result.stderr.decode()[:500]}")
            return False
        logger.info("TypeScript compile complete — Tandem Browser ready")

        return electron_exe.exists()
    except subprocess.TimeoutExpired:
        logger.error("Installation timed out")
        return False
    except Exception as e:
        logger.error(f"Installation failed: {e}")
        return False

def _find_tandem_app():
    """Find the bundled Tandem Browser app directory. Auto-installs on first run."""
    plugin_dir = Path(__file__).parent.parent
    app_dir = plugin_dir / "app"
    if not app_dir.exists():
        return None

    electron_exe = app_dir / "node_modules" / "electron" / "dist" / "electron.exe"
    if electron_exe.exists():
        return app_dir

    # Try auto-install
    if _auto_install_tandem(app_dir):
        return app_dir
    return None

def _is_tandem_running():
    """Check if Tandem Browser API is responding."""
    try:
        req = urllib.request.Request("http://127.0.0.1:8765/status", method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("ready", False)
    except Exception:
        return False

def _ensure_tandem_running():
    """Auto-launch Tandem Browser if not already running. Only one launch at a time."""
    global _tandem_process, _launch_in_progress
    if _is_tandem_running():
        return True

    # Prevent multiple simultaneous launches
    if _launch_in_progress:
        # Another call is already launching — just wait for it
        for _ in range(20):
            time.sleep(1)
            if _is_tandem_running():
                return True
        return False

    # Check if we already launched one that's still running
    if _tandem_process is not None and _tandem_process.poll() is None:
        # Process exists but API not ready yet — wait
        for _ in range(10):
            time.sleep(1)
            if _is_tandem_running():
                return True
        return False

    app_dir = _find_tandem_app()
    if not app_dir:
        logger.warning("Tandem Browser app not found in plugin folder")
        return False

    _launch_in_progress = True
    electron_exe = app_dir / "node_modules" / "electron" / "dist" / "electron.exe"
    logger.info(f"Auto-launching Tandem Browser from {app_dir}")

    env = os.environ.copy()
    env.pop("ELECTRON_RUN_AS_NODE", None)
    env.pop("ATOM_SHELL_INTERNAL_RUN_AS_NODE", None)

    try:
        _tandem_process = subprocess.Popen(
            [str(electron_exe), "."],
            cwd=str(app_dir),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
        # Wait for API to become ready
        for _ in range(15):
            time.sleep(1)
            if _is_tandem_running():
                logger.info("Tandem Browser is ready")
                _launch_in_progress = False
                return True
        logger.warning("Tandem Browser launched but API not responding")
        _launch_in_progress = False
        return False
    except Exception as e:
        logger.error(f"Failed to launch Tandem Browser: {e}")
        _launch_in_progress = False
        return False

# Auto-start the Wingman bridge when plugin loads
_start_wingman_bridge()

ENABLED = True
EMOJI = '\U0001f310'
AVAILABLE_FUNCTIONS = [
    'tandem_browse', 'tandem_search', 'tandem_read_page',
    'tandem_screenshot', 'tandem_click', 'tandem_click_link', 'tandem_type',
    'tandem_tabs', 'tandem_links', 'tandem_forms',
    'tandem_close_tab', 'tandem_status', 'tandem_scroll',
    'tandem_wait', 'tandem_extract', 'tandem_js', 'tandem_snapshot'
]

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "tandem_browse",
            "description": "Open a URL in the visible Tandem Browser window. The browser may have a previously loaded page — this replaces it. Always use this to navigate to a new URL if search results aren't showing what you need.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to navigate to"
                    },
                    "new_tab": {
                        "type": "boolean",
                        "description": "Open in a new tab (default: false, navigates current tab)"
                    }
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "tandem_search",
            "description": "Search the web using DuckDuckGo in the visible Tandem Browser window. This REPLACES whatever page is currently loaded with fresh search results. ALWAYS use this instead of web_search when Tandem Browser is running. If results seem wrong or show an old page, use tandem_browse to navigate directly to the URL you need.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "tandem_read_page",
            "description": "Read the current page content from Tandem Browser as text. Returns the page title, URL, and text content. Check the URL to confirm you're reading the page you expect — if it shows an old page, use tandem_browse or tandem_search to navigate first.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "tandem_snapshot",
            "description": "Get an accessibility snapshot of the page. Best for understanding page structure, finding interactive elements, and getting refs for clicking/filling.",
            "parameters": {
                "type": "object",
                "properties": {
                    "interactive": {
                        "type": "boolean",
                        "description": "Only show interactive elements (default: true)"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "tandem_screenshot",
            "description": "Take a screenshot of the current browser tab.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "tandem_click",
            "description": "Click an element on the page using a CSS selector or an accessibility ref from get_snapshot.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS selector for the element to click (e.g. 'button.submit', '#login')"
                    },
                    "ref": {
                        "type": "string",
                        "description": "Accessibility ref from get_snapshot (e.g. 's3e7'). Use this instead of selector when available."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "tandem_click_link",
            "description": "Click a link on the page by its visible text. This is the MOST RELIABLE way to click links, especially on search result pages. Use this instead of tandem_click when you want to click a link you can see in the page text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The visible text of the link to click (e.g. 'List of best-selling video games - Wikipedia'). Partial match is supported."
                    }
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "tandem_type",
            "description": "Type text into an input field on the page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS selector for the input element"
                    },
                    "ref": {
                        "type": "string",
                        "description": "Accessibility ref from get_snapshot. Use instead of selector when available."
                    },
                    "text": {
                        "type": "string",
                        "description": "Text to type into the element"
                    },
                    "clear": {
                        "type": "boolean",
                        "description": "Clear existing text before typing (default: true)"
                    }
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "tandem_scroll",
            "description": "Scroll the page up or down.",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["up", "down"],
                        "description": "Scroll direction"
                    },
                    "amount": {
                        "type": "integer",
                        "description": "Pixels to scroll (default: 500)"
                    }
                },
                "required": ["direction"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "tandem_wait",
            "description": "Wait for an element to appear on the page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS selector to wait for"
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in milliseconds (default: 5000)"
                    }
                },
                "required": ["selector"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "tandem_extract",
            "description": "Extract readable content from a URL without navigating to it in the browser.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL to extract content from"
                    }
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "tandem_js",
            "description": "Execute JavaScript code on the current page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "JavaScript code to execute"
                    }
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "tandem_tabs",
            "description": "List all open browser tabs.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "tandem_links",
            "description": "Extract all links from the current page.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "tandem_forms",
            "description": "Extract all forms and input fields from the current page.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "tandem_close_tab",
            "description": "Close a browser tab by its tab ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tab_id": {
                        "type": "string",
                        "description": "The tab ID to close (get from list_tabs)"
                    }
                },
                "required": ["tab_id"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "tandem_status",
            "description": "Check if Tandem Browser is running and get current status.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    }
]


def _get_config():
    """Get API URL and token."""
    api_url = "http://127.0.0.1:8765"
    token_file = Path.home() / ".tandem" / "api-token"
    token = ""
    if token_file.exists():
        token = token_file.read_text().strip()
    return api_url, token


def _api_request(endpoint, method="GET", data=None, timeout=30):
    """Make a request to the Tandem API. Auto-launches Tandem if needed."""
    _ensure_tandem_running()
    api_url, token = _get_config()
    url = f"{api_url}{endpoint}"

    headers = {"Authorization": f"Bearer {token}"}
    body = None
    if data is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(data).encode("utf-8")

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result
    except urllib.error.URLError as e:
        return {"error": f"Tandem Browser not reachable: {e}. Is it running?"}
    except Exception as e:
        return {"error": str(e)}


def _format_result(result):
    """Format API result, returning error string or JSON."""
    if isinstance(result, dict) and "error" in result:
        return result["error"]
    text = json.dumps(result, indent=2)
    if len(text) > 8000:
        text = text[:8000] + "\n\n[Output truncated]"
    return text


def browse_url(url, new_tab=False):
    """Open a URL in the browser."""
    global _has_navigated
    _has_navigated = True
    if new_tab:
        result = _api_request("/tabs/open", method="POST", data={"url": url})
    else:
        result = _api_request("/navigate", method="POST", data={"url": url})
    if isinstance(result, dict) and "error" in result:
        return result["error"]

    # Wait for page to actually load (up to 8 seconds)
    for _ in range(8):
        time.sleep(1)
        page = _api_request("/page-content", timeout=10)
        if isinstance(page, dict) and not page.get("error"):
            current_url = page.get("url", "")
            # Check if we've actually navigated away from the old page
            if current_url and (url.split("//")[-1].split("/")[0] in current_url):
                title = page.get("title", "")
                return f"Navigated to: {title}\nURL: {current_url}\n(Page loaded successfully)"
    # Navigation failed — reset to DuckDuckGo home
    _api_request("/navigate", method="POST", data={"url": "https://duckduckgo.com"})
    return f"Navigation to {url} failed. Browser has been reset to DuckDuckGo. Try again with tandem_browse or tandem_search."


def web_search(query):
    """Search the web via DuckDuckGo in Tandem Browser."""
    global _has_navigated
    _has_navigated = True
    import urllib.parse
    encoded = urllib.parse.quote_plus(query)
    search_url = f"https://duckduckgo.com/?q={encoded}"

    # Navigate straight to DuckDuckGo search (no about:blank delay)
    nav = _api_request("/navigate", method="POST", data={"url": search_url})
    if isinstance(nav, dict) and "error" in nav:
        return nav["error"]

    # Wait for OUR search results — verify query is in URL
    loaded = False
    for _ in range(8):
        time.sleep(0.5)
        page = _api_request("/page-content", timeout=5)
        if isinstance(page, dict) and not page.get("error"):
            current_url = page.get("url", "")
            if "duckduckgo.com" in current_url and encoded in current_url:
                loaded = True
                break

    if not loaded:
        # Fallback: wait for #links element
        _api_request("/wait", method="POST",
                     data={"selector": "#links", "timeout": 8000}, timeout=12)
        time.sleep(1)

    # Read final page content
    result = _api_request("/page-content", timeout=15)
    if isinstance(result, dict) and "error" in result:
        return result["error"]

    current_url = result.get("url", "")
    title = result.get("title", "")
    text = result.get("text", "")

    if "duckduckgo.com" not in current_url or encoded not in current_url:
        # Reset to DuckDuckGo home on failure
        _api_request("/navigate", method="POST", data={"url": "https://duckduckgo.com"})
        return (f"Search navigation failed. Browser has been reset to DuckDuckGo.\n"
                f"Try searching again with tandem_search.")

    if not text:
        return f"Search for '{query}' loaded but has no readable text yet. Try tandem_read_page in a few seconds."
    # Truncate search results to avoid flooding LLM context
    if len(text) > 2000:
        text = text[:2000] + "\n\n[Results truncated. Click a link for full details.]"
    return (f"Search results for: \"{query}\"\n"
            f"IMPORTANT: Your search query must be based ONLY on the user's LAST message. "
            f"If unsure, re-read the user's last message before taking any further action.\n"
            f"URL: {current_url}\n\n{text}")


def get_page_content():
    """Get current page content."""
    if not _has_navigated:
        return ("The browser has a page left over from a previous session. "
                "IGNORE its content — it is NOT relevant to the current task. "
                "Use tandem_search to search for what you need, or tandem_browse to go to a specific URL.")
    result = _api_request("/page-content")
    if isinstance(result, dict) and "error" in result:
        return result["error"]
    title = result.get("title", "")
    url = result.get("url", "")
    text = result.get("text", "")
    if not text:
        return f"Page '{title}' ({url}) has no readable text content."
    # Truncate to avoid flooding the LLM context window
    if len(text) > 8000:
        text = text[:8000] + "\n\n[Page content truncated. Use tandem_extract for specific data.]"
    return f"Page: {title}\nURL: {url}\n\n{text}"


def get_snapshot(interactive=True):
    """Get accessibility snapshot of the page."""
    if not _has_navigated:
        return ("The browser has a page left over from a previous session. "
                "IGNORE its content — it is NOT relevant to the current task. "
                "Use tandem_search to search for what you need, or tandem_browse to go to a specific URL.")
    params = f"?interactive={'true' if interactive else 'false'}&compact=true"
    result = _api_request(f"/snapshot{params}")
    return _format_result(result)


def take_screenshot():
    """Take a screenshot of the current tab."""
    result = _api_request("/screenshot")
    if isinstance(result, dict) and "error" in result:
        return result["error"]
    # Don't return the massive base64 data, just confirm
    info = {k: v for k, v in result.items() if k not in ("data", "image", "base64")}
    return "Screenshot captured. " + json.dumps(info)


def click_element(selector=None, ref=None):
    """Click an element by CSS selector or accessibility ref."""
    if ref:
        # Refs are ephemeral — must refresh snapshot first, then click immediately
        _api_request("/snapshot?interactive=true&compact=true")
        result = _api_request("/snapshot/click", method="POST", data={"ref": ref})
        # If ref failed, try to find a link with matching text via CSS
        if isinstance(result, dict) and "error" in result and selector:
            result = _api_request("/click", method="POST", data={"selector": selector})
    elif selector:
        result = _api_request("/click", method="POST", data={"selector": selector})
    else:
        return "Error: provide either selector or ref"
    if isinstance(result, dict) and "error" in result:
        return result["error"]
    return f"Clicked element: {ref or selector}"


def click_link(text):
    """Click a link by its visible text — most reliable click method."""
    # Use JavaScript to find and click a link containing the text
    escaped = text.replace("'", "\\'").replace('"', '\\"')
    js_code = f"""
    (function() {{
        var links = document.querySelectorAll('a');
        var target = null;
        var searchText = '{escaped}'.toLowerCase();
        for (var i = 0; i < links.length; i++) {{
            if (links[i].textContent.toLowerCase().indexOf(searchText) !== -1) {{
                target = links[i];
                break;
            }}
        }}
        if (target) {{
            target.click();
            return 'Clicked: ' + target.textContent.trim().substring(0, 100);
        }} else {{
            return 'ERROR: No link found containing "' + searchText + '"';
        }}
    }})()
    """
    result = _api_request("/execute-js", method="POST", data={"code": js_code})
    if isinstance(result, dict) and "error" in result:
        return result["error"]
    ret = result.get("result", str(result))
    if isinstance(ret, str) and ret.startswith("ERROR:"):
        return ret
    return ret


def type_text(text, selector=None, ref=None, clear=True):
    """Type text into an input field."""
    if ref:
        result = _api_request("/snapshot/fill", method="POST",
                              data={"ref": ref, "value": text})
    elif selector:
        result = _api_request("/type", method="POST",
                              data={"selector": selector, "text": text, "clear": clear})
    else:
        return "Error: provide either selector or ref"
    if isinstance(result, dict) and "error" in result:
        return result["error"]
    return f"Typed '{text}' into {ref or selector}"


def scroll_page(direction, amount=500):
    """Scroll the page."""
    result = _api_request("/scroll", method="POST",
                          data={"direction": direction, "amount": amount})
    if isinstance(result, dict) and "error" in result:
        return result["error"]
    return f"Scrolled {direction} by {amount}px"


def wait_for_element(selector, timeout=5000):
    """Wait for an element to appear."""
    result = _api_request("/wait", method="POST",
                          data={"selector": selector, "timeout": timeout},
                          timeout=max(timeout // 1000 + 5, 15))
    if isinstance(result, dict) and "error" in result:
        return result["error"]
    return f"Element found: {selector}"


def extract_url_content(url):
    """Extract content from a URL without navigating."""
    result = _api_request("/content/extract/url", method="POST", data={"url": url})
    return _format_result(result)


def execute_javascript(code):
    """Execute JavaScript on the current page."""
    result = _api_request("/execute-js", method="POST", data={"code": code})
    return _format_result(result)


def list_tabs():
    """List all open tabs."""
    result = _api_request("/tabs/list")
    return _format_result(result)


def get_links():
    """Extract links from current page."""
    result = _api_request("/links")
    return _format_result(result)


def get_forms():
    """Extract forms from current page."""
    result = _api_request("/forms")
    return _format_result(result)


def close_tab(tab_id):
    """Close a tab by ID."""
    result = _api_request("/tabs/close", method="POST", data={"tabId": tab_id})
    if isinstance(result, dict) and "error" in result:
        return result["error"]
    return f"Closed tab {tab_id}"


def browser_status():
    """Check browser status."""
    result = _api_request("/status")
    return _format_result(result)


def execute(function_name, arguments, config):
    """Sapphire plugin dispatcher — routes tool calls to the correct function."""
    global _tool_call_count
    _tool_call_count += 1

    # Log tool calls with arguments for debugging
    arg_summary = {k: (v[:80] + '...' if isinstance(v, str) and len(v) > 80 else v) for k, v in (arguments or {}).items()}
    logger.info(f"Tandem tool call #{_tool_call_count}: {function_name}({arg_summary})")

    # Start the Wingman chat bridge on first tool call
    _start_wingman_bridge()
    try:
        if function_name == "tandem_browse":
            result = browse_url(
                url=arguments.get("url", ""),
                new_tab=arguments.get("new_tab", False)
            )
        elif function_name == "tandem_search":
            _tool_call_count = 1  # Reset counter — new search = new task
            result = web_search(query=arguments.get("query", ""))
        elif function_name == "tandem_read_page":
            result = get_page_content()
        elif function_name == "tandem_snapshot":
            result = get_snapshot(
                interactive=arguments.get("interactive", True)
            )
        elif function_name == "tandem_screenshot":
            result = take_screenshot()
        elif function_name == "tandem_click":
            result = click_element(
                selector=arguments.get("selector"),
                ref=arguments.get("ref")
            )
        elif function_name == "tandem_click_link":
            result = click_link(
                text=arguments.get("text", "")
            )
        elif function_name == "tandem_type":
            result = type_text(
                text=arguments.get("text", ""),
                selector=arguments.get("selector"),
                ref=arguments.get("ref"),
                clear=arguments.get("clear", True)
            )
        elif function_name == "tandem_scroll":
            result = scroll_page(
                direction=arguments.get("direction", "down"),
                amount=arguments.get("amount", 500)
            )
        elif function_name == "tandem_wait":
            result = wait_for_element(
                selector=arguments.get("selector", ""),
                timeout=arguments.get("timeout", 5000)
            )
        elif function_name == "tandem_extract":
            result = extract_url_content(url=arguments.get("url", ""))
        elif function_name == "tandem_js":
            result = execute_javascript(code=arguments.get("code", ""))
        elif function_name == "tandem_tabs":
            result = list_tabs()
        elif function_name == "tandem_links":
            result = get_links()
        elif function_name == "tandem_forms":
            result = get_forms()
        elif function_name == "tandem_close_tab":
            result = close_tab(tab_id=arguments.get("tab_id", ""))
        elif function_name == "tandem_status":
            result = browser_status()
        else:
            return f"Unknown function '{function_name}'.", False

        # After reaching the limit, nudge the AI to wrap up
        if _tool_call_count >= _MAX_TOOL_CALLS:
            result += ("\n\n⚠ You have used the browser multiple times. "
                       "Stop browsing and provide your answer now based on "
                       "the information you already gathered. "
                       "Re-read the user's LAST message and respond to it.")

        return result, True
    except Exception as e:
        logger.error(f"Tandem Browser tool error: {e}", exc_info=True)
        return f"Error: {e}", False
