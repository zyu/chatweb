# -*- coding: utf-8 -*-
import socketify
from socketify import AppOptions, OpCode
import asyncio
import json
import logging
import os
import random
import string
import time
import threading
# Removed uuid import as it's no longer needed
from dotenv import load_dotenv

# Load .env file at the start
load_dotenv()

# Configure logging
log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level_name, logging.INFO),
    format="%(asctime)s - %(levelname)s - [%(threadName)s] - %(message)s",
)

# Default room name
DEFAULT_ROOM = "general"

# --- Removed Persistent User ID Mapping ---
# client_id_to_user_id_map = {}
# user_id_lock = threading.Lock()

# Store client last activity timestamps for heartbeat monitoring
client_activity = {}
HEARTBEAT_INTERVAL = 60  # Check client activity interval (seconds)
HEARTBEAT_TIMEOUT = 300  # Client timeout duration (seconds)

# Global variable to indicate if the heartbeat checker has started
heartbeat_checker_started = False
heartbeat_thread = None # Keep track of the thread


# --- Helper Function ---
def generate_random_userid(length=4):
    """Generates a random alphanumeric ID of specified length."""
    characters = string.ascii_letters + string.digits
    return "".join(random.choice(characters) for _ in range(length))


# --- Heartbeat Tracker ---
async def heartbeat_checker():
    """Periodically checks client activity and closes timed-out connections."""
    logging.info("Heartbeat checker task started.")
    while True:
        try:
            current_time = time.time()
            inactive_clients = []
            try:
                 clients_to_check = list(client_activity.items())
            except RuntimeError:
                 logging.warning("Heartbeat: Could not iterate client_activity. Skipping cycle.")
                 await asyncio.sleep(HEARTBEAT_INTERVAL)
                 continue

            for ws, last_active in clients_to_check:
                if ws not in client_activity: continue
                if current_time - last_active > HEARTBEAT_TIMEOUT:
                    inactive_clients.append(ws)

            for ws in inactive_clients:
                if ws in client_activity and (time.time() - client_activity.get(ws, 0) > HEARTBEAT_TIMEOUT):
                    try:
                        user_data = ws.get_user_data()
                        # Use chat_user_id stored in user_data for this connection
                        user_id = user_data.get("chat_user_id", "Unknown") if user_data else "Unknown"
                        logging.warning(f"Closing inactive connection: {user_id} (> {HEARTBEAT_TIMEOUT}s)")

                        try:
                            warning_msg = json.dumps({"type": "system", "message": "连接因不活动即将关闭", "timestamp": time.time()})
                            ws.send(warning_msg, OpCode.TEXT)
                        except Exception as send_warn_err:
                            logging.debug(f"Could not send inactivity warning to {user_id}: {send_warn_err}")

                        logging.debug(f"Removing {user_id} from activity tracker.")
                        client_activity.pop(ws, None)
                        logging.debug(f"Calling ws.end() for inactive client {user_id}")
                        ws.end(1000, "Inactive connection timeout")

                    except Exception as e:
                        logging.error(f"Error closing inactive connection for a client ({user_id}): {e}")
                        client_activity.pop(ws, None)

            await asyncio.sleep(HEARTBEAT_INTERVAL)
        except asyncio.CancelledError:
            logging.info("Heartbeat checker task cancelled.")
            break
        except Exception as e:
            logging.error(f"Heartbeat checker error: {e}", exc_info=True)
            await asyncio.sleep(HEARTBEAT_INTERVAL) # Avoid busy-looping


# Function to start the heartbeat checker (remains the same)
def start_heartbeat_checker():
    global heartbeat_checker_started, heartbeat_thread
    if heartbeat_checker_started and heartbeat_thread and heartbeat_thread.is_alive():
        logging.info("Heartbeat checker is already running.")
        return
    loop = asyncio.new_event_loop()
    def run_event_loop():
        logging.info(f"Heartbeat checker thread ({threading.current_thread().name}) starting event loop.")
        asyncio.set_event_loop(loop)
        try:
            checker_task = loop.create_task(heartbeat_checker())
            loop.run_until_complete(checker_task)
        except Exception as e:
            logging.error(f"Heartbeat checker event loop error: {e}", exc_info=True)
        finally:
            logging.info("Closing heartbeat checker event loop.")
            loop.close()
    heartbeat_thread = threading.Thread(target=run_event_loop, name="HeartbeatCheckerThread", daemon=True)
    heartbeat_thread.start()
    heartbeat_checker_started = True
    logging.info("Heartbeat checker thread started.")


