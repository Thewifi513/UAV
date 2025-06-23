import asyncio
from mavsdk import System
from mavsdk.offboard import PositionNedYaw, OffboardError
from mavsdk.action import ActionError
from pynput import keyboard
import sys

class KeyboardControl:
    def __init__(self):
        self.keys = {
            'w': False, 'a': False, 's': False, 'd': False,
            'q': False, 'e': False, 'z': False, 'x': False,
            'l': False, 'h': False, 'c': False
        }
        self.listener = keyboard.Listener(
            on_press=self.on_press,
            on_release=self.on_release)
        self.listener.start()

    def on_press(self, key):
        try:
            k = key.char.lower()
            if k in self.keys:
                self.keys[k] = True
        except AttributeError:
            pass

    def on_release(self, key):
        try:
            k = key.char.lower()
            if k in self.keys:
                self.keys[k] = False
        except AttributeError:
            pass

async def drone_control(drone):
    ctrl = KeyboardControl()
    
    # 控制参数
    MOVE_STEP = 0.5
    ALT_STEP = 0.3
    YAW_STEP = 10.0
    
    # 初始状态
    x, y, alt, yaw = 0.0, 0.0, -5.0, 0.0
    
    print("""
    === 无人机控制系统 ===
    移动控制：
    W/S : 前进/后退
    A/D : 左移/右移        
    Q/E : 左转/右转
    Z/X : 上升/下降

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
                    
                    # 重新进入offboard模式
                    print("[模式] 启动offboard控制...")
                    await drone.offboard.set_position_ned(PositionNedYaw(x, y, alt, yaw))
                    await drone.offboard.start()
                    
                except Exception as e:
                    print(f"[错误] 起飞失败：{str(e)}")
                    continue

            # 处理移动控制
            if ctrl.keys['w']: x += MOVE_STEP
            if ctrl.keys['s']: x -= MOVE_STEP
            if ctrl.keys['a']: y -= MOVE_STEP
            if ctrl.keys['d']: y += MOVE_STEP
            if ctrl.keys['q']: yaw -= YAW_STEP
            if ctrl.keys['e']: yaw += YAW_STEP
            if ctrl.keys['z']: alt -= ALT_STEP
            if ctrl.keys['x']: alt += ALT_STEP
            
            # 处理降落
            if ctrl.keys['l']:
                ctrl.keys['l'] = False
                print("\n[紧急] 执行降落程序...")
                await drone.offboard.stop()
                
                try:
                    await drone.action.land()
                    print("[状态] 降落中...")
                    
                    # 使用组合检测确保着陆
                    async def check_landed():
                        async for is_in_air in drone.telemetry.in_air():
                            if not is_in_air:
                                return True
                            await asyncio.sleep(0.5)
                    
                    await asyncio.wait_for(check_landed(), timeout=30)
                    await drone.action.disarm()
                    print("[成功] 已安全着陆")
                    return 'landed'  # 退出控制循环
                    
                except asyncio.TimeoutError:
                    print("[错误] 降落超时！")
                except Exception as e:
                    print(f"[错误] 降落失败：{str(e)}")

            # 处理返航（关键修复部分）
            if ctrl.keys['h']:
                ctrl.keys['h'] = False
                print("\n[导航] 执行返航程序...")
                await drone.offboard.stop()
                
                try:
                    await drone.action.return_to_launch()
                    print("[状态] 返航中...")
                    
                    # 改进的状态检测
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
                    return 'rtl'  # 退出控制循环
                    
                except asyncio.TimeoutError:
                    print("[错误] 返航超时，尝试强制降落！")
                    await drone.action.land()
                except Exception as e:
                    print(f"[错误] 返航失败：{str(e)}")

            # 发送控制指令
            await drone.offboard.set_position_ned(PositionNedYaw(x, y, alt, yaw))
            print(f"[状态] X:{x:5.1f}m Y:{y:5.1f}m 高度:{-alt:5.1f}m 航向:{yaw:5.1f}°", end='\r')
            await asyncio.sleep(0.1)
            
    except asyncio.CancelledError:
        pass
    except KeyboardInterrupt:
        return 'user_exit'
    finally:
        ctrl.listener.stop()

async def main_loop():
    drone = System()
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
            await asyncio.sleep(5)
            
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
                ctrl = KeyboardControl()
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