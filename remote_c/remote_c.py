# remote_controller_win.py
import msvcrt
import socket
import time
import threading
from threading import Thread, Lock
from collections import deque

class ReliableController:
    def __init__(self):
        self.sock = None
        self.event_queue = deque()  # 线程安全事件队列
        self.lock = Lock()
        self.running = True
        self.reconnect_count = 0
        self.connection_lock = threading.Lock()  # 新增连接锁
        
        # 初始化连接
        self._connect()

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
                    self.sock.settimeout(2.0)  # 设置合理的超时
                    self.sock.connect(('localhost', 5000))
                    print("成功连接地面站")
                    self.reconnect_count = 0
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
                if self.sock is None:
                    self._connect()
                    if self.sock is None:
                        retries -= 1
                        continue
                
                # 发送数据
                self.sock.sendall(f"{event}\n".encode())
                return True
            except (BrokenPipeError, ConnectionResetError) as e:
                print(f"发送失败({retries}): {str(e)}")
                self._connect()  # 尝试重新连接
                retries -= 1
                time.sleep(0.5)  # 等待后重试
            except Exception as e:
                print(f"未知发送错误: {str(e)}")
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
                if self.sock:
                    # 每5秒发送一次心跳
                    self._send_with_retry("heartbeat:ping")
                time.sleep(5)
            except:
                pass

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
                    break
                new_keys.add(key)

            # 检测按键变化
            for key in new_keys - current_keys:
                with self.lock:
                    self.event_queue.append(f"press:{key}")
            for key in current_keys - new_keys:
                with self.lock:
                    self.event_queue.append(f"release:{key}")  # 释放事件立即入队

            current_keys = new_keys
            time.sleep(0.01)  # 10ms扫描间隔(100Hz)

    def start(self):
        """启动双线程"""
        Thread(target=self._key_scanner).start()
        Thread(target=self._event_handler).start()

if __name__ == "__main__":
    ctrl = ReliableController()
    if not ctrl.running:
        print("无法建立初始连接，程序退出")
        exit(1)
        
    ctrl.start()
    
    try:
        while ctrl.running:
            time.sleep(1)
    except KeyboardInterrupt:
        ctrl.running = False
    finally:
        try:
            if ctrl.sock:
                ctrl.sock.close()
        except:
            pass
        print("远程控制已关闭")