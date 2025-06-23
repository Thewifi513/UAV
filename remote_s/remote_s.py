import asyncio
from mavsdk import System
from mavsdk.offboard import PositionNedYaw, OffboardError
from mavsdk.action import ActionError
from pynput import keyboard
import sys

class NetworkKeyboard:
    """ 替换原有KeyboardControl的网络版本 """
    def __init__(self):
        self.keys = {
            'w': False, 'a': False, 's': False, 'd': False,
            'q': False, 'e': False, 'z': False, 'x': False,
            'l': False, 'h': False, 'c': False,
            'j': False, 'k': False
        }
        self.server = None

    async def start_server(self, port=5000):
        self.server = await asyncio.start_server(
            self.handle_client, '0.0.0.0', port)
        async with self.server:
            print(f"控制接口已启动，监听端口: {port}")
            await self.server.serve_forever()

    async def handle_client(self, reader, writer):
        print("远程控制端已连接")
        try:
            while True:
                data = await reader.read(32)
                if not data: break
                msg = data.decode().strip().lower()
                if msg.startswith("press:"):
                    key = msg[6:]
                    if key in self.keys:
                        self.keys[key] = True
                elif msg.startswith("release:"):
                    key = msg[8:]
                    if key in self.keys:
                        self.keys[key] = False
        finally:
            writer.close()

async def drone_control(drone):
    ctrl = NetworkKeyboard()
    asyncio.create_task(ctrl.start_server())
    
    # 控制参数
    MOVE_STEP = 0.5    # 水平移动步长（米）
    YAW_STEP = 10.0    # 偏航角步长（度）
    throttle = 0.3     # 初始油门值
    
    # 初始状态
    x, y, alt, yaw = 0.0, 0.0, -5.0, 0.0  # NED坐标系
    
    print("""
    === 无人机控制系统 ===
    移动控制：
    W/S : 前进/后退
    A/D : 左移/右移        
    Q/E : 左转/右转
    Z/X : 上升/下降（油门控制）
    J/K : 增减油门（±0.05）

    功能键：
    C   : 重新起飞
    L   : 立即降落
    H   : 返航并降落
    ESC : 退出程序
    """)

    async def safe_takeoff():
        """起飞前安全检查"""
        print("\n[安全检测] 正在进行起飞前检查...")
        
        # GPS检测
        async for health in drone.telemetry.health():
            if health.is_global_position_ok:
                print("[安全检测] GPS定位正常")
                break
            await asyncio.sleep(0.1)
        
        # 电池检测
        async for battery in drone.telemetry.battery():
            if battery.remaining_percent > 0.2:
                print(f"[安全检测] 电池电量 {battery.remaining_percent*100:.0f}%")
                break
            else:
                print("[安全检测] 电量不足20%，禁止起飞！")
                return False
            await asyncio.sleep(0.1)
        
        return True

    try:
        while True:
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
            
            # 处理起飞请求
            if ctrl.keys['c']:
                ctrl.keys['c'] = False
                print("\n收到起飞指令...")
                
                if not await safe_takeoff():
                    continue
                
                # 确保已着陆
                print("[状态] 检查着陆状态...")
                async for is_in_air in drone.telemetry.in_air():
                    if not is_in_air:
                        print("[状态] 无人机已就绪")
                        break
                    await asyncio.sleep(0.1)

                try:
                    # 解锁电机
                    print("[动作] 尝试解锁...")
                    try:
                        await drone.action.arm()
                    except ActionError as e:
                        if e._result.result == Result.COMMAND_DENIED_NOT_LANDED:
                            print("[错误] 解锁失败：无人机未着陆！")
                        else:
                            print(f"[错误] 解锁失败：{str(e)}")
                        continue
                    
                    # 执行起飞
                    print("[动作] 起飞至5米高度...")
                    await drone.action.takeoff()
                    await asyncio.sleep(5)
                    
                    # 重置控制参数
                    x, y, alt, yaw = 0.0, 0.0, -5.0, 0.0
                    throttle = 0.3  # 重置油门值
                    
                    # 重新进入offboard模式
                    print("[模式] 启动offboard控制...")
                    await drone.offboard.set_position_ned(PositionNedYaw(x, y, alt, yaw))
                    await drone.offboard.start()
                    
                except Exception as e:
                    print(f"[错误] 起飞失败：{str(e)}")
                    continue

            # 处理降落
            if ctrl.keys['l']:
                ctrl.keys['l'] = False
                print("\n[紧急] 执行降落程序...")
                await drone.offboard.stop()
                
                try:
                    await drone.action.land()
                    print("[状态] 降落中...")
                    
                    async def check_landed():
                        async for is_in_air in drone.telemetry.in_air():
                            if not is_in_air:
                                return True
                            await asyncio.sleep(0.5)
                    
                    await asyncio.wait_for(check_landed(), timeout=30)
                    await drone.action.disarm()
                    print("[成功] 已安全着陆")
                    return 'landed'
                    
                except asyncio.TimeoutError:
                    print("[错误] 降落超时！")
                except Exception as e:
                    print(f"[错误] 降落失败：{str(e)}")

            # 处理返航
            if ctrl.keys['h']:
                ctrl.keys['h'] = False
                print("\n[导航] 执行返航程序...")
                await drone.offboard.stop()
                
                try:
                    await drone.action.return_to_launch()
                    print("[状态] 返航中...")
                    
                    async def check_rtl_complete():
                        while True:
                            position = await drone.telemetry.position().__anext__()
                            in_air = await drone.telemetry.in_air().__anext__()
                            if position.relative_altitude_m < 0.5 and not in_air:
                                return True
                            await asyncio.sleep(1)
                    
                    await asyncio.wait_for(check_rtl_complete(), timeout=60)
                    await drone.action.disarm()
                    print("[成功] 返航完成")
                    return 'rtl'
                    
                except asyncio.TimeoutError:
                    print("[错误] 返航超时，尝试强制降落！")
                    await drone.action.land()
                except Exception as e:
                    print(f"[错误] 返航失败：{str(e)}")

            # 发送控制指令
            await drone.offboard.set_position_ned(PositionNedYaw(x, y, alt, yaw))
            status = f"X:{x:5.1f}m Y:{y:5.1f}m 高度:{-alt:5.1f}m 航向:{yaw:5.1f}° 油门:{throttle:.2f}"
            print(status, end='\r')
            await asyncio.sleep(0.02)
            
    except asyncio.CancelledError:
        pass
    except KeyboardInterrupt:
        return 'user_exit'
    finally:
        ctrl.listener.stop()