# --- WebSocket Event Handlers (Async) ---
async def ws_upgrade(res, req, socket_context):
    """(Async) Handles WebSocket upgrade requests, generating a random user ID."""
    global app
    if "app" not in globals() or app is None:
        logging.error("App instance not found during upgrade")
        res.write_status(500).end("Internal Server Error")
        return

    try:
        # --- Always generate a new random user ID ---
        assigned_chat_user_id = generate_random_userid()
        logging.info(f"Generated new user ID {assigned_chat_user_id} for incoming connection.")

        # --- Proceed with Upgrade ---
        key = req.get_header("sec-websocket-key")
        protocol = req.get_header("sec-websocket-protocol")
        extensions = req.get_header("sec-websocket-extensions")
        remote_ip = "Unknown"
        try:
             remote_ip = req.get_remote_address_bytes().decode('utf-8', errors='replace')
        except Exception:
             logging.warning("Could not get remote IP during upgrade.")


        # Store necessary info in user_data
        user_data = {
            "app": app,
            # Removed client_id
            "chat_user_id": assigned_chat_user_id, # Store the generated chat user ID
            "remote_ip": remote_ip
        }

        # Perform upgrade
        res.upgrade(key, protocol, extensions, socket_context, user_data)
        logging.debug(f"WebSocket upgrade successful for userId {assigned_chat_user_id}")

    except Exception as upgrade_err:
         logging.error(f"WebSocket upgrade failed: {upgrade_err}", exc_info=True)
         try: res.write_status(500).end("Upgrade Error")
         except: pass


# --- Improved Remote Address Handling (remains the same) ---
def decode_remote_address(ws) -> str:
    remote_address_str = "Unknown Address"
    try:
        addr_bytes = ws.get_remote_address_bytes()
        if addr_bytes: remote_address_str = addr_bytes.decode('utf-8', errors='replace')
        else:
            user_data = ws.get_user_data()
            if user_data and "remote_ip" in user_data: remote_address_str = user_data["remote_ip"] + " (from upgrade)"
            else: remote_address_str = "Unknown Address (fetch failed)"
    except Exception as e:
        logging.error(f"Error getting/decoding remote_address: {e}")
        user_data = ws.get_user_data()
        if user_data and "remote_ip" in user_data: remote_address_str = user_data["remote_ip"] + " (from upgrade fallback)"
    return remote_address_str


# --- WebSocket Handlers using the helper ---
async def ws_open(ws):
    """(Async) Handles new WebSocket connection."""
    user_data = None
    chat_user_id = "Unknown(Pre-Data)"
    remote_address_str = "Unknown(Pre-Decode)"
    try:
        user_data = ws.get_user_data()
        if not user_data or "chat_user_id" not in user_data or "app" not in user_data:
            logging.error("User data (app/chat_user_id) missing in ws_open. Closing connection.")
            ws.close()
            return

        chat_user_id = user_data.get("chat_user_id")
        app_instance = user_data.get("app")
        remote_address_str = decode_remote_address(ws)

        logging.info(f"ws_open: Connection opened for user {chat_user_id} from {remote_address_str}")

        client_activity[ws] = time.time()
        logging.debug(f"ws_open: Added user {chat_user_id} to activity tracker.")

        logging.debug(f"ws_open: Subscribing user {chat_user_id} to room '{DEFAULT_ROOM}'...")
        ws.subscribe(DEFAULT_ROOM)
        logging.info(f"ws_open: User {chat_user_id} joined room '{DEFAULT_ROOM}'")

        # --- Send welcome message with the generated ID ---
        try:
            logging.debug(f"ws_open: Preparing welcome message for user {chat_user_id}...")
            welcome_message = json.dumps(
                {
                    "type": "system",
                    "message": f"欢迎, 你的用户ID是: {chat_user_id}", # Standard welcome
                    "user_id": chat_user_id, # Send the generated chat user ID
                    "timestamp": time.time(),
                }
            )
            logging.debug(f"ws_open: Sending welcome message to user {chat_user_id}...")
            ws.send(welcome_message, OpCode.TEXT)
            logging.info(f"ws_open: Sent welcome message to user {chat_user_id}")
        except Exception as send_err:
            logging.error(f"ws_open: Error sending welcome message to user {chat_user_id}: {send_err}", exc_info=True)

        # --- Broadcast join message ---
        try:
            logging.debug(f"ws_open: Preparing join broadcast for user {chat_user_id}...")
            join_message = json.dumps(
                {"type": "user_join", "user": chat_user_id, "timestamp": time.time()}
            )
            if app_instance:
                logging.debug(f"ws_open: Publishing join message for user {chat_user_id} to '{DEFAULT_ROOM}'...")
                app_instance.publish(DEFAULT_ROOM, join_message, OpCode.TEXT)
                logging.info(f"ws_open: Broadcasted join message for user {chat_user_id} to room '{DEFAULT_ROOM}'.")
            else:
                 logging.error("ws_open: app_instance not found, cannot broadcast join message.")
        except Exception as pub_err:
            logging.error(f"ws_open: Error broadcasting join message for user {chat_user_id}: {pub_err}", exc_info=True)

        logging.info(f"ws_open: Handler finished successfully for user {chat_user_id}")

    except Exception as open_err:
        logging.error(f"ws_open: Critical error during connection open for user {chat_user_id}: {open_err}", exc_info=True)
        client_activity.pop(ws, None)
        try: ws.close()
        except Exception as close_err: logging.error(f"ws_open: Error closing connection after open error: {close_err}")


