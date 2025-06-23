#!/usr/bin/env python3

import asyncio
from mavsdk import System
from mavsdk.offboard import PositionNedYaw
async def run():
    drone = System()
    #drone = System(mavsdk_server_address='localhost', port=50051) #仿真需要注释，真机解开注释
    await drone.connect(system_address="udp://:14540")
    status_text_task = asyncio.ensure_future(print_status_text(drone))
    print("等待连接...")
    async for state in drone.core.connection_state():
        if state.is_connected:
            print(f"-- 连接成功!")
            break
    print("GPS定点估算...")
    async for health in drone.telemetry.health():
        if health.is_global_position_ok and health.is_home_position_ok:
            print("-- GPS位置就绪")
            break
    #获取坐标
    global_origin = await drone.telemetry.get_gps_global_origin()
    g = global_origin.altitude_m
    x = global_origin.latitude_deg
    y = global_origin.longitude_deg
    print(g,x,y)
    #获取无人机信息
    x3 = await drone.info.get_flight_information()
    print(x3)
    y3 =await drone.info.get_version()
    print(y3)
    z3= await drone.info.get_product()
    print(z3)
    #电池电压百分比
    async for bat in drone.telemetry.battery():
        x4 = bat.voltage_v
        y4 = bat.remaining_percent*100
        print('%.2f'%x4,"V",'%.1f'%y4,"%")
        break
    #GPS状态及卫星数量
    async for a1 in drone.telemetry.gps_info(): 
        x5=a1.fix_type
        y5=a1.num_satellites
        print("gps：",x5,y5)
        break
    #解锁起飞降落等指令
    await drone.action.set_takeoff_altitude(2)
    print("-- Arming")
    await drone.action.arm()
    print("-- Taking off")
    await drone.action.takeoff()
    await asyncio.sleep(10)
    print("-- Landing")
    await drone.action.land()
    #电池电压百分比
    async for bat in drone.telemetry.battery():
        x4 = bat.voltage_v
        y4 = bat.remaining_percent*100
        print('%.2f'%x4,"V",'%.1f'%y4,"%")
        break

    #status_text_task.cancel()
    #GPS获取
    async for position in drone.telemetry.position():
        x1 = position.latitude_deg 
        y1 = position.longitude_deg
        z1 = position.relative_altitude_m
        g1 = position.absolute_altitude_m
        print("GPS坐标（相对高度与GPS高）",x1,y1,z1,g1)
        break
    # async for rawgps in drone.telemetry.raw_gps():
    #     x2 = rawgps.timestamp_us
    #     y2 =rawgps.heading_uncertainty_deg
    #     g2 =rawgps.latitude_deg
    #     z2 = rawgps.horizontal_uncertainty_m
    #     print(x2,y2,g2,z2)
    #     break
    # 获取更新GPS时间，经纬度等
#获取当前位置航向信息
async def send_position_data(drone):
    drone = System()
    async for position_data in drone.telemetry.position_velocity_ned():
        # 获取当前位置和航向信息
        current_position = position_data.position
        async for heading in drone.telemetry.attitude_euler():
            print(f"Heading: {heading.yaw_deg}")
        # 创建 PositionNedYaw 对象
        position_ned_yaw = PositionNedYaw(
            north_m=current_position.north_m,
            east_m=current_position.east_m,
            down_m=current_position.down_m,
            yaw_deg=heading.yaw_deg
            )
        # 发布位置信息
        await drone.telemetry.set_rate_position_velocity_ned(position_ned_yaw)
async def print_status_text(drone):
    drone = System()
    try:
        async for status_text in drone.telemetry.status_text():
            print(f"Status: {status_text.type}: {status_text.text}")
    except asyncio.CancelledError:
        return
#while(1):   #无限循环
    #asyncio.run(run())
#执行程序
if __name__ == "__main__":   #执行一次
    asyncio.run(run())

