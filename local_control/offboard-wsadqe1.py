import asyncio
from mavsdk import System
from mavsdk.offboard import PositionNedYaw, OffboardError
from pynput import keyboard  # 改用不需要root的库
import sys


class KeyboardControl:
    def __init__(self):
        self.w = self.a = self.s = self.d = False
        self.q = self.e = False
        self.z = self.x = False
        self.land_requested = False
        self.return_home_requested = False
        self.takeoff_requested = False
        self.listener = keyboard.Listener(
            on_press=self.on_press,
            on_release=self.on_release)
        self.listener.start()

    def on_press(self, key):
        try:
            k = key.char
            if k == 'w': self.w = True
            elif k == 'a': self.a = True
            elif k == 's': self.s = True
            elif k == 'd': self.d = True
            elif k == 'q': self.q = True
            elif k == 'e': self.e = True
            elif k == 'z': self.z = True
            elif k == 'x': self.x = True
            elif k == 'l': self.land_requested = True
            elif k == 'h': self.return_home_requested = True
            elif k == 'c': self.takeoff_requested = True
        except AttributeError:
            pass

    def on_release(self, key):
        try:
            k = key.char
            if k == 'w': self.w = False
            elif k == 'a': self.a = False
            elif k == 's': self.s = False
            elif k == 'd': self.d = False
            elif k == 'q': self.q = False
            elif k == 'e': self.e = False
            elif k == 'z': self.z = False
            elif k == 'x': self.x = False
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
    无人机控制说明：
    W/S: 前进/后退
    A/D: 左移/右移       
    Q/E: 左转/右转
    z/x: 上升/下降
    L: 立即降落
    H: 返航并降落
    C: 手动起飞
    ESC: 退出
    """)

    try:
        while True:
            # 处理控制指令
            if ctrl.w: x += MOVE_STEP
            if ctrl.s: x -= MOVE_STEP
            if ctrl.a: y -= MOVE_STEP
            if ctrl.d: y += MOVE_STEP
            if ctrl.q: yaw -= YAW_STEP
            if ctrl.e: yaw += YAW_STEP
            if ctrl.z: alt -= ALT_STEP
            if ctrl.x: alt += ALT_STEP
            
            # 处理特殊指令
            #紧急降落
            if ctrl.land_requested:
                ctrl.land_requested = False
                print("\n-- 执行紧急降落")
                await drone.offboard.stop()  # 先停止offboard模式
                
                try:
                    # 使用更可靠的着陆检测
                    await drone.action.land()
                    
                    # 通过in_air状态检测是否着陆
                    async for is_in_air in drone.telemetry.in_air():
                        if not is_in_air:
                            break
                        await asyncio.sleep(0.5)
                        
                    # 着陆后等待额外1秒确保稳定
                    await asyncio.sleep(1)
                    await drone.action.disarm()
                    return 'landed'
                    
                except Exception as e:
                    print(f"降落时发生错误: {str(e)}")
            #返航
            if ctrl.return_home_requested:
                ctrl.return_home_requested = False
                print("\n-- 执行返航")
                await drone.offboard.stop()
                
                try:
                    await drone.action.return_to_launch()
                    
                    # 使用组合检测：高度+in_air状态
                    async for pos in drone.telemetry.position():
                        if pos.relative_altitude_m < 1.0:  # 接近地面时
                            async for is_in_air in drone.telemetry.in_air():
                                if not is_in_air:
                                    break
                                await asyncio.sleep(0.5)
                            break
                        await asyncio.sleep(1)
                    
                    await asyncio.sleep(2)  # 额外等待确保稳定
                    await drone.action.disarm()
                    return 'rtl'
        
                except Exception as e:
                    print(f"返航时发生错误: {str(e)}")
            #手动起飞
            if ctrl.takeoff_requested:
                ctrl.takeoff_requested = False
                print("\n-- 准备重新起飞")
                
                # 检查是否已着陆
                async for is_in_air in drone.telemetry.in_air():
                    if not is_in_air:
                        break
                    await asyncio.sleep(0.1)

                try:
                    # 重新解锁并起飞
                    print("-- 解锁电机")
                    await drone.action.arm()
                    
                    print("-- 起飞到5米高度")
                    await drone.action.takeoff()
                    await asyncio.sleep(5)  # 等待到达目标高度
                    
                    # 重置控制参数
                    x, y, alt, yaw = 0.0, 0.0, -5.0, 0.0
                    
                    # 重新进入offboard模式
                    print("-- 启动offboard模式")
                    await drone.offboard.set_position_ned(PositionNedYaw(x, y, alt, yaw))
                    await drone.offboard.start()
                    
                except Exception as e:
                    print(f"起飞失败: {str(e)}")
                    continue

            # 发送控制指令
            await drone.offboard.set_position_ned(PositionNedYaw(x, y, alt, yaw))
            
            # 显示状态
            print(f"X:{x:5.1f}m Y:{y:5.1f}m 高度:{-alt:5.1f}m 航向:{yaw:5.1f}°", end='\r')
            
            await asyncio.sleep(0.1)
            
    except asyncio.CancelledError:
        pass
    except KeyboardInterrupt:
        return 'keyboard_interrupt'
    finally:
        ctrl.listener.stop()

async def run():
    drone = System()
    await drone.connect(system_address="udp://:14540")

    print("等待无人机连接...")
    async for state in drone.core.connection_state():
        if state.is_connected:
            print("-- 无人机已连接")
            break

    print("等待GPS定位...")
    async for health in drone.telemetry.health():
        if health.is_global_position_ok:
            print("-- GPS定位就绪")
            break

    print("-- 解锁电机")
    await drone.action.arm()

    print("-- 起飞")
    await drone.action.takeoff()
    await asyncio.sleep(5)  # 等待起飞完成

    print("-- 启动offboard模式")
    await drone.offboard.set_position_ned(PositionNedYaw(0.0, 0.0, -5.0, 0.0))
    try:
        await drone.offboard.start()
    except OffboardError as error:
        print(f"启动offboard失败: {error}")
        await drone.action.disarm()
        return

    result = None
    try:
        result = await drone_control(drone)
    except KeyboardInterrupt:
        print("\n用户中断")

    # 如果没有触发特殊指令则执行默认返航
    if result not in ['landed', 'rtl']:
        print("\n-- 执行默认返航")
        await drone.offboard.stop()
        await drone.action.return_to_launch()
        
        print("-- 等待降落...")
        while True:
            async for pos in drone.telemetry.position():
                if pos.relative_altitude_m < 0.5:
                    await drone.action.disarm()
                    return
            await asyncio.sleep(1)

if __name__ == "__main__":
    try:
        asyncio.get_event_loop().run_until_complete(run())
    except KeyboardInterrupt:
        print("程序终止")