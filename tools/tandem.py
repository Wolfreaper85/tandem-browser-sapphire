# Tandem Browser tool — plugin tool
"""
Tandem Browser — AI can browse the web, take screenshots, read page content,
click elements, fill forms, and interact with web pages through the local
Tandem Browser API.
"""

import json
import logging
import os
import platform
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Platform detection ──
IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")


def _get_electron_path(app_dir):
    """Get the Electron executable path for the current platform."""
    if IS_WINDOWS:
        return app_dir / "node_modules" / "electron" / "dist" / "electron.exe"
    elif IS_MAC:
        return app_dir / "node_modules" / "electron" / "dist" / "Electron.app" / "Contents" / "MacOS" / "Electron"
    else:
        return app_dir / "node_modules" / "electron" / "dist" / "electron"


def _get_node_exe_name():
    """Get the node executable name for the current platform."""
    return "node.exe" if IS_WINDOWS else "node"


def _get_npm_cmd(node_dir):
    """Get the npm command for the current platform."""
    if IS_WINDOWS and (node_dir / "npm.cmd").exists():
        return str(node_dir / "npm.cmd")
    elif not IS_WINDOWS and (node_dir / "bin" / "npm").exists():
        return str(node_dir / "bin" / "npm")
    return "npm"


def _get_popen_flags():
    """Get platform-specific Popen flags."""
    if IS_WINDOWS:
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


