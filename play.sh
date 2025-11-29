#!/bin/bash

export DISPLAY=:0
export XDG_RUNTIME_DIR=/run/user/$(id -u)

cvlc -I dummy \
    --file-caching=200 \
    --network-caching=200 \
     --extraintf http \
     --http-port 8080 \
     --http-password secretpw \
     --fullscreen \
     --loop \
     "$1"
