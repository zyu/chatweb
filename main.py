import socketify
# *** 修改点：导入 AppOptions ***
from socketify import AppOptions
import asyncio
import json
import logging
import os
import random # 导入 random 模块
import string # 导入 string 模块
from dotenv import load_dotenv

# 在程序开始时加载 .env 文件
load_dotenv()

# 配置日志记录
log_level_name = os.getenv('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(level=getattr(logging, log_level_name, logging.INFO),
                    format='%(asctime)s - %(levelname)s - %(message)s')

# 默认房间名
DEFAULT_ROOM = "general"

# --- Helper Function ---
def generate_random_userid(length=4):
    """生成指定长度的随机字母数字组合 ID"""
    characters = string.ascii_letters + string.digits
    return ''.join(random.choice(characters) for i in range(length))

# --- WebSocket 事件处理函数 (异步) ---
# ws_upgrade, ws_open, ws_message, ws_close 函数保持不变
async def ws_upgrade(res, req, socket_context):
    """ (异步) 处理 WebSocket 升级请求 """
    if 'app' not in globals():
         logging.error("App instance not found in global scope during upgrade")
         res.write_status(500).end("Internal Server Error")
         return
    # 使用 try-except 获取全局变量，增加健壮性
    try:
        app = globals()['app'] # 从全局作用域获取 app 实例
    except KeyError:
        logging.error("App instance not found in global scope during upgrade (KeyError)")
        res.write_status(500).end("Internal Server Error")
        return


    key = req.get_header("sec-websocket-key")
    protocol = req.get_header("sec-websocket-protocol")
    extensions = req.get_header("sec-websocket-extensions")

    # 在升级时生成随机 user_id
    user_id = generate_random_userid(4)
    logging.info(f"为新连接生成 User ID: {user_id}")

    # 将 app 实例和生成的 user_id 放入 user_data
    user_data = {
        "app": app,
        "user_id": user_id
    }

    # 执行升级，并将 user_data 传递给连接
    res.upgrade(key, protocol, extensions, socket_context, user_data)

async def ws_open(ws):
    """(异步) 处理新的 WebSocket 连接 (在 upgrade 成功后调用)"""
    user_data = ws.get_user_data() # 获取在 upgrade 时设置的数据
    if not user_data or "user_id" not in user_data:
        logging.error("User data (app/user_id) not found on WebSocket open")
        # 考虑是否关闭连接
        # ws.close()
        return

    user_id = user_data.get("user_id") # 获取预先生成的 user_id

    # 在 open 时获取地址 (主要用于日志)
    try:
        remote_address = ws.get_remote_address_bytes() # Use bytes version for potentially better compatibility
        # Decode address bytes to string, handling potential errors
        try:
            ip_str = remote_address[0].decode('utf-8', errors='replace')
            port_int = remote_address[1]
            remote_address_decoded = (ip_str, port_int)
        except Exception as decode_err:
            logging.warning(f"Could not decode remote address bytes: {remote_address}, error: {decode_err}")
            remote_address_decoded = ("未知地址(解码失败)", 0)

    except Exception as e:
        logging.error(f"无法在 ws_open 中获取 remote_address: {e}")
        remote_address_decoded = ("未知地址", 0)


    logging.info(f"客户端连接成功 (升级后): {remote_address_decoded} (ID: {user_id})")

    ws.subscribe(DEFAULT_ROOM)
    logging.info(f"用户 {user_id} 加入房间: {DEFAULT_ROOM}")

    join_message = json.dumps({
        "type": "user_join",
        "user": user_id, # 使用获取到的 user_id
        "timestamp": asyncio.get_event_loop().time()
    })
    # Publish to default room only, excluding sender
    try:
      ws.publish(DEFAULT_ROOM, join_message, False) # False indicates not to publish to sender
    except Exception as pub_err:
      logging.error(f"Error publishing join message for user {user_id}: {pub_err}")


