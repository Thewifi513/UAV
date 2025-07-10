import asyncio
from mavsdk import System
from mavsdk.offboard import PositionNedYaw, OffboardError
from mavsdk.action import ActionError
import sys
import websockets  # 新增WebSocket库

class NetworkKeyboard:
    """网络键盘控制类（WebSocket版）"""
    def __init__(self):
        self.keys = {
            'w': False, 'a': False, 's': False, 'd': False,
            'q': False, 'e': False, 'z': False, 'x': False,
            'l': False, 'h': False, 'c': False,
            'j': False, 'k': False
        }
        self.websocket = None  # 替换原TCP连接
    
    async def start_server(self, port=5000):
        """启动WebSocket服务器"""
        async with websockets.serve(
            self.handle_client, 
            "55.55.100.123",  # 新veth的IP
            port
        ):
            print(f"WebSocket服务绑定到55.55.100.123:{port}")
            await asyncio.Future()  # 持续运行

    async def handle_client(self, websocket, path):
        """处理WebSocket客户端连接"""
        print("远程控制端已连接 (WebSocket)")
        self.websocket = websocket
        
        try:
            async for message in websocket:  # 持续接收消息
                msg = message.strip().lower()
                
                # 处理按键消息（逻辑不变）
                if msg.startswith("press:"):
                    key = msg[6:]
                    if key in self.keys:
                        self.keys[key] = True
                elif msg.startswith("release:"):
                    key = msg[8:]
                    if key in self.keys:
                        self.keys[key] = False
                elif msg == "heartbeat:ping":
                    pass  # 忽略心跳包
                    
        except websockets.exceptions.ConnectionClosed:
            print("远程控制端已断开 (WebSocket)")
        finally:
            self.websocket = None


async def drone_control(drone):
    ctrl = NetworkKeyboard()
    server_task = asyncio.create_task(ctrl.start_server())
    
    # 控制参数
    MOVE_STEP = 0.5    # 水平移动步长（米）
    YAW_STEP = 10.0    # 偏航角步长（度）
    throttle = 0.05     # 初始油门值
    
    # 初始状态
    x, y, alt, yaw = 0.0, 0.0, 0.0, 0.0  # NED坐标系
    armed = False  # 无人机是否已解锁
    initial_alt = 0.0  # 初始高度
    
    print("""
    === 无人机控制系统 ===
    移动控制：
    W/S : 前进/后退
    A/D : 左移/右移        
    Q/E : 左转/右转
    Z/X : 上升/下降（油门控制）
    J/K : 增减油门（±0.05）

    功能键：
    C   : 解锁并进入offboard模式
    L   : 立即降落（最高优先级）
    H   : 返航并降落（最高优先级）
    """)

    async def safe_takeoff():
        """起飞前安全检查"""
        print("\n[安全检测] 正在进行快速检查...")
        
        # 快速GPS检测
        health = await drone.telemetry.health().__anext__()
        if health.is_global_position_ok:
            print("[安全检测] GPS定位正常")
            return True
        print("[安全检测] GPS未就绪")
        return False

    try:
        while True:
            # 1. 最高优先级：处理降落和返航
            if ctrl.keys['l'] or ctrl.keys['h']:
                # 清除按键状态
                landing = ctrl.keys['l']
                rtl = ctrl.keys['h']
                ctrl.keys['l'] = False
                ctrl.keys['h'] = False
                
                # 保存当前状态
                was_armed = armed
                
                # 停止offboard模式
                if was_armed:
                    try:
                        await drone.offboard.stop()
                    except OffboardError:
                        pass
                
                # 执行降落或返航
                if landing:
                    print("\n[紧急] 执行最高优先级降落程序...")
                    await drone.action.land()
                    print("[状态] 降落中...")
                    
                    # 等待着陆
                    for _ in range(30):
                        is_in_air = await drone.telemetry.in_air().__anext__()
                        if not is_in_air:
                            break
                        await asyncio.sleep(0.1)
                    
                    try:
                        await drone.action.disarm()
                    except:
                        pass
                    
                    # 重置状态
                    armed = False
                    x, y, alt, yaw = 0.0, 0.0, 0.0, 0.0
                    print("[成功] 已安全着陆")
                    
                elif rtl:
                    print("\n[紧急] 执行最高优先级返航程序...")
                    await drone.action.return_to_launch()
                    print("[状态] 返航中...")
                    
                    # 等待返航完成
                    for _ in range(60):
                        position = await drone.telemetry.position().__anext__()
                        in_air = await drone.telemetry.in_air().__anext__()
                        if position.relative_altitude_m < 1.0 and not in_air:
                            break
                        await asyncio.sleep(0.1)
                    
                    try:
                        await drone.action.disarm()
                    except:
                        pass
                    
                    # 重置状态
                    armed = False
                    x, y, alt, yaw = 0.0, 0.0, 0.0, 0.0
                    print("[成功] 返航完成")
                
                # 跳过本次循环其他操作
                continue
            
            # 2. 正常控制逻辑
            # 处理油门调整
            if ctrl.keys['j']:
                throttle = min(throttle + 0.05, 1.0)
                ctrl.keys['j'] = False  # 单次触发
                print(f"[油门] 当前：{throttle:.2f}")
            if ctrl.keys['k']:
                throttle = max(throttle - 0.05, 0.0)
                ctrl.keys['k'] = False  # 单次触发
                print(f"[油门] 当前：{throttle:.2f}")

            # 处理垂直控制
            if armed:
                if ctrl.keys['z']: alt -= throttle  # 上升
                if ctrl.keys['x']: alt += throttle  # 下降

                # 处理水平控制
                if ctrl.keys['w']: x += throttle
                if ctrl.keys['s']: x -= throttle
                if ctrl.keys['a']: y -= throttle
                if ctrl.keys['d']: y += throttle
                
                # 处理偏航控制
                if ctrl.keys['q']: yaw -= YAW_STEP
                if ctrl.keys['e']: yaw += YAW_STEP
                
                # 归一化偏航角
                if yaw > 180: yaw -= 360
                if yaw < -180: yaw += 360
            
            # 处理解锁请求
            if ctrl.keys['c']:
                ctrl.keys['c'] = False
                print("\n收到解锁指令...")
                
                if not await safe_takeoff():
                    continue
                
                # 快速检查着陆状态
                is_in_air = await drone.telemetry.in_air().__anext__()
                if is_in_air:
                    print("[警告] 无人机已在空中")
                    # 获取当前位置作为起点
                    position = await drone.telemetry.position().__anext__()
                    initial_alt = position.relative_altitude_m
                    alt = -initial_alt  # NED坐标系下负值为高度
                else:
                    print("[状态] 无人机已就绪")
                    initial_alt = 0.0
                    alt = 0.0

                try:
                    # 解锁电机
                    print("[动作] 尝试解锁...")
                    try:
                        await drone.action.arm()
                        armed = True
                        print("[状态] 无人机已解锁")
                        
                        # 进入offboard模式
                        print("[模式] 启动offboard控制...")
                        try:
                            await drone.offboard.set_position_ned(
                                PositionNedYaw(x, y, alt, yaw))
                            await drone.offboard.start()
                            print("[状态] 已进入offboard模式，按Z键上升")
                        except OffboardError as e:
                            print(f"[错误] 启动offboard失败: {str(e)}")
                    
                    except ActionError as e:
                        print(f"[错误] 解锁失败：{str(e)}")
                        continue
                    
                except Exception as e:
                    print(f"[错误] 解锁失败：{str(e)}")
                    continue

            # 发送控制指令
            if armed:
                try:
                    await drone.offboard.set_position_ned(PositionNedYaw(x, y, alt, yaw))
                except OffboardError:
                    pass  # 短暂错误可忽略
            
            # 显示状态信息 - 单行刷新
            status = f"X:{x:5.1f}m Y:{y:5.1f}m 高度:{-alt:5.1f}m 航向:{yaw:5.1f}° 油门:{throttle:.2f}"
            if not armed:
                status += " [未解锁]"
            else:
                status += " [已解锁]"
            print(status, end="\r")
            
            # 高频控制循环 - 50Hz
            await asyncio.sleep(0.02)
            
    except asyncio.CancelledError:
        pass
    except KeyboardInterrupt:
        return 'user_exit'
    finally:
        # 清理网络服务器
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass

async def reset_fc_state(drone):
    """强制重置飞控状态到安全模式"""
    try:
        # 1. 停止offboard模式
        try:
            await drone.offboard.stop()
            print("[状态重置] Offboard模式已停止")
        except OffboardError:
            pass
        
        # 2. 发送零速度指令（清除残留指令）
        try:
            await drone.offboard.set_velocity_ned(VelocityNedYaw(0.0, 0.0, 0.0, 0.0))
            print("[状态重置] 零速度指令已发送")
        except OffboardError:
            pass
        
        # 3. 检查并解锁/加锁
        is_armed = await drone.telemetry.armed().__anext__()
        if is_armed:
            try:
                await drone.action.disarm()
                print("[状态重置] 已加锁")
            except ActionError:
                pass
    except Exception as e:
        print(f"[警告] 状态重置失败: {str(e)}")

async def main_loop():
    drone = System()
    #drone = System(mavsdk_server_address='localhost', port=50051) #仿真注释，真机解开
    await drone.connect(system_address="udp://:14540")

    # 连接阶段
    print("\n等待无人机连接...")
    try:
        await asyncio.wait_for(drone.connect(), timeout=5.0)
        print("[连接] 无人机已连接")
    except asyncio.TimeoutError:
        print("[错误] 连接无人机超时")
        return
    
    print("执行飞控状态重置...")
    await reset_fc_state(drone)

    # 准备阶段
    print("等待GPS定位...")
    for _ in range(5):  # 最多等待5秒
        health = await drone.telemetry.health().__anext__()
        if health.is_global_position_ok:
            print("[定位] GPS就绪")
            break
        await asyncio.sleep(1)
    else:
        print("[警告] GPS未就绪，继续操作")

    while True:
        try:
            # 运行控制程序
            control_result = await drone_control(drone)
            
            # 处理控制结束后的清理
            try:
                await drone.offboard.stop()
            except:
                pass
            
            if control_result == 'user_exit':
                break

        except KeyboardInterrupt:
            print("\n[系统] 用户中断操作")
            break
        except Exception as e:
            print(f"[严重错误] 系统异常：{str(e)}")
            break

    # 最终清理
    print("\n[系统] 正在关闭...")
    try:
        await drone.action.disarm()
    except:
        pass
    try:
        await drone.offboard.stop()
    except:
        pass

if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        print("\n[系统] 程序已终止")
    print("系统关闭完成")