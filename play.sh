#!/bin/bash

export DISPLAY=:0
export XDG_RUNTIME_DIR=/run/user/$(id -u)

cvlc -I dummy \
     --extraintf http \
     --http-port 8080 \
     --http-password secretpw \
     --fullscreen \
     --loop \
     "$1"
