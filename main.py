# -*- coding: utf-8 -*-
import socketify
from socketify import AppOptions, OpCode
import asyncio
import json
import logging
import os
import random
import string
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
    logging.info(f"为新连接生成 User ID: {user_id}")

    # Store app instance and generated user_id in user_data
    user_data = {
        "app": app,
        "user_id": user_id
    }

    # Perform upgrade and pass user_data to the connection
    res.upgrade(key, protocol, extensions, socket_context, user_data)


# --- Improved Remote Address Handling ---
def decode_remote_address(ws) -> str:
    """Decodes the WebSocket's remote address."""
    remote_address_str = "未知地址(获取失败)" # Unknown address (fetch failed)
    try:
        # Try to get the remote address
        addr = ws.get_remote_address()
        
        # Check if addr is a string (which appears to be the case in your environment)
        if isinstance(addr, str):
            # It's already a string representation
            remote_address_str = addr
        elif isinstance(addr, tuple) and len(addr) == 2:
            # It's a (host, port) tuple as originally expected
            ip_str, port = addr
            remote_address_str = f"{ip_str}:{port}"
        else:
            # For any other format, just convert to string representation
            remote_address_str = str(addr)
            
    except Exception as e:
        logging.error(f"获取或解码 remote_address 时出错: {e}")
        
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

    logging.info(f"客户端连接成功 (升级后): {remote_address_str} (ID: {user_id})")

    ws.subscribe(DEFAULT_ROOM)
    logging.info(f"用户 {user_id} 加入房间: {DEFAULT_ROOM}")

    # Send a welcome message to just this user
    try:
        welcome_message = json.dumps({
            "type": "system",
            "message": f"欢迎加入聊天室，您的 ID 是: {user_id}",
            "timestamp": asyncio.get_event_loop().time()
        })
        ws.send(welcome_message, OpCode.TEXT)
        logging.debug(f"已发送欢迎消息给用户 {user_id}")
    except Exception as send_err:
        logging.error(f"发送欢迎消息给用户 {user_id} 时出错: {send_err}")


async def ws_message(ws, message, opcode):
    """(Async) Handles received WebSocket messages."""
    user_data = ws.get_user_data()
    if not user_data or "user_id" not in user_data:
        logging.error("User data (app/user_id) not found on WebSocket message")
        return

    user_id = user_data.get("user_id")
    remote_address_str = decode_remote_address(ws)

    # --- Message Type Handling ---
    message_content = None
    is_text = False

    if opcode == OpCode.TEXT:
        # Handle text messages
        try:
            if isinstance(message, str):
                message_content = message
                is_text = True
            elif isinstance(message, bytes):
                message_content = message.decode('utf-8', errors='replace')
                is_text = True
            else:
                logging.error(f"收到来自 {user_id} 的消息类型不支持: {type(message)}")
                return
                
            logging.debug(f"收到来自 {remote_address_str} ({user_id}) 的文本消息: {message_content}")
        except Exception as e:
            logging.error(f"解析消息内容时出错: {e}")
            return
    elif opcode == OpCode.BINARY:
        logging.info(f"收到来自 {remote_address_str} ({user_id}) 的二进制消息 (长度: {len(message)})")
        return # Ignore binary messages
    else:
        # Handle control frames
        if opcode == OpCode.PING:
            logging.debug(f"收到来自 {user_id} 的 PING")
        elif opcode == OpCode.PONG:
            logging.debug(f"收到来自 {user_id} 的 PONG")
        elif opcode == OpCode.CLOSE:
            logging.warning(f"收到来自 {user_id} 的 CLOSE OpCode 在 ws_message 中")
        else:
            logging.warning(f"收到来自 {user_id} 的未知 OpCode: {opcode}")
        return

    # --- Process Text Message ---
    if is_text and message_content:
        try:
            data = json.loads(message_content)
            
            # Add user ID and timestamp to the data
            data['user'] = user_id
            data['timestamp'] = asyncio.get_event_loop().time()

            # Basic validation of expected chat message format
            if data.get('type') == 'chat' and 'message' in data:
                app_instance = user_data.get("app")
                if app_instance:
                    try:
                        # Publish the message to all clients in the room
                        formatted_message = json.dumps(data)
                        app_instance.publish(DEFAULT_ROOM, formatted_message, False)
                        logging.debug(f"广播来自 {user_id} 的聊天消息到房间 {DEFAULT_ROOM}")
                    except Exception as pub_err:
                        logging.error(f"广播消息时出错: {pub_err}")
                else:
                    logging.error(f"无法获取 app 实例来广播消息")
            else:
                logging.warning(f"收到来自 {user_id} 的未知或无效消息类型: {data.get('type')}")
                
        except json.JSONDecodeError:
            logging.error(f"无法解析来自 {user_id} 的 JSON 消息: {message_content}")
            try:
                error_msg = json.dumps({
                    "type": "error", 
                    "message": "无效的消息格式 (非 JSON 文本)",
                    "timestamp": asyncio.get_event_loop().time()
                })
                ws.send(error_msg, OpCode.TEXT)
            except Exception as send_err:
                logging.error(f"发送错误消息失败: {send_err}")
        except Exception as e:
            logging.error(f"处理消息时出错: {e}")


