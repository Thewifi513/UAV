#!/bin/bash

gnome-terminal -t "SITL" -- bash -c "sh /home/sinet/mavsdk/PX4.sh;exec bash"

sleep 8
gnome-terminal -t "CONTROL" -- bash -c "sh /home/sinet/mavsdk/control.sh;exec bash"
