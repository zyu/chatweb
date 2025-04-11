# -*- coding: utf-8 -*-
import socketify
from socketify import AppOptions, OpCode # Import OpCode
import asyncio
import json
import logging
import os
import random
import string
# import socket # socket module is not explicitly needed for this logic
from dotenv import load_dotenv

# Load .env file at the start
load_dotenv()

# Configure logging
log_level_name = os.getenv('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(level=getattr(logging, log_level_name, logging.INFO),
                    format='%(asctime)s - %(levelname)s - %(message)s')

# Default room name
DEFAULT_ROOM = "general"

# --- Helper Function ---
def generate_random_userid(length=4):
    """Generates a random alphanumeric ID of specified length."""
    characters = string.ascii_letters + string.digits
    return ''.join(random.choice(characters) for _ in range(length))

# --- WebSocket Event Handlers (Async) ---
async def ws_upgrade(res, req, socket_context):
    """(Async) Handles WebSocket upgrade requests."""
    global app # Ensure app is accessible
    if 'app' not in globals():
         logging.error("App instance not found in global scope during upgrade")
         res.write_status(500).end("Internal Server Error")
         return

    key = req.get_header("sec-websocket-key")
    protocol = req.get_header("sec-websocket-protocol")
    extensions = req.get_header("sec-websocket-extensions")

    # Generate random user_id during upgrade
    user_id = generate_random_userid(4)
    logging.info(f"为新连接生成 User ID: {user_id}") # Generating User ID for new connection

    # Store app instance and generated user_id in user_data
    user_data = {
        "app": app,
        "user_id": user_id
    }

    # Perform upgrade and pass user_data to the connection
    res.upgrade(key, protocol, extensions, socket_context, user_data)


# --- Helper Function for Address Decoding (More Robust) ---
def decode_remote_address(ws) -> str:
    """Attempts to decode the WebSocket's remote address robustly."""
    remote_address_str = "未知地址(获取失败)" # Unknown address (fetch failed)
    try:
        # ws.get_remote_address() returns a tuple (address_bytes, port)
        # address_bytes is typically IPv6 (16 bytes) or IPv4 (4 bytes)
        addr_tuple = ws.get_remote_address_bytes() # Use _bytes version

        if addr_tuple and isinstance(addr_tuple, tuple) and len(addr_tuple) == 2:
            address_bytes, port = addr_tuple

            # Handle None or empty address bytes
            if not address_bytes:
                 logging.warning("get_remote_address_bytes() returned empty address bytes.")
                 return f"未知地址(空字节):{port}" # Unknown address (empty bytes)

            # Attempt to decode common formats
            try:
                # Check for IPv4-mapped IPv6 ::ffff:x.x.x.x
                if len(address_bytes) == 16 and address_bytes.startswith(b'\x00'*10 + b'\xff\xff'):
                    ipv4_bytes = address_bytes[12:]
                    # Convert 4 bytes to standard IPv4 string
                    ip_str = ".".join(map(str, ipv4_bytes))
                    remote_address_str = f"{ip_str}:{port}"
                # Check for standard IPv4
                elif len(address_bytes) == 4:
                    ip_str = ".".join(map(str, address_bytes))
                    remote_address_str = f"{ip_str}:{port}"
                # Check for standard IPv6 (basic representation)
                elif len(address_bytes) == 16:
                    # This provides a hex representation, not the standard compressed IPv6 format
                    # For a full IPv6 format, socket.inet_ntop(socket.AF_INET6, address_bytes) could be used
                    # but requires the socket module and adds complexity. Logging hex is often sufficient.
                    ip_str = address_bytes.hex()
                    remote_address_str = f"[{ip_str}]:{port}" # Use [] for IPv6 per convention
                else:
                    logging.warning(f"get_remote_address_bytes() returned unexpected address bytes length: {len(address_bytes)}")
                    remote_address_str = f"未知地址(格式错误):{port}" # Unknown address (format error)

            except Exception as decode_e:
                 logging.error(f"Error decoding address bytes {address_bytes!r}: {decode_e}")
                 remote_address_str = f"未知地址(解码错误):{port}" # Unknown address (decode error)

        elif addr_tuple is None:
             logging.warning("get_remote_address_bytes() returned None.")
             remote_address_str = "未知地址(返回None)" # Unknown address (returned None)
        else:
             # Log the unexpected format
             logging.warning(f"get_remote_address_bytes() 返回非预期格式: {addr_tuple}") # Returned unexpected format
             remote_address_str = "未知地址(格式错误)" # Unknown address (format error)

    except Exception as e:
        # Catch potential errors from ws.get_remote_address_bytes() itself
        logging.error(f"获取或解码 remote_address 时出错: {e}") # Error getting or decoding remote_address
        # Keep the initial "未知地址(获取失败)"

    return remote_address_str

