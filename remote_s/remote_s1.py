# ground_station.py
import asyncio
from mavsdk import System
from mavsdk.offboard import VelocityNedYaw, PositionNedYaw
from mavsdk.action import ActionError

class DroneController:
    def __init__(self):
        # 原有控制参数...
        self.flight_mode = "MANUAL"  # 新增飞行状态标识
        self.takeoff_altitude = 5.0  # 起飞高度
        self.last_update = None  # 初始化时间戳
        
        # 确保所有属性已定义
        self.velocity = [0.0, 0.0, 0.0]
        self.yaw = 0.0
        self.throttle = 0.5
        self.controls = {
            'w': False, 'a': False, 's': False, 'd': False,
            'q': False, 'e': False, 'z': False, 'x': False,
            'c': False, 'l': False, 'h': False,
            'j': False, 'k': False
        }

    async def handle_takeoff(self, drone):
        """整合原有起飞逻辑"""
        print("\n[起飞] 初始化起飞流程...")
        
        # 安全检查（保留原有检测逻辑）
        if not await self.safe_takeoff(drone):
            return

        # 确保已着陆
        async for is_in_air in drone.telemetry.in_air():
            if not is_in_air:
                print("[状态] 无人机已就绪")
                break
        
        try:
            # 解锁电机
            await drone.action.arm()
            
            # 使用原有位置控制起飞
            await drone.action.takeoff()
            await asyncio.sleep(5)  # 等待达到目标高度
            
            # 切换到速度控制模式
            await drone.offboard.set_velocity_ned(VelocityNedYaw(0, 0, 0, 0))
            await drone.offboard.start()
            self.flight_mode = "OFFBOARD"
            
        except ActionError as e:
            print(f"[错误] 起飞失败: {str(e)}")

    async def safe_takeoff(self, drone):
        """保留原有安全检测"""
        print("[安全检测] 正在进行起飞前检查...")
        # GPS检测
        async for health in drone.telemetry.health():
            if health.is_global_position_ok:
                print("[安全检测] GPS定位正常")
                break
        # 电池检测
        async for battery in drone.telemetry.battery():
            if battery.remaining_percent > 0.2:
                print(f"[安全检测] 电池电量 {battery.remaining_percent*100:.0f}%")
                return True
            else:
                print("[安全检测] 电量不足20%，禁止起飞！")
                return False

    async def control_loop(self, drone):
        """整合控制流程"""
        while True:
            if self.controls['c']:
                self.controls['c'] = False  # 单次触发
                if self.flight_mode == "MANUAL":
                    await self.handle_takeoff(drone)
            
            if self.flight_mode == "OFFBOARD":
                await self.smooth_control(drone)
            
            await asyncio.sleep(0.1)

    async def smooth_control(self, drone):
        """平滑控制主循环"""
        self.last_update = asyncio.get_event_loop().time()  # 初始化时间戳
        try:
            await drone.offboard.set_velocity_ned(VelocityNedYaw(0, 0, 0, 0))
            await drone.offboard.start()
            
            while True:
                now = asyncio.get_event_loop().time()
                dt = now - self.last_update
                self.last_update = now
                
                # 速度计算（基于持续按键）
                self._calculate_velocity(dt)
                
                # 发送速度指令
                await drone.offboard.set_velocity_ned(
                    VelocityNedYaw(
                        self.velocity[0], 
                        self.velocity[1], 
                        self.velocity[2], 
                        self.yaw
                    )
                )
                await asyncio.sleep(0.02)  # 50Hz控制频率
                
        except Exception as e:
            print(f"控制异常: {str(e)}")
            await drone.offboard.stop()

    def _calculate_velocity(self, dt):
        """计算平滑速度"""
        # 水平运动
        forward = 1 if self.controls['w'] else -1 if self.controls['s'] else 0
        right = 1 if self.controls['d'] else -1 if self.controls['a'] else 0
        
        # 标准化方向向量
        if forward != 0 or right != 0:
            norm = (forward**2 + right**2)**0.5
            self.velocity[0] = (forward / norm) * self.MAX_SPEED * self.throttle
            self.velocity[1] = (right / norm) * self.MAX_SPEED * self.throttle
        else:
            self.velocity[0] *= 0.8  # 惯性衰减
            self.velocity[1] *= 0.8

        # 垂直运动
        if self.controls['z']:
            self.velocity[2] = -self.MAX_SPEED * self.throttle  # 上升
        elif self.controls['x']:
            self.velocity[2] = self.MAX_SPEED * self.throttle   # 下降
        else:
            self.velocity[2] *= 0.8

        # 偏航控制
        if self.controls['q']:
            self.yaw -= self.YAW_RATE * dt
        elif self.controls['e']:
            self.yaw += self.YAW_RATE * dt
            
        # 油门控制
        if self.controls['j']:
            self.throttle = min(self.throttle + self.THROTTLE_STEP*dt, 1.0)
        if self.controls['k']:
            self.throttle = max(self.throttle - self.THROTTLE_STEP*dt, 0.0)

        # 保持航向在0-360范围
        self.yaw %= 360

class NetworkServer:
    def __init__(self, controller):
        self.controller = controller
    
    async def start(self, port=5000):
        server = await asyncio.start_server(
            self.handle_client, '0.0.0.0', port)
        async with server:
            print(f"控制服务已启动（端口 {port}）")
            await server.serve_forever()

    async def handle_client(self, reader, writer):
        print("远程控制端已连接")
        try:
            while True:
                data = await reader.read(32)
                if not data:
                    break
                msg = data.decode().strip().lower()
                if msg.startswith(('press:', 'release:')):
                    action, key = msg.split(':')
                    self.controller.update_controls(key, action == 'press')
        finally:
            writer.close()

async def main():
    drone = System()
    await drone.connect(system_address="udp://:14540")
    
    print("等待无人机连接...")
    async for state in drone.core.connection_state():
        if state.is_connected:
            break
    
    controller = DroneController()
    server = NetworkServer(controller)
    
    asyncio.create_task(server.start())
    await controller.control_loop(drone)

if __name__ == "__main__":
    asyncio.run(main())