async def ws_message(ws, message, opcode):
    """(异步) 处理收到的 WebSocket 消息"""
    user_data = ws.get_user_data() # 获取在 upgrade 时设置的数据
    if not user_data or "user_id" not in user_data:
        logging.error("User data (app/user_id) not found on WebSocket message")
        return

    user_id = user_data.get("user_id") # 获取预先生成的 user_id

    # 在 message 时获取地址 (主要用于日志)
    try:
        remote_address = ws.get_remote_address_bytes() # Use bytes version
        try:
            ip_str = remote_address[0].decode('utf-8', errors='replace')
            port_int = remote_address[1]
            remote_address_decoded = (ip_str, port_int)
        except Exception as decode_err:
            logging.warning(f"Could not decode remote address bytes: {remote_address}, error: {decode_err}")
            remote_address_decoded = ("未知地址(解码失败)", 0)
    except Exception as e:
        logging.error(f"无法在 ws_message 中获取 remote_address: {e}")
        remote_address_decoded = ("未知地址", 0)


    # Assuming message is bytes, decode it
    try:
        message_str = message.decode('utf-8')
        logging.debug(f"收到来自 {remote_address_decoded} ({user_id}) 的消息: {message_str}")
    except UnicodeDecodeError:
        logging.error(f"无法将来自 {remote_address_decoded} ({user_id}) 的消息解码为 UTF-8: {message!r}") # Log raw bytes representation
        # Optionally send an error back if needed, ensure message is bytes
        try:
            error_msg = json.dumps({
                "type": "error",
                "message": "无效的消息编码 (非 UTF-8)",
                "timestamp": asyncio.get_event_loop().time()
            }).encode('utf-8') # Encode error message to bytes
            ws.send(error_msg, opcode) # Send with original opcode if relevant, or default
        except Exception as send_err:
             logging.error(f"向客户端 {remote_address_decoded} 发送编码错误消息失败: {send_err}")
        return # Stop processing if decode fails

    try:
        data = json.loads(message_str) # Parse decoded string
        data['user'] = user_id # 使用获取到的 user_id
        data['timestamp'] = asyncio.get_event_loop().time()

        if data.get('type') == 'chat' and data.get('message'):
             # Publish to default room only, excluding sender
             try:
                 ws.publish(DEFAULT_ROOM, json.dumps(data), False) # False indicates not to publish to sender
             except Exception as pub_err:
                 logging.error(f"Error publishing chat message from user {user_id}: {pub_err}")
        else:
            logging.warning(f"收到来自 {remote_address_decoded} ({user_id}) 的未知或无效消息类型: {data.get('type')}")

    except json.JSONDecodeError:
        logging.error(f"无法解析来自 {remote_address_decoded} ({user_id}) 的 JSON 消息: {message_str}")
        try:
            error_msg = json.dumps({
                "type": "error",
                "message": "无效的消息格式 (非 JSON)",
                "timestamp": asyncio.get_event_loop().time()
            }).encode('utf-8') # Encode error message to bytes
            ws.send(error_msg, opcode)
        except Exception as send_err:
             logging.error(f"向客户端 {remote_address_decoded} 发送 JSON 错误消息失败: {send_err}")
    except Exception as e:
        logging.error(f"处理来自 {remote_address_decoded} ({user_id}) 的消息时出错: {e}")


async def ws_close(ws, code, message):
    """(异步) 处理 WebSocket 连接关闭"""
    user_data = ws.get_user_data() # 获取在 upgrade 时设置的数据
    user_id = "未知用户"
    app = None

    # 在 close 时获取地址 (主要用于日志)
    try:
        remote_address = ws.get_remote_address_bytes() # Use bytes version
        try:
            ip_str = remote_address[0].decode('utf-8', errors='replace')
            port_int = remote_address[1]
            remote_address_decoded = (ip_str, port_int)
        except Exception as decode_err:
             logging.warning(f"Could not decode remote address bytes on close: {remote_address}, error: {decode_err}")
             remote_address_decoded = ("未知地址(解码失败)", 0)
    except Exception as e:
        logging.error(f"无法在 ws_close 中获取 remote_address: {e}")
        remote_address_decoded = ("未知地址", 0)


    if user_data:
        user_id = user_data.get("user_id", "未知用户(获取失败)") # 获取预先生成的 user_id
        app = user_data.get("app")
    else:
        logging.warning(f"连接关闭时未找到用户数据 (app/user_id), code: {code}, message: {message}, address: {remote_address_decoded}")


    # Decode message bytes if they exist
    message_str = ""
    if message:
        try:
            message_str = message.decode('utf-8', errors='replace')
        except Exception as decode_err:
            logging.warning(f"Could not decode close message bytes: {message!r}, error: {decode_err}")
            message_str = "(无法解码的消息)"


    logging.info(f"客户端断开连接: {remote_address_decoded} (ID: {user_id}), code: {code}, message: {message_str}")

    leave_message = json.dumps({
        "type": "user_leave",
        "user": user_id, # 使用获取到的 user_id
        "timestamp": asyncio.get_event_loop().time()
    })

    if app:
        try:
            # Publish to default room only, excluding sender
            app.publish(DEFAULT_ROOM, leave_message, False) # False indicates not to publish to sender
            logging.info(f"已广播用户 {user_id} 离开房间 {DEFAULT_ROOM} 的消息")
        except Exception as pub_err:
            logging.error(f"尝试在 ws_close 中广播用户 {user_id} 离开消息时出错: {pub_err}")
    else:
        logging.error(f"无法在 ws_close 中获取 app 实例来广播用户 {user_id} 离开的消息")


