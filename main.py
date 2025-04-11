# main.py
import socketify
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

async def ws_upgrade(res, req, socket_context):
    """ (异步) 处理 WebSocket 升级请求 """
    if 'app' not in globals():
         logging.error("App instance not found in global scope during upgrade")
         res.write_status(500).end("Internal Server Error")
         return
    app = globals()['app'] # 从全局作用域获取 app 实例

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
        return

    user_id = user_data.get("user_id") # 获取预先生成的 user_id

    # 在 open 时获取地址 (主要用于日志)
    try:
        remote_address = ws.get_remote_address()
    except Exception as e:
        logging.error(f"无法在 ws_open 中获取 remote_address: {e}")
        remote_address = ("未知地址", 0)

    logging.info(f"客户端连接成功 (升级后): {remote_address} (ID: {user_id})")

    ws.subscribe(DEFAULT_ROOM)
    logging.info(f"用户 {user_id} 加入房间: {DEFAULT_ROOM}")

    join_message = json.dumps({
        "type": "user_join",
        "user": user_id, # 使用获取到的 user_id
        "timestamp": asyncio.get_event_loop().time()
    })
    ws.publish(DEFAULT_ROOM, join_message)


async def ws_message(ws, message, opcode):
    """(异步) 处理收到的 WebSocket 消息"""
    user_data = ws.get_user_data() # 获取在 upgrade 时设置的数据
    if not user_data or "user_id" not in user_data:
        logging.error("User data (app/user_id) not found on WebSocket message")
        return

    user_id = user_data.get("user_id") # 获取预先生成的 user_id

    # 在 message 时获取地址 (主要用于日志)
    try:
        remote_address = ws.get_remote_address()
    except Exception as e:
        logging.error(f"无法在 ws_message 中获取 remote_address: {e}")
        remote_address = ("未知地址", 0)

    logging.debug(f"收到来自 {remote_address} ({user_id}) 的消息: {message}")

    try:
        data = json.loads(message) # message 已经是 str
        data['user'] = user_id # 使用获取到的 user_id
        data['timestamp'] = asyncio.get_event_loop().time()

        if data.get('type') == 'chat' and data.get('message'):
            ws.publish(DEFAULT_ROOM, json.dumps(data))
        else:
            logging.warning(f"收到来自 {remote_address} ({user_id}) 的未知或无效消息类型: {data.get('type')}")

    except json.JSONDecodeError:
        logging.error(f"无法解析来自 {remote_address} ({user_id}) 的 JSON 消息: {message}")
        try:
            error_msg = json.dumps({
                "type": "error",
                "message": "无效的消息格式",
                "timestamp": asyncio.get_event_loop().time()
            })
            ws.send(error_msg)
        except Exception as send_err:
             logging.error(f"向客户端 {remote_address} 发送错误消息失败: {send_err}")
    except Exception as e:
        logging.error(f"处理来自 {remote_address} ({user_id}) 的消息时出错: {e}")

async def ws_close(ws, code, message):
    """(异步) 处理 WebSocket 连接关闭"""
    user_data = ws.get_user_data() # 获取在 upgrade 时设置的数据
    if not user_data:
        # 尝试获取地址用于日志，即使没有 user_data
        try:
            remote_address = ws.get_remote_address()
        except Exception as e:
            remote_address = ("未知地址", 0)
        logging.warning(f"连接关闭时未找到用户数据 (app/user_id), code: {code}, message: {message}, address: {remote_address}")
        return

    user_id = user_data.get("user_id", "未知用户(获取失败)") # 获取预先生成的 user_id
    app = user_data.get("app")

    # 在 close 时获取地址 (主要用于日志)
    try:
        remote_address = ws.get_remote_address()
    except Exception as e:
        logging.error(f"无法在 ws_close 中获取 remote_address: {e}")
        remote_address = ("未知地址", 0)


    logging.info(f"客户端断开连接: {remote_address} (ID: {user_id}), code: {code}, message: {message}")

    leave_message = json.dumps({
        "type": "user_leave",
        "user": user_id, # 使用获取到的 user_id
        "timestamp": asyncio.get_event_loop().time()
    })

    if app:
        try:
            app.publish(DEFAULT_ROOM, leave_message)
            logging.info(f"已广播用户 {user_id} 离开房间 {DEFAULT_ROOM} 的消息")
        except Exception as pub_err:
            logging.error(f"尝试在 ws_close 中广播用户 {user_id} 离开消息时出错: {pub_err}")
    else:
        logging.error(f"无法在 ws_close 中获取 app 实例来广播用户 {user_id} 离开的消息")


# --- HTTP 处理函数 (异步) ---
async def home(res, req):
    """(异步) 处理根路径 '/' 的 HTTP GET 请求"""
    res.end("聊天室后端正在运行 (使用 Poetry 和随机用户 ID)")


# --- 主应用设置和启动 ---
if __name__ == "__main__":
    app = socketify.App()
    globals()['app'] = app

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

    # 确保从 .env 读取端口或使用默认值 3001 (如果 .env 没有设置)
    port = int(os.getenv('PORT', '3001'))
    logging.info(f"服务器正在启动，监听端口 {port}...")

    app.listen(
        port,
        lambda config: logging.info(f"成功启动! 访问 http://localhost:{config.port} 或 ws://localhost:{config.port}/ws"),
    )
    app.run()
