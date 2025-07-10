在本实验的网络拓扑中，无人机相关配置被隔离在namespace中，需要sudo权限进入namespace进行操作，而mavlink相关环境安装在sinet用户下，无法直接在sudo用户下直接运行，此时命令修改如下：
sudo -E ip netns exec ns7 python3 ./remote_s2.py
-E 参数即在当前用户环境中运行此命令