async def ws_message(ws, message, opcode):
    """(Async) Handles received WebSocket messages."""
    user_data = ws.get_user_data()
    if not user_data or "chat_user_id" not in user_data:
        logging.warning("Received message from WebSocket without user_data/chat_user_id.")
        return

    chat_user_id = user_data.get("chat_user_id")
    app_instance = user_data.get("app")
    remote_address_str = decode_remote_address(ws)

    if ws in client_activity: client_activity[ws] = time.time()
    else:
        logging.debug(f"ws_message: Received message from user {chat_user_id} but not in activity tracker.")
        return

    logging.debug(f"ws_message: Received OpCode {opcode} from user {chat_user_id} ({remote_address_str})")

    message_content = None
    is_text = False

    # --- Message Handling (largely unchanged, uses chat_user_id) ---
    if opcode == OpCode.TEXT:
        try:
            if isinstance(message, bytes): message_content = message.decode("utf-8")
            elif isinstance(message, str): message_content = message
            else: logging.error(f"ws_message: Unexpected TEXT type {type(message)} from {chat_user_id}"); return
            is_text = True
            logging.debug(f"ws_message: Decoded TEXT from {chat_user_id}: {message_content}")
        except UnicodeDecodeError: logging.error(f"ws_message: Failed UTF-8 decode from {chat_user_id}"); return
        except Exception as e: logging.error(f"ws_message: Error processing TEXT from {chat_user_id}: {e}", exc_info=True); return
    elif opcode == OpCode.BINARY: logging.info(f"ws_message: Received BINARY from {chat_user_id}. Length: {len(message)}"); return
    elif opcode == OpCode.PING: logging.debug(f"ws_message: Received PING from {chat_user_id}. Relying on auto PONG."); return
    elif opcode == OpCode.PONG: logging.debug(f"ws_message: Received PONG from {chat_user_id}."); return
    elif opcode == OpCode.CLOSE: logging.warning(f"ws_message: Received CLOSE OpCode from {chat_user_id} in message handler."); return
    else: logging.warning(f"ws_message: Received unknown OpCode {opcode} from {chat_user_id}."); return

    # Process Decoded Text Message
    if is_text and message_content:
        try:
            data = json.loads(message_content)
            message_type = data.get("type")
            data["user"] = chat_user_id # Set user field correctly
            if "timestamp" not in data: data["timestamp"] = time.time()

            if message_type == "chat" and "message" in data:
                if app_instance:
                    try:
                        formatted_message = json.dumps(data)
                        logging.debug(f"ws_message: Broadcasting chat from {chat_user_id} to '{DEFAULT_ROOM}'")
                        app_instance.publish(DEFAULT_ROOM, formatted_message, OpCode.TEXT)
                        logging.debug(f"ws_message: Publish called for chat message.")
                    except Exception as pub_err: logging.error(f"ws_message: Error broadcasting chat from {chat_user_id}: {pub_err}", exc_info=True)
                else: logging.error(f"ws_message: app_instance missing for broadcast from {chat_user_id}")
            else: logging.warning(f"ws_message: Invalid message type '{message_type}' from {chat_user_id}: {message_content}")
        except json.JSONDecodeError:
            logging.error(f"ws_message: Failed JSON decode from {chat_user_id}: {message_content}")
            try: ws.send(json.dumps({"type": "error", "message": "无效消息格式", "timestamp": time.time()}), OpCode.TEXT)
            except Exception as send_err: logging.error(f"ws_message: Failed send JSON error to {chat_user_id}: {send_err}")
        except Exception as e: logging.error(f"ws_message: General error processing msg from {chat_user_id}: {e}", exc_info=True)