# --- WebSocket Handlers using the helper ---
async def ws_open(ws):
    """(Async) Handles new WebSocket connection (called after successful upgrade)."""
    user_data = ws.get_user_data()
    if not user_data or "user_id" not in user_data:
        logging.error("User data (app/user_id) not found on WebSocket open")
        ws.close() # Close connection if essential data is missing
        return

    user_id = user_data.get("user_id")
    remote_address_str = decode_remote_address(ws) # Use helper to decode

    logging.info(f"客户端连接成功 (升级后): {remote_address_str} (ID: {user_id})") # Client connected successfully (after upgrade)

    ws.subscribe(DEFAULT_ROOM)
    logging.info(f"用户 {user_id} 加入房间: {DEFAULT_ROOM}") # User joined room

    # Prepare the join message
    join_message = json.dumps({
        "type": "user_join",
        "user": user_id,
        "timestamp": asyncio.get_event_loop().time()
    })

    # Broadcast the join message to the room
    # NOTE: The frontend also adds a local "join" message. Decide if you want both.
    # If this is enabled, everyone in the room (including the new user) gets notified.
    try:
        # Use app instance from user_data to publish globally to the room
        app_instance = user_data.get("app")
        if app_instance:
             app_instance.publish(DEFAULT_ROOM, join_message, False) # False = don't exclude self
             logging.info(f"已广播用户 {user_id} 加入房间 {DEFAULT_ROOM} 的消息 (From ws_open)") # Broadcasted user join message for room
        else:
             logging.error(f"无法在 ws_open 中获取 app 实例来广播用户 {user_id} 加入的消息") # Could not get app instance in ws_open to broadcast user join message
    except Exception as pub_err:
        logging.error(f"广播用户 {user_id} 加入房间 {DEFAULT_ROOM} 消息时出错: {pub_err}") # Error broadcasting user join message for room

    # Original logging indicating skipping (can be removed if broadcasting is enabled)
    # logging.info(f"ws_open: 跳过立即发布用户 {user_id} 加入的消息 (用于测试)") # Skipping immediate publish of user join message (for testing)


async def ws_message(ws, message, opcode):
    """(Async) Handles received WebSocket messages."""
    user_data = ws.get_user_data()
    if not user_data or "user_id" not in user_data:
        logging.error("User data (app/user_id) not found on WebSocket message")
        return # Or close connection

    user_id = user_data.get("user_id")
    remote_address_str = decode_remote_address(ws) # Use helper to decode

    # --- Message Type Handling ---
    message_content = None
    is_text = False

    if opcode == OpCode.TEXT:
        # Message should be string
        if isinstance(message, str):
            message_content = message
            is_text = True
            logging.debug(f"收到来自 {remote_address_str} ({user_id}) 的文本消息: {message_content}") # Received text message from
        else:
            # This case should ideally not happen if opcode is TEXT, but handle defensively
            logging.error(f"收到来自 {remote_address_str} ({user_id}) 的消息，OpCode 是 TEXT 但类型不是 str: {type(message)}") # Received message, OpCode is TEXT but type is not str
            try:
                # Try decoding if it looks like bytes
                message_content = message.decode('utf-8', errors='replace')
                is_text = True
                logging.warning(f"尝试将非 str 文本消息解码为: {message_content}") # Attempted to decode non-str text message to
            except Exception:
                 logging.error(f"无法解码标记为 TEXT 的非 str 消息: {message!r}") # Could not decode non-str message marked as TEXT
                 # Optionally send an error back to the client
                 return
    elif opcode == OpCode.BINARY:
        # Handle binary message - currently just logging it
        logging.info(f"收到来自 {remote_address_str} ({user_id}) 的二进制消息 (长度: {len(message)})") # Received binary message from (length: ...)
        # Decide how to handle binary data (e.g., ignore, process, forward)
        # For a simple chat, we might ignore or reject binary messages
        # message_content = message # Keep as bytes if needed
        return # Ignore binary messages for now in this chat example
    elif opcode == OpCode.PING:
        # socketify handles PONG automatically, but you can log if needed
        logging.debug(f"收到来自 {remote_address_str} ({user_id}) 的 PING") # Received PING from
        # ws.send Pong is handled by the library
        return
    elif opcode == OpCode.PONG:
        logging.debug(f"收到来自 {remote_address_str} ({user_id}) 的 PONG") # Received PONG from
        # Handle PONG if you have custom keepalive logic
        return
    elif opcode == OpCode.CLOSE:
         # Close frame is handled by ws_close, shouldn't arrive here typically
         logging.warning(f"收到来自 {remote_address_str} ({user_id}) 的 CLOSE OpCode 在 ws_message 中") # Received CLOSE OpCode in ws_message from
         return
    else:
         logging.warning(f"收到来自 {remote_address_str} ({user_id}) 的未知 OpCode: {opcode}") # Received unknown OpCode from
         return

    # --- Process Text Message ---
    if is_text and message_content:
        try:
            data = json.loads(message_content)
            data['user'] = user_id # Add user ID to the data
            data['timestamp'] = asyncio.get_event_loop().time() # Add server timestamp

            # Basic validation of expected chat message format (from client)
            if data.get('type') == 'chat' and 'message' in data:
                # Get app instance to publish globally
                app_instance = user_data.get("app")
                if app_instance:
                    try:
                        # Publish the message to the room
                        # Ensure data is dumped back to JSON string for publishing
                        app_instance.publish(DEFAULT_ROOM, json.dumps(data), False) # Publish as text
                        logging.debug(f"广播来自 {user_id} 的聊天消息到房间 {DEFAULT_ROOM}") # Broadcasting chat message from user to room
                    except Exception as pub_err:
                        logging.error(f"广播来自用户 {user_id} 的聊天消息时出错: {pub_err}") # Error broadcasting chat message from user
                else:
                     logging.error(f"无法在 ws_message 中获取 app 实例来广播用户 {user_id} 的聊天消息") # Could not get app instance in ws_message to broadcast user chat message
            else:
                logging.warning(f"收到来自 {remote_address_str} ({user_id}) 的未知或无效消息类型: {data.get('type')}") # Received unknown or invalid message type from

        except json.JSONDecodeError:
            logging.error(f"无法解析来自 {remote_address_str} ({user_id}) 的 JSON 消息: {message_content}") # Cannot parse JSON message from
            try:
                # Send error back to the specific client
                error_msg = json.dumps({
                    "type": "error", "message": "无效的消息格式 (非 JSON 文本)", # Invalid message format (non-JSON text)
                    "timestamp": asyncio.get_event_loop().time()
                })
                ws.send(error_msg, OpCode.TEXT) # Send error as text
            except Exception as send_err:
                 logging.error(f"向客户端 {remote_address_str} 发送 JSON 错误消息失败: {send_err}") # Failed to send JSON error message to client
        except Exception as e:
            logging.error(f"处理来自 {remote_address_str} ({user_id}) 的消息时出错: {e}") # Error processing message from