async def ws_close(ws, code, message):
    """(Async) Handles WebSocket connection closure."""
    user_data = ws.get_user_data()
    user_id = "未知用户"
    app_instance = None
    remote_address_str = decode_remote_address(ws)

    if user_data:
        user_id = user_data.get("user_id", "未知用户(获取失败)")
        app_instance = user_data.get("app")

    message_str = ""
    if message:
        try:
            if isinstance(message, bytes):
                message_str = message.decode('utf-8', errors='replace')
            elif isinstance(message, str):
                message_str = message
            else:
                message_str = f"(非预期类型: {type(message)})"
        except Exception:
            message_str = "(无法解码的消息)"

    logging.info(f"客户端断开连接: {remote_address_str} (ID: {user_id}), code: {code}, message: {message_str}")

    # Broadcast leave message if user_id is known and app_instance is available
    if user_id != "未知用户" and user_id != "未知用户(获取失败)" and app_instance:
        leave_message = json.dumps({
            "type": "user_leave",
            "user": user_id,
            "timestamp": asyncio.get_event_loop().time()
        })
        try:
            app_instance.publish(DEFAULT_ROOM, leave_message, False)
            logging.info(f"已广播用户 {user_id} 离开房间 {DEFAULT_ROOM} 的消息")
        except Exception as pub_err:
            logging.error(f"广播用户离开消息时出错: {pub_err}")


# --- HTTP Handler (Async) ---
async def home(res, req):
    """(Async) Handles HTTP GET requests for the root path '/'."""
    res.write_header('Content-Type', 'text/plain; charset=utf-8')
    res.end("安全聊天室后端正在运行 (使用 Poetry, 随机用户 ID, WSS/HTTPS)")


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
                logging.info(f"找到 SSL 证书和密钥文件，将使用 AppOptions 启用 HTTPS/WSS")
            except Exception as e:
                logging.error(f"创建 AppOptions 时出错: {e}")
                app_options = None
        else:
            logging.warning(f"SSL 证书或密钥文件路径无效或文件不存在，将以 HTTP/WS 模式运行")
    else:
        logging.warning("未配置 SSL 证书和密钥环境变量，将以 HTTP/WS 模式运行")

    # Create the Socketify App, passing AppOptions if available
    app = socketify.App(app_options)
    globals()['app'] = app

    # WebSocket configuration options with improved settings
    ws_options = {
        "compression": 0,  # Disable compression to avoid potential issues
        "max_payload_length": 16 * 1024,
        "idle_timeout": 120,  # Reduced from 300 for faster detection of dead connections
        "max_backpressure": 1024 * 1024,  # Set a reasonable backpressure limit
        "reset_idle_timeout_on_send": True,  # Reset timeout on send
        "upgrade": ws_upgrade,
        "open": ws_open,
        "message": ws_message,
        "close": ws_close,
        "ping": None,  # Let the library handle pings automatically
        "pong": None,  # Let the library handle pongs automatically
    }

    app.ws("/ws", ws_options)
    app.get("/", home)

    # Get host and port from environment variables or use defaults
    port = int(os.getenv('PORT', '8011'))
    host = os.getenv('HOST', '0.0.0.0')

    logging.info(f"服务器正在启动，监听地址 {host}:{port}...")

    # Define the callback for successful listening
    def on_listen(config):
        protocol = 'https' if app_options else 'http'
        ws_protocol = 'wss' if app_options else 'ws'
        display_host = host if host != '0.0.0.0' else '127.0.0.1'
        actual_port = config.port

        if host == '0.0.0.0':
            logging.info(f"服务器正在监听所有接口 (0.0.0.0) 上的端口 {actual_port}")
            logging.info(f"请使用你机器的实际 IP 地址或 'localhost'/'127.0.0.1' 访问")

        logging.info(
            f"成功启动! "
            f"访问 {protocol}://{display_host}:{actual_port} 或 "
            f"{ws_protocol}://{display_host}:{actual_port}/ws"
        )

    # Start listening
    app.listen(port, on_listen)

    # Run the application's event loop
    app.run()