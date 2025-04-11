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
from dotenv import load_dotenv

# Load .env file at the start
load_dotenv()

# Configure logging
log_level_name = os.getenv('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(level=getattr(logging, log_level_name, logging.INFO),
                    format='%(asctime)s - %(levelname)s - %(message)s')

# Default room name
DEFAULT_ROOM = "general"

# Store client last activity timestamps for heartbeat monitoring
client_activity = {}
HEARTBEAT_INTERVAL = 30  # 检查客户端活动的间隔（秒）
HEARTBEAT_TIMEOUT = 70   # 客户端超时时间（秒）

# 创建一个全局变量，用于指示心跳检查器是否已启动
heartbeat_checker_started = False

# --- Helper Function ---
def generate_random_userid(length=4):
    """Generates a random alphanumeric ID of specified length."""
    characters = string.ascii_letters + string.digits
    return ''.join(random.choice(characters) for _ in range(length))

# --- Heartbeat Tracker ---
async def heartbeat_checker():
    """定期检查客户端活动并关闭超时的连接"""
    logging.info("心跳检查器已启动")
    while True:
        try:
            current_time = time.time()
            inactive_clients = []
            
            # Find inactive clients
            for ws, last_active in list(client_activity.items()):
                if current_time - last_active > HEARTBEAT_TIMEOUT:
                    inactive_clients.append(ws)
            
            # Close inactive connections
            for ws in inactive_clients:
                try:
                    user_data = ws.get_user_data()
                    user_id = user_data.get("user_id", "未知用户") if user_data else "未知用户"
                    logging.warning(f"关闭不活跃的连接: {user_id} (超过 {HEARTBEAT_TIMEOUT}秒无活动)")
                    
                    # Send a warning before closing
                    try:
                        warning_msg = json.dumps({
                            "type": "system",
                            "message": "由于长时间不活动，您的连接即将关闭",
                            "timestamp": time.time()
                        })
                        ws.send(warning_msg, OpCode.TEXT)
                    except Exception:
                        pass  # Ignore if we can't send the warning
                    
                    # Remove from activity tracker and close the connection
                    if ws in client_activity:
                        del client_activity[ws]
                    ws.end(1000, "不活跃连接超时")
                except Exception as e:
                    logging.error(f"关闭不活跃连接时出错: {e}")
            
            # Wait for next check
            await asyncio.sleep(HEARTBEAT_INTERVAL)
        except Exception as e:
            logging.error(f"心跳检查器出错: {e}")
            await asyncio.sleep(HEARTBEAT_INTERVAL)

# 启动心跳检查器的函数
def start_heartbeat_checker():
    global heartbeat_checker_started
    
    if heartbeat_checker_started:
        logging.info("心跳检查器已经在运行中")
        return
    
    # 创建一个新的事件循环用于心跳检查
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # 将心跳检查器作为一个任务启动
    loop.create_task(heartbeat_checker())
    
    # 在一个单独的线程中运行事件循环
    import threading
    def run_event_loop():
        try:
            loop.run_forever()
        except Exception as e:
            logging.error(f"心跳检查器事件循环出错: {e}")
        finally:
            loop.close()
    
    heartbeat_thread = threading.Thread(target=run_event_loop, daemon=True)
    heartbeat_thread.start()
    heartbeat_checker_started = True
    logging.info("心跳检查器线程已启动")

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

    # 初始化客户端活动时间
    client_activity[ws] = time.time()

    logging.info(f"客户端连接成功 (升级后): {remote_address_str} (ID: {user_id})")

    ws.subscribe(DEFAULT_ROOM)
    logging.info(f"用户 {user_id} 加入房间: {DEFAULT_ROOM}")

    # Send a welcome message to just this user
    try:
        welcome_message = json.dumps({
            "type": "system",
            "message": f"欢迎加入聊天室，您的 ID 是: {user_id}",
            "timestamp": time.time()
        })
        ws.send(welcome_message, OpCode.TEXT)
        logging.debug(f"已发送欢迎消息给用户 {user_id}")
    except Exception as send_err:
        logging.error(f"发送欢迎消息给用户 {user_id} 时出错: {send_err}")

    # Broadcast join message to room
    try:
        join_message = json.dumps({
            "type": "user_join",
            "user": user_id,
            "timestamp": time.time()
        })
        app_instance = user_data.get("app")
        if app_instance:
            app_instance.publish(DEFAULT_ROOM, join_message, False)
            logging.debug(f"已广播用户 {user_id} 加入房间 {DEFAULT_ROOM} 的消息")
    except Exception as pub_err:
        logging.error(f"广播用户加入消息时出错: {pub_err}")


async def ws_message(ws, message, opcode):
    """(Async) Handles received WebSocket messages."""
    # 更新客户端活动时间
    client_activity[ws] = time.time()
    
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
        try:
            # Try to handle binary message as UTF-8 text (for browser compatibility)
            if isinstance(message, bytes):
                message_content = message.decode('utf-8', errors='replace')
                is_text = True
                logging.debug(f"已将二进制消息解码为文本: {message_content}")
            else:
                logging.error(f"二进制消息类型不支持: {type(message)}")
                return
        except Exception as e:
            logging.error(f"解析二进制消息时出错: {e}")
            return
    else:
        # Handle control frames
        if opcode == OpCode.PING:
            logging.debug(f"收到来自 {user_id} 的 PING")
            # Automatically respond to ping with pong
            try:
                ws.send("", OpCode.PONG)
            except Exception as e:
                logging.error(f"发送 PONG 响应时出错: {e}")
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
            
            # 处理 ping 心跳消息
            if data.get('type') == 'ping':
                logging.debug(f"收到来自 {user_id} 的 ping 心跳")
                try:
                    # 发送 pong 响应
                    pong_response = json.dumps({
                        "type": "pong",
                        "timestamp": time.time()
                    })
                    ws.send(pong_response, OpCode.TEXT)
                    return
                except Exception as e:
                    logging.error(f"发送 pong 响应时出错: {e}")
                    return
            
            # Add user ID and timestamp to the data (for normal messages)
            data['user'] = user_id
            if 'timestamp' not in data:
                data['timestamp'] = time.time()

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
                    "timestamp": time.time()
                })
                ws.send(error_msg, OpCode.TEXT)
            except Exception as send_err:
                logging.error(f"发送错误消息失败: {send_err}")
        except Exception as e:
            logging.error(f"处理消息时出错: {e}")


async def ws_close(ws, code, message):
    """(Async) Handles WebSocket connection closure."""
    # 移除客户端活动记录
    if ws in client_activity:
        del client_activity[ws]
        
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
            "timestamp": time.time()
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
        "compression": 0,  # 禁用压缩以避免潜在问题
        "max_payload_length": 16 * 1024,
        "idle_timeout": 120,  # 降低超时时间以更快检测死连接
        "max_backpressure": 1024 * 1024,  # 设置合理的背压限制
        "reset_idle_timeout_on_send": True,  # 发送时重置超时
        "upgrade": ws_upgrade,
        "open": ws_open,
        "message": ws_message,
        "close": ws_close,
        "ping": None,  # 让库自动处理 ping
        "pong": None,  # 让库自动处理 pong
    }

    app.ws("/ws", ws_options)
    app.get("/", home)

    # 启动心跳检查器 (以线程方式运行，而不是使用 asyncio.create_task)
    start_heartbeat_checker()

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