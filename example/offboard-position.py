import asyncio
from mavsdk import System
from mavsdk.offboard import (OffboardError,PositionNedYaw,VelocityNedYaw,VelocityBodyYawspeed,AttitudeRate)


#END坐标，高度为-数，以下程序做了修改，正常数输入即可

async def move_right(drone):
    
    x=0
    i=0
    while True:

        print("输入起飞高度") 
        x=int(input())
        print(x)
        if x > 0:
            pass
        else:
            print("数据无效")
        print("输入1起飞")
        i=int(input())
        print(i)
        if i==1:
            break
        else:
            print("重新输入")
   
        

    var1, var2, var3, var4 = 0, 0, 0, 0
  
    await drone.action.takeoff()
    
    async for heading in drone.telemetry.attitude_euler():
        print(heading.yaw_deg)
        break
    await drone.offboard.set_position_ned(PositionNedYaw(0.0, 0.0, 0.0, heading.yaw_deg)) 
    if i==1:
        print("-- Arm")
        await drone.action.arm()

        x=-x
        await drone.offboard.set_position_ned(PositionNedYaw(0.0, 0.0, x, heading.yaw_deg))
        
        
    print("-- Starting offboard")
    try:
        await drone.offboard.start()
    except OffboardError as error:
        print(f"切换 offboard mode 失败，错误代码: \
            {error._result.result}")
        print("-- Disarming")
        await drone.action.disarm()
        return

        

    

    while True:
        
        input_str = input("输入控制模式下X轴,Y轴,z轴对应,航向角/油门0-1行程(注意：高度为相对高度，输入负数意味着低于起飞高度)   空格隔开 ")
        try:
            var1, var2, var3, var4 = map(float, input_str.split())
            if var3>0:
                var33=-var3
            else:
                var33=-var3 

        except ValueError:
            print("输入无效，请重试.")
            continue
        if var1 != 0 or var2 != 0 or var33 != 0 or var4 != 0 :
                print(var1,var2,var3,var4)
                print("-- 执行")
                await drone.offboard.set_position_ned(PositionNedYaw(var1, var2, var33, var4))  
                #END坐标下航点及航向
                #await drone.offboard.set_velocity_body(VelocityBodyYawspeed(var1, var2, var33, var4))
                #await asyncio.sleep(5)
                #break
                #END机体坐标下设置x,y,z速度与偏航向角度
                #await drone.offboard.set_velocity_ned(VelocityNedYaw(var1, var2, var33, var4))
                #NED坐标设置x,y,z速度和航向
                #await drone.offboard.set_attitude_rate(AttitudeRate(var1, var2, var33, var4))
                #END坐标设置姿态角度及油门杆量

            
        input_str = input("输入 回车继续，'q' 退出: ")
        if input_str == 'q':
            break





async def run():
    drone = System()
    #drone = System(mavsdk_server_address='localhost', port=50051)
    await drone.connect(system_address="udp://:14540")

    async for health in drone.telemetry.health():
        
        if health.is_global_position_ok and health.is_home_position_ok:
            print("-- OK")
            break


    await drone.action.arm()
    async for heading in drone.telemetry.attitude_euler():
        print(heading.yaw_deg)
        break
   


    


    await move_right(drone)



 


    print("-- Stopping offboard")
    try:
        await drone.offboard.stop()
    except OffboardError as error:
        print(f"Stopping offboard mode failed with error code: \
              {error._result.result}")
 

    print("-- hold")
    await drone.action.hold()
    f=0
    g=0
    while True:

        print("输入返航高度") 
        f=int(input())
        print(f)
        if f > 0:
            await drone.action.set_return_to_launch_altitude(f)
        else:
            print("数据无效")
        print("输入1返航")
        g=int(input())
        print(g)
        if g==1:
            break
        else:
            print("重新输入")

    gao = await drone.action.get_return_to_launch_altitude() 


    print("返航高度",gao)
    await drone.action.return_to_launch()



    #await asyncio.sleep(30)
    #print("-- Landing")
    #await drone.action.land()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(run())