# --- HTTP 处理函数 (异步) ---
async def home(res, req):
    """(异步) 处理根路径 '/' 的 HTTP GET 请求"""
    res.end("安全聊天室后端正在运行 (使用 Poetry, 随机用户 ID, WSS/HTTPS)")


# --- 主应用设置和启动 ---
if __name__ == "__main__":

    # --- SSL/TLS 配置 ---
    # 从环境变量读取证书和密钥文件路径
    # 你需要确保这些文件存在并且路径正确
    ssl_certfile = os.getenv('SSL_CERTFILE', 'cert.pem') # 添加默认值
    ssl_keyfile = os.getenv('SSL_KEYFILE', 'key.pem') # 添加默认值
    ssl_passphrase = os.getenv('SSL_PASSPHRASE') # 读取密码（可能为 None）
    app_options = None # 用于存放 AppOptions 实例

    if ssl_certfile and ssl_keyfile:
        if os.path.exists(ssl_certfile) and os.path.exists(ssl_keyfile):
            # *** 修改点：创建 AppOptions 实例 ***
            try:
                app_options = AppOptions(
                    key_file_name=ssl_keyfile,
                    cert_file_name=ssl_certfile,
                    passphrase=ssl_passphrase # 如果没有密码，传递 None 也可以
                )
                logging.info(f"找到 SSL 证书和密钥文件，将使用 AppOptions 启用 HTTPS/WSS。 Cert: {ssl_certfile}, Key: {ssl_keyfile}")
            except Exception as e:
                 logging.error(f"创建 AppOptions 时出错: {e}")
                 app_options = None # 创建失败则不使用 SSL

        else:
            logging.warning(f"SSL 证书或密钥文件路径无效或文件不存在 ({ssl_certfile}, {ssl_keyfile})，将以 HTTP/WS 模式运行。")
    else:
        logging.warning("未配置 SSL_CERTFILE 和 SSL_KEYFILE 环境变量 (或值为空)，将以 HTTP/WS 模式运行。")

    # *** 修改点：在创建 App 时传入 AppOptions 实例 (如果存在) ***
    app = socketify.App(app_options) # 如果 app_options 为 None, 则不启用 SSL
    if app_options:
        logging.info("Socketify App 初始化时传入了 AppOptions。")
    else:
        logging.info("Socketify App 初始化时未传入 AppOptions (HTTP/WS 模式)。")


    globals()['app'] = app # Store app instance globally for access in handlers

    ws_options = {
        "idle_timeout": 300,
        "max_payload_length": 16 * 1024,
        "upgrade": ws_upgrade,
        "open": ws_open,
        "message": ws_message,
        "close": ws_close,
    }

    app.ws("/ws", ws_options)
    app.get("/", home)

    # 确保从 .env 读取端口或使用默认值
    port = int(os.getenv('PORT', '8011')) # 使用 8011 作为示例，你可以更改
    host = os.getenv('HOST', '0.0.0.0') # 监听所有接口

    logging.info(f"服务器正在启动，监听地址 {host}:{port}...")

    # 从 listen() 中移除 ssl 参数 (保持不变)
    app.listen(
        port,
        lambda config: logging.info(
            f"成功启动! "
            # 使用 app_options 判断是否启用了 SSL
            f"访问 {'https' if app_options else 'http'}://{host}:{config.port} 或 "
            f"{'wss' if app_options else 'ws'}://{host}:{config.port}/ws"
        )
    )

    app.run()