# ═══════════════════════════════════════════════════════════
# WINGMAN CHAT BRIDGE — Two-way bridge between Tandem Wingman and Sapphire
# - Wingman → Sapphire: forwards robin messages (waits if Sapphire is busy)
# - Sapphire → Wingman: mirrors AI replies into the Wingman panel
# ═══════════════════════════════════════════════════════════
_bridge_thread = None
_bridge_running = False
_has_navigated = False  # Track if AI has navigated in this session
_tool_call_count = 0    # Track tool calls per chat turn
_last_tool_time = 0     # Timestamp of last tool call — resets counter after gap
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
    # Capped at 500 entries to prevent unbounded growth over long sessions.
    posted_hashes = set()
    MAX_POSTED_HASHES = 500

    def _content_hash(text):
        """Short hash of message content for dedup."""
        return hashlib.md5(text[:500].encode("utf-8", errors="replace")).hexdigest()

    def _track_hash(h):
        """Add a hash to posted_hashes, evicting oldest entries if over cap."""
        if len(posted_hashes) >= MAX_POSTED_HASHES:
            # Remove ~20% of entries to avoid evicting on every add
            to_remove = list(posted_hashes)[:MAX_POSTED_HASHES // 5]
            for old in to_remove:
                posted_hashes.discard(old)
        posted_hashes.add(h)

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
            time.sleep(5)  # Reduced polling frequency to ease load on Tandem
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
                    _track_hash(_content_hash(reply))
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
                    _track_hash(h)
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
            last_chunk_time = time.time()
            STREAM_TIMEOUT = 60  # seconds without data = stream is stalled
            for line in resp:
                last_chunk_time = time.time()
                decoded = line.decode("utf-8", errors="replace").strip()
                if not decoded:
                    # Check for stalled stream on empty lines (SSE keepalives)
                    if time.time() - last_chunk_time > STREAM_TIMEOUT:
                        logger.warning("Wingman bridge: Sapphire stream stalled, returning partial reply")
                        break
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

def _get_portable_node_dir():
    """Get the shared portable Node.js directory at ~/.tandem/node/."""
    tandem_dir = Path.home() / ".tandem"
    tandem_dir.mkdir(parents=True, exist_ok=True)
    return tandem_dir / "node"


def _add_node_to_path(node_dir):
    """Add the portable Node.js to PATH for this process."""
    path_dir = str(node_dir) if IS_WINDOWS else str(node_dir / "bin")
    current_path = os.environ.get("PATH", "")
    if path_dir not in current_path:
        os.environ["PATH"] = path_dir + os.pathsep + current_path


def _get_node_exe_path(node_dir):
    """Get the full path to the node executable in a portable install."""
    if IS_WINDOWS:
        return node_dir / _get_node_exe_name()
    else:
        return node_dir / "bin" / _get_node_exe_name()


def _check_node_version(version_str):
    """Check if a Node.js version string meets the minimum requirement (v20+)."""
    try:
        # Parse "v20.18.0" → 20
        major = int(version_str.strip().lstrip("v").split(".")[0])
        return major >= 20
    except (ValueError, IndexError):
        return False


def _ensure_node_available(app_dir):
    """Ensure Node.js is available — download portable version if needed."""
    import zipfile

    # Step 1: Check if Node.js is already on system PATH
    # Use shell=True on Windows to resolve PATH through cmd.exe
    try:
        result = subprocess.run(
            ["node", "--version"],
            capture_output=True, timeout=10,
            shell=IS_WINDOWS
        )
        if result.returncode == 0:
            version = result.stdout.decode().strip()
            if _check_node_version(version):
                logger.info(f"Found system Node.js {version}")
                return True
            else:
                logger.warning(f"System Node.js {version} is too old (need v20+), will use portable version")
    except Exception:
        pass

    # Step 2: Check if we already have portable Node.js at ~/.tandem/node/
    node_dir = _get_portable_node_dir()
    node_exe = _get_node_exe_path(node_dir)
    if node_exe.exists():
        # Verify version of portable install too
        try:
            result = subprocess.run([str(node_exe), "--version"], capture_output=True, timeout=10)
            version = result.stdout.decode().strip()
            if _check_node_version(version):
                _add_node_to_path(node_dir)
                logger.info(f"Using portable Node.js {version} from {node_dir}")
                return True
            else:
                logger.warning(f"Portable Node.js {version} is too old, re-downloading...")
                import shutil
                shutil.rmtree(node_dir, ignore_errors=True)
        except Exception:
            pass

    # Step 3: Download portable Node.js to ~/.tandem/node/
    NODE_VERSION = "v20.18.0"
    arch = platform.machine().lower()
    if arch in ("amd64", "x86_64", "x64"):
        arch_name = "x64"
    elif arch in ("arm64", "aarch64"):
        arch_name = "arm64"
    else:
        arch_name = "x64"

    if IS_WINDOWS:
        archive_name = f"node-{NODE_VERSION}-win-{arch_name}.zip"
    elif IS_MAC:
        archive_name = f"node-{NODE_VERSION}-darwin-{arch_name}.tar.gz"
    else:
        archive_name = f"node-{NODE_VERSION}-linux-{arch_name}.tar.xz"

    download_url = f"https://nodejs.org/dist/{NODE_VERSION}/{archive_name}"
    # Download to ~/.tandem/ (temp location during extraction)
    tandem_dir = Path.home() / ".tandem"
    archive_path = tandem_dir / archive_name

    logger.info(f"Node.js not found — downloading portable Node.js {NODE_VERSION}...")
    logger.info(f"Download URL: {download_url}")

    try:
        req = urllib.request.Request(download_url)
        with urllib.request.urlopen(req, timeout=120) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            with open(archive_path, "wb") as f:
                while True:
                    chunk = resp.read(1024 * 256)  # 256KB chunks
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        pct = int(downloaded / total * 100)
                        if pct % 25 == 0:
                            logger.info(f"Downloading Node.js... {pct}%")

        logger.info("Download complete — extracting...")

        # Extract based on format
        extracted_dir_name = archive_name.replace(".zip", "").replace(".tar.gz", "").replace(".tar.xz", "")

        if IS_WINDOWS:
            with zipfile.ZipFile(archive_path, 'r') as zf:
                zf.extractall(tandem_dir)
        else:
            import tarfile
            with tarfile.open(archive_path, 'r:*') as tf:
                tf.extractall(tandem_dir)

        # Rename extracted folder to just "node"
        extracted_path = tandem_dir / extracted_dir_name
        if extracted_path.exists():
            if node_dir.exists():
                import shutil
                shutil.rmtree(node_dir)
            extracted_path.rename(node_dir)

        # Clean up archive
        archive_path.unlink()

        # Add to PATH
        _add_node_to_path(node_dir)

        # Verify
        result = subprocess.run([str(node_exe), "--version"], capture_output=True, timeout=10)
        if result.returncode == 0:
            logger.info(f"Portable Node.js {result.stdout.decode().strip()} ready")
            return True
        else:
            logger.error("Downloaded Node.js but it failed to run")
            return False

    except Exception as e:
        logger.error(f"Failed to download portable Node.js: {e}")
        if archive_path.exists():
            archive_path.unlink()
        return False


def _auto_install_tandem(app_dir):
    """Auto-install npm dependencies and compile TypeScript on first run."""
    if not (app_dir / "package.json").exists():
        return False

    electron_exe = _get_electron_path(app_dir)

    if electron_exe.exists():
        return True  # Already installed

    # Ensure Node.js is available (download if needed)
    if not _ensure_node_available(app_dir):
        logger.error("Could not get Node.js — installation cannot proceed")
        return False

    # Determine npm command — use portable from ~/.tandem/node/ if available
    node_dir = _get_portable_node_dir()
    npm_cmd = _get_npm_cmd(node_dir)

    logger.info("First run — installing Tandem Browser dependencies (this may take a minute)...")

    def _run_npm(args, timeout_sec):
        """Run an npm command, handling shell=True string quoting on Windows."""
        if IS_WINDOWS:
            # shell=True on Windows needs a single string; quote path for spaces
            cmd = f'"{npm_cmd}" {" ".join(args)}'
            return subprocess.run(cmd, cwd=str(app_dir), capture_output=True, timeout=timeout_sec, shell=True)
        else:
            return subprocess.run([npm_cmd] + args, cwd=str(app_dir), capture_output=True, timeout=timeout_sec)

    try:
        # Run npm install
        install = _run_npm(["install"], 300)
        if install.returncode != 0:
            logger.error(f"npm install failed: {install.stderr.decode()[:500]}")
            return False
        logger.info("npm install complete")

        # Compile TypeScript
        compile_result = _run_npm(["run", "compile"], 120)
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

    electron_exe = _get_electron_path(app_dir)
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
    try:
        electron_exe = _get_electron_path(app_dir)
        logger.info(f"Auto-launching Tandem Browser from {app_dir}")

        # macOS: clear quarantine flags so Gatekeeper doesn't block Electron
        if IS_MAC and electron_exe.exists():
            try:
                subprocess.run(
                    ["xattr", "-cr", str(electron_exe.parent.parent.parent)],  # Electron.app dir
                    capture_output=True, timeout=10
                )
                logger.info("Cleared macOS quarantine flags on Electron.app")
            except Exception as e:
                logger.warning(f"Could not clear quarantine flags: {e}")

        env = os.environ.copy()
        env.pop("ELECTRON_RUN_AS_NODE", None)
        env.pop("ATOM_SHELL_INTERNAL_RUN_AS_NODE", None)

        # Ensure portable Node.js is on PATH for Electron child processes
        node_dir = _get_portable_node_dir()
        node_exe = _get_node_exe_path(node_dir)
        if node_exe.exists():
            _add_node_to_path(node_dir)
            env["PATH"] = os.environ["PATH"]

        # Write stderr to a log file instead of PIPE to avoid deadlock
        # (Electron can fill the 64KB pipe buffer, blocking the process)
        # The file handle intentionally stays open while Electron runs —
        # it's cleaned up when the process exits or on next launch.
        tandem_log_dir = Path.home() / ".tandem"
        tandem_log_dir.mkdir(parents=True, exist_ok=True)
        stderr_log = open(tandem_log_dir / "electron-stderr.log", "w")

        _tandem_process = subprocess.Popen(
            [str(electron_exe), "."],
            cwd=str(app_dir),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=stderr_log,
            **_get_popen_flags()
        )
        # Wait for API to become ready
        for _ in range(15):
            time.sleep(1)
            # Check if process crashed
            if _tandem_process.poll() is not None:
                exit_code = _tandem_process.returncode
                stderr_log.close()
                # Read crash output from log file
                stderr_content = ""
                try:
                    stderr_content = (tandem_log_dir / "electron-stderr.log").read_text(errors="replace")[:1500]
                except Exception:
                    pass
                logger.error(f"Electron crashed on launch (exit code {exit_code})")
                if stderr_content:
                    logger.error(f"Electron stderr: {stderr_content}")
                _tandem_process = None
                return False
            if _is_tandem_running():
                logger.info("Tandem Browser is ready")
                return True
        # Timed out — check log file for clues
        logger.warning("Tandem Browser launched but API not responding after 15 seconds")
        try:
            stderr_log.flush()
            stderr_content = (tandem_log_dir / "electron-stderr.log").read_text(errors="replace")[:1500]
            if stderr_content:
                logger.warning(f"Electron stderr: {stderr_content}")
        except Exception:
            pass
        return False
    except Exception as e:
        logger.error(f"Failed to launch Tandem Browser: {e}")
        return False
    finally:
        _launch_in_progress = False

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
    if not _ensure_tandem_running():
        return {"error": "Tandem Browser could not be started. Check the Sapphire logs for details. The plugin auto-downloads Node.js and dependencies on first run — ensure you have internet access."}
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
    # Extract target domain for matching (handles redirects like facebook.com → www.facebook.com)
    try:
        target_domain = url.split("//")[-1].split("/")[0].replace("www.", "").lower()
    except Exception:
        target_domain = ""

    # Wait 2 seconds for initial load, then check up to 3 more times
    time.sleep(2)
    for attempt in range(4):
        page = _api_request("/page-content", timeout=10)
        if isinstance(page, dict) and not page.get("error"):
            current_url = (page.get("url", "") or "").lower()
            page_text = page.get("text", "") or ""
            title = page.get("title", "") or ""

            # Skip blank/about:blank pages
            if not current_url or current_url in ("about:blank", ""):
                time.sleep(1.5)
                continue

            # Check domain match (strip www for comparison)
            current_domain = current_url.split("//")[-1].split("/")[0].replace("www.", "")
            if target_domain and target_domain in current_domain:
                # Page loaded — but warn if content is empty (might still be loading)
                if not page_text.strip() and attempt < 2:
                    time.sleep(1.5)
                    continue  # Give it more time to render
                return f"Navigated to: {title}\nURL: {current_url}\n(Page loaded successfully)"
        time.sleep(1.5)

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
    # Use fewer polls with longer intervals to reduce API pressure
    loaded = False
    time.sleep(2)  # Initial wait for DuckDuckGo to load
    for _ in range(4):
        page = _api_request("/page-content", timeout=5)
        if isinstance(page, dict) and not page.get("error"):
            current_url = page.get("url", "")
            if "duckduckgo.com" in current_url and encoded in current_url:
                loaded = True
                break
        time.sleep(1.5)

    if not loaded:
        # Fallback: let Tandem wait for the element (single request, no polling)
        _api_request("/wait", method="POST",
                     data={"selector": "#links", "timeout": 8000}, timeout=12)

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
    # Use JSON.stringify for safe escaping of all special characters
    escaped_json = json.dumps(text)  # Produces a properly escaped JSON string
    js_code = f"""
    (function() {{
        var links = document.querySelectorAll('a');
        var target = null;
        var searchText = {escaped_json}.toLowerCase();
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
    global _tool_call_count, _last_tool_time

    # Reset counter if >60 seconds since last tool call (new chat turn)
    now = time.time()
    if now - _last_tool_time > 60:
        _tool_call_count = 0
    _last_tool_time = now
    _tool_call_count += 1

    # Log tool calls with arguments for debugging
    arg_summary = {k: (v[:80] + '...' if isinstance(v, str) and len(v) > 80 else v) for k, v in (arguments or {}).items()}
    logger.info(f"Tandem tool call #{_tool_call_count}: {function_name}({arg_summary})")

    # Hard block after limit — refuse to execute, force the AI to answer
    if _tool_call_count > _MAX_TOOL_CALLS:
        logger.warning(f"Tandem tool call #{_tool_call_count} BLOCKED — over limit of {_MAX_TOOL_CALLS}")
        return ("STOP. You have exceeded the maximum number of browser actions. "
                "Do NOT call any more tools — no tandem tools, no web_search, "
                "no get_website, no browsing tools of ANY kind. You have enough "
                "information. Provide your answer NOW using the information you "
                "already gathered. Re-read the user's LAST message and respond "
                "to it directly."), True

    # Start the Wingman chat bridge on first tool call
    _start_wingman_bridge()
    try:
        if function_name == "tandem_browse":
            result = browse_url(
                url=arguments.get("url", ""),
                new_tab=arguments.get("new_tab", False)
            )
        elif function_name == "tandem_search":
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

        # Warn at limit, hard block comes on the NEXT call
        if _tool_call_count == _MAX_TOOL_CALLS:
            result += ("\n\n⚠ FINAL BROWSER ACTION. You will NOT be able to use "
                       "the browser again after this. Provide your answer NOW "
                       "based on the information you have gathered. "
                       "Re-read the user's LAST message and respond to it.")

        return result, True
    except Exception as e:
        logger.error(f"Tandem Browser tool error: {e}", exc_info=True)
        return f"Error: {e}", False
