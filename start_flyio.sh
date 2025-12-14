#!/bin/bash
# Startup script for fly.io deployment
# Handles persistent storage symlinks

set -e

DATA_DIR="/app/data"

echo "=== Job Notification Bot - Fly.io Startup ==="

# Create subdirectories in persistent volume
mkdir -p "$DATA_DIR/sessions"
mkdir -p "$DATA_DIR/configs"
mkdir -p "$DATA_DIR/logs"

# Create symlinks from app directories to persistent volume
# Sessions
if [ ! -L "/app/sessions" ]; then
    rm -rf /app/sessions 2>/dev/null || true
    ln -sf "$DATA_DIR/sessions" /app/sessions
    echo "Linked sessions -> $DATA_DIR/sessions"
fi

# Configs
if [ ! -L "/app/configs" ]; then
    rm -rf /app/configs 2>/dev/null || true
    ln -sf "$DATA_DIR/configs" /app/configs
    echo "Linked configs -> $DATA_DIR/configs"
fi

# Logs
if [ ! -L "/app/logs" ]; then
    rm -rf /app/logs 2>/dev/null || true
    ln -sf "$DATA_DIR/logs" /app/logs
    echo "Linked logs -> $DATA_DIR/logs"
fi

# Copy default config if not exists
if [ ! -f "$DATA_DIR/configs/channels_config.json" ] && [ -f "/app/configs_default/channels_config.example.json" ]; then
    cp /app/configs_default/channels_config.example.json "$DATA_DIR/configs/channels_config.json"
    echo "Created default channels_config.json"
fi

echo "Persistent storage configured"
echo "Starting bot..."

# Start the multi-channel bot with web interface
exec python main_multi.py