async def ws_close(ws, code, message):
    """(Async) Handles WebSocket connection closure."""
    user_data = ws.get_user_data()
    chat_user_id = "Unknown"
    app_instance = None
    remote_address_str = decode_remote_address(ws)

    if user_data:
        chat_user_id = user_data.get("chat_user_id", "Unknown(Data Error)")
        app_instance = user_data.get("app")

    client_activity.pop(ws, None)
    logging.debug(f"ws_close: Removed user {chat_user_id} from activity tracker.")

    message_str = ""
    if message:
        try:
            if isinstance(message, bytes): message_str = message.decode("utf-8", errors="replace")
            elif isinstance(message, str): message_str = message
            else: message_str = f"(Unexpected type: {type(message)})"
        except Exception: message_str = "(Undecodable close message)"

    logging.info(f"ws_close: Client disconnected: {remote_address_str} (User ID: {chat_user_id}), Code: {code}, Message: '{message_str}'")

    # Broadcast leave message
    if chat_user_id and chat_user_id not in ["Unknown", "Unknown(Data Error)", "Unknown(Pre-Data)"] and app_instance:
        logging.debug(f"ws_close: Preparing leave broadcast for user {chat_user_id}...")
        leave_message = json.dumps({"type": "user_leave", "user": chat_user_id, "timestamp": time.time()})
        try:
            app_instance.publish(DEFAULT_ROOM, leave_message, OpCode.TEXT)
            logging.info(f"ws_close: Broadcasted leave message for user {chat_user_id} to room '{DEFAULT_ROOM}'.")
        except Exception as pub_err: logging.error(f"ws_close: Error broadcasting leave for {chat_user_id}: {pub_err}", exc_info=True)
    elif not app_instance: logging.warning(f"ws_close: Cannot broadcast leave for {chat_user_id}, app_instance missing.")
    else: logging.debug(f"ws_close: Not broadcasting leave for invalid/unknown user ID '{chat_user_id}'.")


# --- HTTP Handler (Async) ---
async def home(res, req):
    res.write_header("Content-Type", "text/plain; charset=utf-8")
    res.end("Secure Chat Room Backend Running (Random User IDs, WSS/HTTPS)")


# --- Main Application Setup and Start ---
if __name__ == "__main__":
    ssl_certfile = os.getenv("SSL_CERTFILE")
    ssl_keyfile = os.getenv("SSL_KEYFILE")
    ssl_passphrase = os.getenv("SSL_PASSPHRASE")
    app_options = None
    if ssl_certfile and ssl_keyfile and os.path.exists(ssl_certfile) and os.path.exists(ssl_keyfile):
        try:
            app_options = AppOptions(key_file_name=ssl_keyfile, cert_file_name=ssl_certfile, passphrase=ssl_passphrase)
            logging.info(f"SSL cert/key found. Enabling HTTPS/WSS.")
        except Exception as e: logging.error(f"Error creating AppOptions for SSL: {e}", exc_info=True)
    else: logging.warning("SSL cert/key not found or not set. Running in HTTP/WS mode.")

    app = socketify.App(app_options)
    globals()["app"] = app

    ws_options = {
        "compression": 0, "max_payload_length": 16 * 1024, "idle_timeout": 120,
        "max_backpressure": 1 * 1024 * 1024, "max_lifetime": 0, "reset_idle_timeout_on_send": True,
        "send_pings_automatically": True,
        "upgrade": ws_upgrade, "open": ws_open, "message": ws_message, "close": ws_close,
    }
    logging.info(f"WebSocket options configured: {ws_options}")
    app.ws("/ws", ws_options)
    app.get("/", home)
    start_heartbeat_checker()
    port_str = os.getenv("PORT", "8011")
    try: port = int(port_str)
    except ValueError: logging.error(f"Invalid PORT: '{port_str}'. Using 8011."); port = 8011
    host = os.getenv("HOST", "0.0.0.0")
    logging.info(f"Attempting to start server on {host}:{port}...")
    def on_listen(config):
        protocol = "https" if app_options else "http"; ws_protocol = "wss" if app_options else "ws"
        actual_host = config.host if config.host != "0.0.0.0" else "127.0.0.1"; actual_port = config.port
        if config.host == "0.0.0.0": logging.info(f"Server listening on 0.0.0.0:{actual_port}")
        else: logging.info(f"Server listening on {config.host}:{actual_port}")
        logging.info(f"Successfully started!")
        logging.info(f"  HTTP: {protocol}://{actual_host}:{actual_port}/")
        logging.info(f"  WS:   {ws_protocol}://{actual_host}:{actual_port}/ws") # No query param needed now
    def on_error(error): logging.error(f"Failed to listen on {host}:{port} - {error}", exc_info=True)
    try: app.listen(port, on_listen, host=host); app.run()
    except Exception as listen_err: on_error(listen_err)
    logging.info("Server shutdown.")

