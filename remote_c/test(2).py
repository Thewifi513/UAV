import socket
import time
import threading
from threading import Thread, Lock
from collections import deque
import websockets
import asyncio
import ssl
import json
import os
from datetime import datetime
import sys
from OpenSSL import crypto

# 全局键盘模块引用（仅在启用键盘时设置）
global_keyboard_module = None

# ==============================================================
# 核心代码部分 - 连接远程无人机地面站及本地前端
# ==============================================================

class ReliableController:
    def __init__(self, enable_keyboard=False):
        # 网络配置
        self.ground_station_ip = '55.55.100.250'  # 地面站命名空间IP
        self.ground_station_port = 5000
        self.websocket_port = 8765
        self.ssl_cert = 'server.crt'
        self.ssl_key = 'server.key'
        
        # 连接状态
        self.sock = None
        self.event_queue = deque()
        self.lock = Lock()
        self.running = True
        self.reconnect_count = 0
        self.connection_lock = threading.Lock()
        
        # WebSocket管理
        self.ws_clients = set()
        self.ws_lock = Lock()
        self.auth_token = "SECURE_TOKEN_123"  # 身份验证令牌
        self.expect_secure = bool(self.ssl_cert)
        
        # 键盘监听
        self.enable_keyboard = enable_keyboard  # 键盘输入开关
        self.listener = None
        self.current_keys = set()
        
        # 日志系统
        self.log_file = "controller_log.txt"
        self._log("控制器初始化")
        self._log(f"操作系统: {sys.platform}")
        self._log(f"键盘输入: {'启用' if self.enable_keyboard else '禁用'}")
        
        # 初始化连接
        self._connect()

        #初始化油门状态
        self.throttle_adjust_lock = Lock()  # 新增油门调整锁
        self.throttle_adjust_thread = None  # 油门调整线程
        self.current_throttle = 0.05  # 初始油门值0.05
        
        # 确保SSL证书存在
        if not os.path.exists(self.ssl_cert) or not os.path.exists(self.ssl_key):
            self._log("警告: SSL证书缺失，将使用非加密连接")
            self.ssl_cert = None
            self.ssl_key = None

    def _log(self, message):
        """记录日志"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{timestamp}] {message}"
        print(log_entry)
        
        # 写入日志文件
        with open(self.log_file, "a") as f:
            f.write(log_entry + "\n")

    def _connect(self):
        """建立带重试的连接"""
        with self.connection_lock:
            while self.running and self.reconnect_count < 5:
                try:
                    # 关闭旧连接（如果存在）
                    if self.sock:
                        try:
                            self.sock.close()
                        except:
                            pass
                    
                    # 创建新连接
                    self.sock = socket.socket()
                    self.sock.settimeout(3.0)
                    self.sock.connect((self.ground_station_ip, self.ground_station_port))
                    self._log(f"成功连接地面站: {self.ground_station_ip}:{self.ground_station_port}")
                    self.reconnect_count = 0
                    return True
                except Exception as e:
                    error_msg = f"连接失败: {str(e)}，5秒后重试... (尝试 {self.reconnect_count+1}/5)"
                    self._log(error_msg)
                    self.reconnect_count += 1
                    time.sleep(5)
            
            self.running = False
            self._log("连接失败次数过多，停止尝试")
            return False

    def _send_with_retry(self, event):
        """带重试机制的发送"""
        retries = 3
        while retries > 0 and self.running:
            try:
                # 检查连接状态
                if self.sock is None:
                    if not self._connect():
                        return False
                
                # 发送数据
                self.sock.sendall(f"{event}\n".encode())
                return True
            except (BrokenPipeError, ConnectionResetError) as e:
                self._log(f"连接错误: {str(e)}，尝试重新连接...")
                self._connect()
                retries -= 1
                time.sleep(0.5)
            except socket.timeout:
                self._log("发送超时，重试中...")
                retries -= 1
                time.sleep(0.2)
            except Exception as e:
                self._log(f"未知发送错误: {str(e)}")
                retries -= 1
                time.sleep(0.5)
        return False

    def _broadcast_to_ws(self, event):
        """线程安全的WebSocket广播"""
        with self.ws_lock:
            clients = list(self.ws_clients)
        
        if not clients:
            return
            
        # 创建线程安全广播协程
        async def async_broadcast():
            for client in clients:
                try:
                    await client.send(event)
                except Exception as e:
                    self._log(f"WebSocket广播错误: {str(e)}")
        
        # 在新线程中运行事件循环
        def run_broadcast():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(async_broadcast())
            loop.close()
        
        Thread(target=run_broadcast, daemon=True).start()

    def _throttle_adjuster(self, target_throttle):
        """单独的油门调整线程"""
        try:
            # 计算需要发送的按键次数
            steps = int((target_throttle - self.current_throttle) / 0.05)
            direction = "k" if steps > 0 else "j"
            abs_steps = abs(steps)
            
            if abs_steps > 0:
                #self._log(f"开始油门调整: {abs_steps} 次 {direction} 按键")
                
                # 发送按键事件序列（带间隔）
                for i in range(abs_steps):
                    if not self.running:
                        break
                    
                    # 发送按键按下事件
                    press_cmd = f"press:{direction}"
                    if self._send_with_retry(press_cmd):
                        # 更新当前油门状态（每步增加0.05）
                        with self.throttle_adjust_lock:
                            adjustment = 0.05 if direction == "k" else -0.05
                            self.current_throttle = max(0.0, min(1.0, self.current_throttle + adjustment))
                            
                        # 广播到前端
                        self._broadcast_to_ws(json.dumps({
                            "type": "throttle_status",
                            "throttle": self.current_throttle,
                            "timestamp": time.time()
                        }))
                    
                    # 添加短暂延迟防止阻塞
                    time.sleep(0.05)  # 50ms间隔
                
                # 发送按键释放事件
                if self.running:
                    release_cmd = f"release:{direction}"
                    self._send_with_retry(release_cmd)
                    
                self._log(f"油门调整完成: 当前 {self.current_throttle:.2f}")
        except Exception as e:
            self._log(f"油门调整线程错误: {str(e)}")

    def _event_handler(self):
        """事件处理线程（核心逻辑）"""
        # 启动心跳线程
        Thread(target=self._heartbeat, daemon=True).start()
        
        while self.running:
            if not self.event_queue:
                time.sleep(0.001)
                continue

            # 优先处理释放事件（防止刹车失效）
            with self.lock:
                # 复制队列内容避免长时间锁定
                all_events = list(self.event_queue)
                release_events = [e for e in all_events if ":release" in e]
                normal_events = [e for e in all_events if ":release" not in e]
                events_to_process = release_events + normal_events

            # 处理事件
            for event in events_to_process:
                # 处理前端发送的油门命令
                if event.startswith("throttle:"):
                    try:
                        target_throttle = float(event.split(":")[1])
                        # 确保油门值在0-1范围内
                        if 0 <= target_throttle <= 1:
                            # 四舍五入到0.05的倍数
                            target_throttle = round(target_throttle * 20) / 20.0
                            
                            # 启动单独的油门调整线程
                            if self.throttle_adjust_thread and self.throttle_adjust_thread.is_alive():
                                self.throttle_adjust_thread.join(timeout=0.1)
                                
                            self.throttle_adjust_thread = Thread(
                                target=self._throttle_adjuster,
                                args=(target_throttle,),
                                daemon=True
                            )
                            self.throttle_adjust_thread.start()
                        else:
                            self._log(f"无效的油门值: {target_throttle}")
                    except ValueError:
                        self._log(f"无效的油门格式: {event}")
                    finally:
                        # 从队列中移除油门事件
                        with self.lock:
                            try:
                                self.event_queue.remove(event)
                            except ValueError:
                                pass
                        continue
                
                # 处理J/K按键事件（本地键盘输入）- 保持原样
                if event in ["press:j", "press:k"]:
                    try:
                        # 计算油门调整值
                        adjustment = -0.05 if event == "press:j" else 0.05
                        new_throttle = self.current_throttle + adjustment
                        
                        # 限制在0-1范围内
                        new_throttle = max(0.0, min(1.0, new_throttle))
                        
                        # 四舍五入到0.05的倍数
                        new_throttle = round(new_throttle * 20) / 20.0
                        
                        # 更新油门状态
                        self.current_throttle = new_throttle
                        self._log(f"按键调整油门: {event} -> {new_throttle:.2f}")
                        
                        # 发送按键命令（直接转发给地面站）
                        if self._send_with_retry(event):
                            # 广播到前端
                            self._broadcast_to_ws(json.dumps({
                                "type": "throttle_status",
                                "throttle": new_throttle,
                                "timestamp": time.time()
                            }))
                    except Exception as e:
                        self._log(f"油门按键处理错误: {str(e)}")
                    finally:
                        # 从队列中移除事件
                        with self.lock:
                            try:
                                self.event_queue.remove(event)
                            except ValueError:
                                pass
                        continue
                
                # 处理其他命令
                if self._send_with_retry(event):
                    # 广播到WebSocket客户端
                    self._broadcast_to_ws(json.dumps({
                        "type": "event",
                        "event": event,
                        "timestamp": time.time()
                    }))
                    
                    # 从队列中移除
                    with self.lock:
                        try:
                            self.event_queue.remove(event)
                        except ValueError:
                            pass
                else:
                    self._log(f"丢弃未送达事件: {event}")
                    with self.lock:
                        try:
                            self.event_queue.remove(event)
                        except ValueError:
                            pass
                
                time.sleep(0.005)  # 控制发送频率

    def _heartbeat(self):
        """心跳机制保持连接活跃"""
        while self.running:
            try:
                if self.sock:
                    # 每5秒发送一次心跳
                    self._send_with_retry("heartbeat:ping")
                    self._broadcast_to_ws(json.dumps({
                        "type": "heartbeat",
                        "status": "active",
                        "timestamp": time.time()
                    }))
                time.sleep(5)
            except:
                pass

    # ==============================================================
    # 键盘控制部分 - 根据开关状态决定是否启用
    # ==============================================================
    
    def _on_key_press(self, key):
        """处理按键按下事件 - 仅当键盘输入启用时有效"""
        if not self.enable_keyboard:
            return
            
        try:
            # 处理特殊键
            if key == global_keyboard_module.Key.esc:  # 使用全局引用
                self.running = False
                return False
            
            # 获取按键字符表示
            char = key.char if hasattr(key, 'char') else str(key).split('.')[-1]
            
            # 统一转换为小写
            if char:
                char = char.lower()
                with self.lock:
                    if f"press:{char}" not in self.event_queue:
                        self.event_queue.append(f"press:{char}")
        except Exception as e:
            self._log(f"按键处理错误: {str(e)}")

    def _on_key_release(self, key):
        """处理按键释放事件 - 仅当键盘输入启用时有效"""
        if not self.enable_keyboard:
            return
            
        try:
            # 获取按键字符表示
            char = key.char if hasattr(key, 'char') else str(key).split('.')[-1]
            
            # 统一转换为小写
            if char:
                char = char.lower()
                with self.lock:
                    self.event_queue.append(f"release:{char}")
        except Exception as e:
            self._log(f"按键释放处理错误: {str(e)}")

    def _start_key_listener(self):
        """启动键盘监听器 - 仅当键盘输入启用时有效"""
        if not self.enable_keyboard:
            self._log("键盘输入已禁用，跳过键盘监听器启动")
            return
            
        self._log("控制就绪 | 方向：WASD | 转向：Q/E | 升降：Z/X | 起飞：C | 降落：L | 退出：ESC")
        
        # 创建键盘监听器
        self.listener = global_keyboard_module.Listener(  # 使用全局引用
            on_press=self._on_key_press,
            on_release=self._on_key_release
        )
        
        # 启动监听器
        self.listener.start()
        self._log("键盘监听器已启动")

    # ==============================================================
    # WebSocket服务器部分
    # ==============================================================
    
    async def _ws_handler(self, websocket, path):
        """处理WebSocket连接 - 支持混合消息格式"""
        self._log(f"收到WebSocket连接请求: {websocket.remote_address}")
        self._log(f"WebSocket请求头: {websocket.request_headers}")
        
        # 协议一致性检查
        if websocket.secure != self.expect_secure:
            await websocket.close(code=4003, reason=f"Protocol mismatch (expect {'wss' if self.expect_secure else 'ws'})")
            return
        
        # 身份验证
        try:
            token = await websocket.recv()
            if token != self.auth_token:
                self._log(f"无效的WebSocket令牌: {token}")
                await websocket.close(code=4003, reason="Invalid token")
                return
        except websockets.exceptions.ConnectionClosed:
            return
        
        # 添加客户端
        with self.ws_lock:
            self.ws_clients.add(websocket)
        self._log(f"Web客户端连接: {websocket.remote_address}")
        
        try:
            # 发送完整初始状态
            status = {
                "type": "status",
                "connected": self.sock is not None,
                "queue_size": len(self.event_queue),
                "clients": len(self.ws_clients),
                "timestamp": time.time(),
                "protocol": "wss" if websocket.secure else "ws",
                "keyboard_enabled": self.enable_keyboard  # 添加键盘状态
            }
            await websocket.send(json.dumps(status))
            
            # 增强的消息处理 - 支持混合格式
            async for message in websocket:
                try:
                    # 尝试解析JSON消息
                    try:
                        data = json.loads(message)
                        if data.get("type") == "get_throttle":
                            response = json.dumps({
                                "type": "throttle_status",
                                "throttle": self.current_throttle,
                                "timestamp": time.time()
                            })
                            await websocket.send(response)
                            continue
                        if data.get("type") == "command":
                            command = data.get("command", "")
                            self._log(f"收到JSON命令: {command}")
                            with self.lock:
                                self.event_queue.append(command)
                        else:
                            # 其他JSON类型消息
                            self._log(f"收到JSON消息: {message}")
                            with self.lock:
                                self.event_queue.append(message)
                    except json.JSONDecodeError:
                        # 处理纯文本命令
                        self._log(f"收到纯文本命令: {message}")
                        with self.lock:
                            self.event_queue.append(message)
                except Exception as e:
                    self._log(f"处理消息错误: {str(e)}")
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            # 移除客户端
            with self.ws_lock:
                if websocket in self.ws_clients:
                    self.ws_clients.remove(websocket)
            self._log(f"Web客户端断开: {websocket.remote_address}")

    async def _ws_server(self):
        """启动并保持WebSocket服务器运行"""
        # 记录启动信息
        self._log(f"启动WebSocket服务: 端口={self.websocket_port}")
        
        # 配置SSL
        ssl_context = None
        if os.path.exists(self.ssl_cert) and os.path.exists(self.ssl_key):
            try:
                ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ssl_context.load_cert_chain(self.ssl_cert, self.ssl_key)
                self._log("SSL证书加载成功")
            except Exception as e:
                self._log(f"SSL加载失败: {str(e)}")
        
        # 创建服务器实例
        try:
            # 创建WebSocket服务器
            server = await websockets.serve(
                self._ws_handler,
                "0.0.0.0",  # 绑定到所有接口
                self.websocket_port,
                ssl=ssl_context,
                ping_interval=20,
                ping_timeout=30
            )
            
            # 记录成功启动
            protocol = "wss" if ssl_context else "ws"
            self._log(f"WebSocket服务器已启动: {protocol}://0.0.0.0:{self.websocket_port}")
            
            # 保持服务器运行直到关闭
            await server.wait_closed()
            self._log("WebSocket服务器已关闭")
            
        except OSError as e:
            # 处理端口占用等错误
            self._log(f"无法启动WebSocket服务器: {str(e)}")
            if "Address already in use" in str(e):
                self._log("错误: 端口已被占用")
        except Exception as e:
            # 处理其他异常
            self._log(f"WebSocket服务器意外错误: {str(e)}")
            raise

    def start(self):
        """启动所有线程"""
        # 启动WebSocket服务器线程
        def run_ws_server():
            try:
                # 创建新的事件循环
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                # 记录线程启动
                self._log("WebSocket服务器线程启动")
                
                # 运行服务器
                loop.run_until_complete(self._ws_server())
                
            except Exception as e:
                # 记录未捕获的异常
                self._log(f"WebSocket服务器线程崩溃: {str(e)}")
            finally:
                # 清理事件循环
                loop.close()
                self._log("WebSocket服务器线程退出")
        
        # 在后台线程中启动WebSocket服务器
        Thread(target=run_ws_server, daemon=True).start()
        
        # 启动键盘监听器（如果启用）
        if self.enable_keyboard:
            self._start_key_listener()
            Thread(target=self._event_handler, daemon=True).start()
        else:
            # 即使键盘禁用，也需要启动事件处理器处理WebSocket事件
            Thread(target=self._event_handler, daemon=True).start()
            self._log("键盘输入已禁用，仅处理WebSocket事件")

    def shutdown(self):
        """安全关闭控制器"""
        self.running = False
        try:
            if self.sock:
                self.sock.close()
            if self.enable_keyboard and self.listener:  # 仅当键盘启用时才停止监听器
                self.listener.stop()
        except:
            pass
        self._log("控制器安全关闭")

def generate_self_signed_cert():
    # 生成密钥对
    key = crypto.PKey()
    key.generate_key(crypto.TYPE_RSA, 2048)

    # 创建证书
    cert = crypto.X509()
    cert.get_subject().CN = "localhost"  # 主域名
    cert.set_serial_number(1000)
    cert.gmtime_adj_notBefore(0)
    cert.gmtime_adj_notAfter(365*24*60*60)  # 1年有效期
    cert.set_issuer(cert.get_subject())  # 自签名
    cert.set_pubkey(key)

    # 添加所有可能的访问地址到 SAN
    san_list = [
        "DNS:localhost",          # 支持 localhost
        "IP:55.55.102.2",         # 支持 IP 访问
        "DNS:127.0.0.1",          # 支持 127.0.0.1
        "DNS:yourapp.local"       # 可选：自定义域名（需配 hosts 文件）
    ]
    cert.add_extensions([
        crypto.X509Extension(
            b"subjectAltName",
            False,
            ", ".join(san_list).encode()  # 合并所有地址
        )
    ])

    # 签名并保存
    cert.sign(key, "sha256")
    with open("server.crt", "wb") as f:
        f.write(crypto.dump_certificate(crypto.FILETYPE_PEM, cert))
    with open("server.key", "wb") as f:
        f.write(crypto.dump_privatekey(crypto.FILETYPE_PEM, key))

if __name__ == "__main__":
    # 添加命令行参数控制键盘输入
    import argparse
    parser = argparse.ArgumentParser(description='远程无人机控制系统')
    parser.add_argument('--enable-keyboard', action='store_true', 
                        help='启用本地键盘控制（默认禁用）')
    args = parser.parse_args()
    
    # 检查并生成SSL证书（如果不存在）
    if not os.path.exists("server.crt") or not os.path.exists("server.key"):
        generate_self_signed_cert()
    
    # 检查pynput安装（仅当启用键盘时）
    if args.enable_keyboard:
        try:
            from pynput import keyboard
            global_keyboard_module = keyboard  # 设置全局引用
        except ImportError:
            print("错误: 未安装pynput库，请执行: pip install pynput")
            exit(1)
    
    # 创建控制器实例
    ctrl = ReliableController(enable_keyboard=args.enable_keyboard)
    ctrl.start()
    
    try:
        while ctrl.running:
            time.sleep(1)
    except KeyboardInterrupt:
        ctrl._log("检测到Ctrl+C，正在关闭...")
    finally:
        ctrl.shutdown()
        print("远程控制已关闭")