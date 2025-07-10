import msvcrt
import time
import threading
from threading import Thread, Lock
from collections import deque
import websockets
import asyncio

class ReliableController:
    def __init__(self):
        self.websocket = None  # WebSocket连接对象
        self.event_queue = deque()  # 线程安全事件队列
        self.lock = Lock()
        self.running = True
        self.reconnect_count = 0
        self.connection_lock = threading.Lock()  # 连接锁
        
        # 创建专用事件循环
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
        # 初始化连接
        self._connect()

    def _connect(self):
        """建立到新veth接口(55.55.100.250)的WebSocket连接"""
        with self.connection_lock:
            while self.running and self.reconnect_count < 5:
                try:
                    # 关闭旧连接（如果存在）
                    if self.websocket:
                        try:
                            self.loop.run_until_complete(self.websocket.close())
                        except:
                            pass
                    
                    # 创建新连接 - 指向新veth接口
                    self.websocket = self.loop.run_until_complete(
                        websockets.connect(
                            'ws://55.55.100.250:5000',  # 新veth接口IP
                            ping_interval=None,  # 禁用自动ping
                            close_timeout=1.0,
                            # 增加连接超时和重试参数
                            open_timeout=5.0,
                            ping_timeout=5.0,
                            max_size=2**20  # 1MB最大消息
                        )
                    )
                    print("成功连接到ns7的专用控制接口(55.55.100.250)")
                    self.reconnect_count = 0
                    return
                except websockets.exceptions.InvalidURI:
                    print("错误: 无效的连接地址")
                    self.running = False
                    return
                except Exception as e:
                    print(f"连接失败: {str(e)}，5秒后重试...")
                    self.reconnect_count += 1
                    time.sleep(5)
            self.running = False
            print("连接失败次数过多，停止尝试")

    def _send_with_retry(self, event):
        """带重试机制的发送"""
        retries = 3
        while retries > 0 and self.running:
            try:
                # 检查连接状态
                if self.websocket is None or (hasattr(self.websocket, 'closed') and self.websocket.closed.done()):
                    self._connect()
                    if self.websocket is None:
                        retries -= 1
                        continue
                
                # 发送数据（同步方式）
                self.loop.run_until_complete(
                    self.websocket.send(f"{event}\n")
                )
                return True
            except (websockets.exceptions.ConnectionClosed, ConnectionResetError) as e:
                print(f"发送失败({retries}): 连接已关闭 - {str(e)}")
                self._connect()
                retries -= 1
                time.sleep(0.5)
            except websockets.exceptions.WebSocketException as e:
                print(f"WebSocket错误({retries}): {str(e)}")
                retries -= 1
                time.sleep(0.5)
            except Exception as e:
                print(f"未知发送错误({retries}): {str(e)}")
                retries -= 1
        return False

    def _event_handler(self):
        """事件处理线程（核心逻辑）"""
        # 启动心跳线程
        Thread(target=self._heartbeat, daemon=True).start()
        
        while self.running:
            if not self.event_queue:
                time.sleep(0.001)
                continue

            # 优先处理释放事件（防止刹车失效）
            release_events = [e for e in self.event_queue if ":release" in e]
            normal_events = [e for e in self.event_queue if ":release" not in e]

            # 处理队列顺序：释放事件优先
            for event in release_events + normal_events:
                if self._send_with_retry(event):
                    with self.lock:
                        try:
                            self.event_queue.remove(event)
                        except ValueError:
                            pass
                else:
                    print(f"丢弃未送达事件: {event}")
                    with self.lock:
                        try:
                            self.event_queue.remove(event)
                        except ValueError:
                            pass
                time.sleep(0.005)  # 控制发送间隔(200Hz)

    def _heartbeat(self):
        """心跳机制保持连接活跃"""
        while self.running:
            try:
                if (self.websocket is not None and hasattr(self.websocket, 'closed') and 
                    not self.websocket.closed.done()):
                    self._send_with_retry("heartbeat:ping")
                time.sleep(3)
            except Exception as e:
                print(f"心跳发送失败: {str(e)}")

    def _key_scanner(self):
        """键盘扫描线程（10ms响应级）"""
        current_keys = set()
        print("控制就绪 | 方向：WASD | 转向：Q/E | 升降：Z/X | 起飞：C | 降落：L | 退出：ESC")
        
        while self.running:
            # 实时检测按键变化
            new_keys = set()
            while msvcrt.kbhit():
                key = msvcrt.getch().decode('latin-1').lower()
                if key == '\x1b':  # ESC退出
                    self.running = False
                    print("\n用户请求退出")
                    break
                new_keys.add(key)

            # 检测按键变化
            for key in new_keys - current_keys:
                with self.lock:
                    self.event_queue.append(f"press:{key}")
                    # 调试信息
                    if key not in [' ', '\r']:  # 过滤空格和回车
                        print(f"按下: {key}")
            for key in current_keys - new_keys:
                with self.lock:
                    self.event_queue.append(f"release:{key}")
                    # 调试信息
                    if key not in [' ', '\r']:
                        print(f"释放: {key}")

            current_keys = new_keys
            time.sleep(0.01)  # 10ms扫描间隔(100Hz)

    def start(self):
        """启动双线程"""
        Thread(target=self._key_scanner).start()
        Thread(target=self._event_handler).start()
        
    def get_connection_status(self):
        """获取连接状态"""
        if (self.websocket is not None and hasattr(self.websocket, 'closed') and 
            not self.websocket.closed.done()):
            return "已连接"
        return "未连接"

if __name__ == "__main__":
    print("无人机远程控制器 - WebSocket版本")
    print("=" * 50)
    
    ctrl = ReliableController()
    if not ctrl.running:
        print("无法建立初始连接，程序退出")
        exit(1)
        
    ctrl.start()
    
    try:
        # 显示连接状态
        last_status = ""
        while ctrl.running:
            current_status = ctrl.get_connection_status()
            if current_status != last_status:
                print(f"\n[状态] 连接状态: {current_status}")
                last_status = current_status
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n检测到Ctrl+C，正在关闭...")
        ctrl.running = False
    finally:
        # 清理资源
        try:
            if ctrl.websocket:
                ctrl.loop.run_until_complete(ctrl.websocket.close())
        except:
            pass
        finally:
            ctrl.loop.close()
        
        print("远程控制已安全关闭")