async def ws_close(ws, code, message):
    """(Async) Handles WebSocket connection closure."""
    user_data = ws.get_user_data()
    user_id = "未知用户" # Unknown user
    app_instance = None # Use a different name to avoid confusion with global 'app'
    remote_address_str = decode_remote_address(ws) # Use helper to decode

    if user_data:
        user_id = user_data.get("user_id", "未知用户(获取失败)") # Unknown user (fetch failed)
        app_instance = user_data.get("app") # Get the app instance stored during upgrade
    else:
        # This might happen if the connection closes before ws_open completes or fails early
        logging.warning(f"连接关闭时未找到用户数据 (app/user_id), code: {code}, address: {remote_address_str}") # User data not found on connection close

    # Decode the close message if it exists and is bytes
    message_str = ""
    if message: # Check if message is not None
        if isinstance(message, bytes):
            try:
                message_str = message.decode('utf-8', errors='replace')
            except Exception as decode_err:
                logging.warning(f"无法解码关闭消息字节: {message!r}, error: {decode_err}") # Could not decode close message bytes
                message_str = "(无法解码的消息)" # (Undecodable message)
        elif isinstance(message, str):
             message_str = message # Already a string
        else:
             message_str = f"(非预期类型: {type(message)})" # (Unexpected type: ...)

    logging.info(f"客户端断开连接: {remote_address_str} (ID: {user_id}), code: {code}, message: {message_str}") # Client disconnected

    # Only broadcast leave message if user_id is known and app_instance is available
    if user_id != "未知用户" and user_id != "未知用户(获取失败)" and app_instance:
        # Prepare leave message
        leave_message = json.dumps({
            "type": "user_leave",
            "user": user_id,
            "timestamp": asyncio.get_event_loop().time()
        })

        # Publish leave message using the app instance stored in user_data
        try:
            # Publish as text
            app_instance.publish(DEFAULT_ROOM, leave_message, False)
            logging.info(f"已广播用户 {user_id} 离开房间 {DEFAULT_ROOM} 的消息") # Broadcasted user leave message for room
        except Exception as pub_err:
            # Log error if publishing fails
            logging.error(f"尝试在 ws_close 中广播用户 {user_id} 离开消息时出错: {pub_err}") # Error broadcasting user leave message in ws_close
    elif not app_instance and user_id != "未知用户":
         logging.error(f"无法在 ws_close 中获取 app 实例来广播用户 {user_id} 离开的消息") # Could not get app instance in ws_close to broadcast user leave message