async def main_loop():

    drone = System()
    #drone = System(mavsdk_server_address='localhost', port=50051) #仿真需要注释，真机解开注释
    await drone.connect(system_address="udp://:14540")

    # 连接阶段
    print("\n等待无人机连接...")
    async for state in drone.core.connection_state():
        if state.is_connected:
            print("[连接] 无人机已连接")
            break

    # 准备阶段
    print("等待GPS定位...")
    async for health in drone.telemetry.health():
        await asyncio.sleep(2)
        if health.is_global_position_ok:
            print("[定位] GPS就绪")
            break

    while True:
        try:
            # 初始化起飞
            print("\n[准备] 正在解锁电机...")
            await drone.action.arm()
            
            print("[动作] 起飞中...")
            await drone.action.takeoff()
            
            # 进入offboard模式
            print("[模式] 启动offboard控制")
            await drone.offboard.set_position_ned(PositionNedYaw(0.0, 0.0, -5.0, 0.0))
            await drone.offboard.start()
            
            # 运行控制程序
            control_result = await drone_control(drone)
            
            # 处理控制结束后的清理
            await drone.offboard.stop()
            
            if control_result in ('landed', 'rtl'):
                print("\n[系统] 进入待命模式，可用操作：")
                print("按下 C 重新起飞 | ESC 完全退出")
                
                # 创建新的键盘监听实例
                ctrl = NetworkKeyboard()
                try:
                    while True:
                        if ctrl.keys['c']:
                            print("\n[指令] 检测到起飞请求")
                            break
                        await asyncio.sleep(0.1)
                finally:
                    ctrl.listener.stop()
                    
            elif control_result == 'user_exit':
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
    await drone.offboard.stop()

if __name__ == "__main__":
    try:
        asyncio.get_event_loop().run_until_complete(main_loop())
    except KeyboardInterrupt:
        print("\n[系统] 程序已终止")
    print("系统关闭完成")