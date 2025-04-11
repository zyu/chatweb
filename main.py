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


# --- Helper Function for Address Decoding (Using get_remote_address) ---
def decode_remote_address(ws) -> str:
    """Attempts to decode the WebSocket's remote address using get_remote_address()."""
    remote_address_str = "未知地址(获取失败)" # Unknown address (fetch failed)
    try:
        # Use get_remote_address() which should return (str, int)
        addr_tuple = ws.get_remote_address()

        if addr_tuple and isinstance(addr_tuple, tuple) and len(addr_tuple) == 2:
            ip_str, port = addr_tuple

            # Check if the returned value is actually (str, int)
            if isinstance(ip_str, str) and isinstance(port, int):
                # Handle IPv4-mapped IPv6 string format "::ffff:xxx.xxx.xxx.xxx"
                if ip_str.startswith("::ffff:"):
                    ipv4_part = ip_str.split(":")[-1]
                    # Basic validation for IPv4 format
                    if all(0 <= int(part) <= 255 for part in ipv4_part.split('.') if part.isdigit()):
                         remote_address_str = f"{ipv4_part}:{port}"
                    else:
                         logging.warning(f"解析出的 IPv4 部分格式无效: {ipv4_part}") # Parsed IPv4 part format invalid
                         remote_address_str = f"未知地址(IPv4解析错误):{port}" # Unknown address (IPv4 parse error)
                else:
                    # Assume other formats (like standard IPv4 or IPv6) are okay as strings
                    remote_address_str = f"{ip_str}:{port}"
            else:
                # Log if the tuple doesn't contain (str, int)
                logging.warning(f"get_remote_address() 返回的元组类型不匹配: ({type(ip_str)}, {type(port)})") # Tuple types returned by get_remote_address() do not match
                remote_address_str = f"未知地址(类型错误):{addr_tuple[1] if len(addr_tuple) > 1 else 'N/A'}" # Unknown address (type error)

        elif addr_tuple is None:
             logging.warning("get_remote_address() returned None.")
             remote_address_str = "未知地址(返回None)" # Unknown address (returned None)
        else:
             # Log the unexpected format if it's not a 2-element tuple
             logging.warning(f"get_remote_address() 返回非预期格式: {addr_tuple}") # Returned unexpected format
             remote_address_str = "未知地址(格式错误)" # Unknown address (format error)

    except Exception as e:
        # Catch potential errors from ws.get_remote_address() itself or during processing
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

    # --- Temporarily disable broadcasting join message ---
    # Prepare the join message
    # join_message = json.dumps({
    #     "type": "user_join",
    #     "user": user_id,
    #     "timestamp": asyncio.get_event_loop().time()
    # })
    # try:
    #     app_instance = user_data.get("app")
    #     if app_instance:
    #          app_instance.publish(DEFAULT_ROOM, join_message, False)
    #          logging.info(f"已广播用户 {user_id} 加入房间 {DEFAULT_ROOM} 的消息 (From ws_open)")
    #     else:
    #          logging.error(f"无法在 ws_open 中获取 app 实例来广播用户 {user_id} 加入的消息")
    # except Exception as pub_err:
    #     logging.error(f"广播用户 {user_id} 加入房间 {DEFAULT_ROOM} 消息时出错: {pub_err}")
    logging.info(f"ws_open: 跳过广播用户 {user_id} 加入的消息 (调试 1002 错误)") # Skipping broadcast of user join message (debugging 1002 error)


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
        if isinstance(message, str):
            message_content = message
            is_text = True
            logging.debug(f"收到来自 {remote_address_str} ({user_id}) 的文本消息: {message_content}") # Received text message from
        else:
            logging.error(f"收到来自 {remote_address_str} ({user_id}) 的消息，OpCode 是 TEXT 但类型不是 str: {type(message)}") # Received message, OpCode is TEXT but type is not str
            try:
                message_content = message.decode('utf-8', errors='replace')
                is_text = True
                logging.warning(f"尝试将非 str 文本消息解码为: {message_content}") # Attempted to decode non-str text message to
            except Exception:
                 logging.error(f"无法解码标记为 TEXT 的非 str 消息: {message!r}") # Could not decode non-str message marked as TEXT
                 return
    elif opcode == OpCode.BINARY:
        logging.info(f"收到来自 {remote_address_str} ({user_id}) 的二进制消息 (长度: {len(message)})") # Received binary message from (length: ...)
        return # Ignore binary messages
    elif opcode == OpCode.PING:
        logging.debug(f"收到来自 {remote_address_str} ({user_id}) 的 PING") # Received PING from
        return
    elif opcode == OpCode.PONG:
        logging.debug(f"收到来自 {remote_address_str} ({user_id}) 的 PONG") # Received PONG from
        return
    elif opcode == OpCode.CLOSE:
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
                app_instance = user_data.get("app")
                if app_instance:
                    try:
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
    app_instance = None
    remote_address_str = decode_remote_address(ws) # Use helper to decode

    if user_data:
        user_id = user_data.get("user_id", "未知用户(获取失败)") # Unknown user (fetch failed)
        app_instance = user_data.get("app")
    else:
        logging.warning(f"连接关闭时未找到用户数据 (app/user_id), code: {code}, address: {remote_address_str}") # User data not found on connection close

    message_str = ""
    if message:
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
        leave_message = json.dumps({
            "type": "user_leave",
            "user": user_id,
            "timestamp": asyncio.get_event_loop().time()
        })
        try:
            app_instance.publish(DEFAULT_ROOM, leave_message, False)
            logging.info(f"已广播用户 {user_id} 离开房间 {DEFAULT_ROOM} 的消息") # Broadcasted user leave message for room
        except Exception as pub_err:
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

    if ssl_certfile and ssl_keyfile:
        if os.path.exists(ssl_certfile) and os.path.exists(ssl_keyfile):
            try:
                app_options = AppOptions(
                    key_file_name=ssl_keyfile,
                    cert_file_name=ssl_certfile,
                    passphrase=ssl_passphrase
                )
                logging.info(f"找到 SSL 证书和密钥文件，将使用 AppOptions 启用 HTTPS/WSS。 Cert: {ssl_certfile}, Key: {ssl_keyfile}")
            except Exception as e:
                 logging.error(f"创建 AppOptions 时出错: {e}")
                 app_options = None
        else:
            logging.warning(f"SSL 证书或密钥文件路径无效或文件不存在 ({ssl_certfile}, {ssl_keyfile})，将以 HTTP/WS 模式运行。")
    else:
        logging.warning("未配置 SSL_CERTFILE 和 SSL_KEYFILE 环境变量 (或值为空)，将以 HTTP/WS 模式运行。")


    # Create the Socketify App, passing AppOptions if available
    app = socketify.App(app_options)
    if app_options:
        logging.info("Socketify App 初始化时传入了 AppOptions。")
    else:
        logging.info("Socketify App 初始化时未传入 AppOptions (HTTP/WS 模式)。")

    globals()['app'] = app

    # WebSocket configuration options
    ws_options = {
        "compression": 0,
        "max_payload_length": 16 * 1024,
        "idle_timeout": 300,
        "upgrade": ws_upgrade,
        "open": ws_open,
        "message": ws_message,
        "close": ws_close,
    }


    app.ws("/ws", ws_options)
    app.get("/", home)

    # Get host and port from environment variables or use defaults
    port = int(os.getenv('PORT', '8011'))
    host = os.getenv('HOST', '0.0.0.0')

    logging.info(f"服务器正在启动，监听地址 {host}:{port}...") # Log the intended host and port

    # Define the callback for successful listening
    def on_listen(config):
        protocol = 'https' if app_options else 'http'
        ws_protocol = 'wss' if app_options else 'ws'
        display_host = host if host != '0.0.0.0' else '127.0.0.1'
        actual_port = config.port

        if host == '0.0.0.0':
             logging.info(f"服务器正在监听所有接口 (0.0.0.0) 上的端口 {actual_port}。") # Server listening on all interfaces...
             logging.info(f"请使用你机器的实际 IP 地址或 'localhost'/'127.0.0.1' 访问。") # Please use your machine's actual IP address...

        logging.info(
            f"成功启动! " # Successfully started!
            f"访问 {protocol}://{display_host}:{actual_port} 或 " # Visit ... or ...
            f"{ws_protocol}://{display_host}:{actual_port}/ws"
        )

    # Start listening - Pass only port and callback
    app.listen(port, on_listen)

    # Run the application's event loop
    app.run()