# --- HTTP Handler (Async) ---
async def home(res, req):
    """(Async) Handles HTTP GET requests for the root path '/'."""
    res.write_header('Content-Type', 'text/plain; charset=utf-8')
    res.end("安全聊天室后端正在运行 (使用 Poetry, 随机用户 ID, WSS/HTTPS)") # Secure chat backend running...


# --- Main Application Setup and Start ---
if __name__ == "__main__":

    # --- SSL/TLS Configuration ---
    ssl_certfile = os.getenv('SSL_CERTFILE', 'cert.pem')
    ssl_keyfile = os.getenv('SSL_KEYFILE', 'key.pem')
    ssl_passphrase = os.getenv('SSL_PASSPHRASE')
    app_options = None

    # Check if SSL files are configured and exist
    if ssl_certfile and ssl_keyfile:
        if os.path.exists(ssl_certfile) and os.path.exists(ssl_keyfile):
            try:
                # Create AppOptions for SSL
                app_options = AppOptions(
                    key_file_name=ssl_keyfile,
                    cert_file_name=ssl_certfile,
                    passphrase=ssl_passphrase
                )
                logging.info(f"找到 SSL 证书和密钥文件，将使用 AppOptions 启用 HTTPS/WSS。 Cert: {ssl_certfile}, Key: {ssl_keyfile}") # Found SSL cert/key files, enabling HTTPS/WSS with AppOptions
            except Exception as e:
                 # Log error if AppOptions creation fails
                 logging.error(f"创建 AppOptions 时出错: {e}") # Error creating AppOptions
                 app_options = None # Fallback to HTTP/WS
        else:
            # Log warning if files are specified but not found
            logging.warning(f"SSL 证书或密钥文件路径无效或文件不存在 ({ssl_certfile}, {ssl_keyfile})，将以 HTTP/WS 模式运行。") # SSL cert or key file path invalid or file not found, running in HTTP/WS mode
    else:
        # Log warning if SSL environment variables are not set
        logging.warning("未配置 SSL_CERTFILE 和 SSL_KEYFILE 环境变量 (或值为空)，将以 HTTP/WS 模式运行。") # SSL_CERTFILE and SSL_KEYFILE env vars not configured, running in HTTP/WS mode

    # Create the Socketify App, passing AppOptions if available
    app = socketify.App(app_options)
    if app_options:
        logging.info("Socketify App 初始化时传入了 AppOptions。") # Socketify App initialized with AppOptions
    else:
        logging.info("Socketify App 初始化时未传入 AppOptions (HTTP/WS 模式)。") # Socketify App initialized without AppOptions (HTTP/WS mode)

    # Store app instance globally for access in handlers (especially ws_upgrade)
    # Consider alternatives like dependency injection if the app grows complex
    globals()['app'] = app

    # WebSocket configuration options
    ws_options = {
        "compression": 0, # Disable compression unless needed (0 = disabled)
        "max_payload_length": 16 * 1024, # Max message size (16KB)
        "idle_timeout": 300, # Timeout in seconds (5 minutes)
        "upgrade": ws_upgrade, # Handler for upgrade request
        "open": ws_open,       # Handler for connection open
        "message": ws_message, # Handler for incoming messages
        "close": ws_close,     # Handler for connection close
        # "drain": ws_drain,    # Optional: Handle backpressure
        # "ping": ws_ping,      # Optional: Handle PING frames (usually automatic PONG)
        # "pong": ws_pong       # Optional: Handle PONG frames
    }

    # Register WebSocket route
    app.ws("/ws", ws_options)
    # Register HTTP route
    app.get("/", home)

    # Get host and port from environment variables or use defaults
    port = int(os.getenv('PORT', '8011'))
    host = os.getenv('HOST', '0.0.0.0')

    logging.info(f"服务器正在启动，监听地址 {host}:{port}...") # Server starting, listening on address...

    # Define the callback for successful listening
    def on_listen(config):
        protocol = 'https' if app_options else 'http'
        ws_protocol = 'wss' if app_options else 'ws'
        # Determine the display host address
        display_host = config.host if config.host != '0.0.0.0' else '127.0.0.1' # Use 127.0.0.1 for display if listening on 0.0.0.0
        if config.host == '0.0.0.0':
             logging.info(f"服务器正在监听所有接口。请使用你的机器的实际 IP 地址或 'localhost'/'127.0.0.1' 访问。") # Server listening on all interfaces. Please use your machine's actual IP address or 'localhost'/'127.0.0.1' to access.

        logging.info(
            f"成功启动! " # Successfully started!
            f"访问 {protocol}://{display_host}:{config.port} 或 " # Visit ... or ...
            f"{ws_protocol}://{display_host}:{config.port}/ws"
        )

    # Start listening
    app.listen(port, host, on_listen) # Pass host here

    # Run the application's event loop
    app.run()