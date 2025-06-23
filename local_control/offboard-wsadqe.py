import asyncio
from mavsdk import System
from mavsdk.offboard import PositionNedYaw, OffboardError
from pynput import keyboard
import sys

class KeyboardControl:
    def __init__(self):
        self.w = self.a = self.s = self.d = False
        self.q = self.e = False
        self.z = self.x = False
        self.listener = keyboard.Listener(
            on_press=self.on_press,
            on_release=self.on_release)
        self.listener.start()

    def on_press(self, key):
        try:
            if key.char == 'w': self.w = True
            elif key.char == 'a': self.a = True
            elif key.char == 's': self.s = True
            elif key.char == 'd': self.d = True
            elif key.char == 'q': self.q = True
            elif key.char == 'e': self.e = True
            elif key.char == 'z': self.z = True
            elif key.char == 'x': self.x = True
        except AttributeError:
            pass

    def on_release(self, key):
        try:
            if key.char == 'w': self.w = False
            elif key.char == 'a': self.a = False
            elif key.char == 's': self.s = False
            elif key.char == 'd': self.d = False
            elif key.char == 'q': self.q = False
            elif key.char == 'e': self.e = False
            elif key.char == 'z': self.z = False
            elif key.char == 'x': self.x = False
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
    Z/X: 上升/下降
    ESC: 退出
    """)

    try:
        while True:
            # 处理键盘输入
            if ctrl.w: x += MOVE_STEP
            if ctrl.s: x -= MOVE_STEP
            if ctrl.a: y -= MOVE_STEP
            if ctrl.d: y += MOVE_STEP
            if ctrl.q: yaw -= YAW_STEP
            if ctrl.e: yaw += YAW_STEP
            if ctrl.z: alt -= ALT_STEP
            if ctrl.x: alt += ALT_STEP
            
            # 发送控制指令
            await drone.offboard.set_position_ned(PositionNedYaw(x, y, alt, yaw))
            
            # 显示状态
            print(f"X:{x:5.1f}m Y:{y:5.1f}m 高度:{-alt:5.1f}m 航向:{yaw:5.1f}°", end='\r')
            
            await asyncio.sleep(0.1)
            
    except asyncio.CancelledError:
        pass
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

    try:
        await drone_control(drone)
    except KeyboardInterrupt:
        print("\n用户中断")

    print("-- 返航")
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