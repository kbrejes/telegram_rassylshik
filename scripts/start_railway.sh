#!/bin/bash
# Startup script for Railway deployment
# Works with or without persistent volume

set -e

echo "=== Job Notification Bot - Railway Startup ==="

# Railway volume mount path (if configured)
DATA_DIR="${RAILWAY_VOLUME_MOUNT_PATH:-/app/data}"

echo "Data directory: $DATA_DIR"

# Create directories
mkdir -p "$DATA_DIR/sessions"
mkdir -p "$DATA_DIR/configs"
mkdir -p "$DATA_DIR/logs"

# Always create symlinks for configs, sessions, logs to DATA_DIR
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
if [ ! -f "$DATA_DIR/configs/channels_config.json" ]; then
    if [ -f "/app/configs_default/channels_config.example.json" ]; then
        cp /app/configs_default/channels_config.example.json "$DATA_DIR/configs/channels_config.json"
        echo "Created default channels_config.json"
    else
        echo '{"output_channels": []}' > "$DATA_DIR/configs/channels_config.json"
        echo "Created empty channels_config.json"
    fi
fi

# Restore session from environment variable (if provided)
if [ -n "$BOT_SESSION_BASE64" ] && [ ! -f "/app/bot_session.session" ]; then
    echo "Restoring bot session from environment..."
    echo "$BOT_SESSION_BASE64" | base64 -d > /app/bot_session.session
    echo "Bot session restored"
fi

# Create symlink for main bot session (bot looks for bot_session.session in /app/)
if [ -f "$DATA_DIR/sessions/bot_session.session" ] && [ ! -f "/app/bot_session.session" ]; then
    ln -sf "$DATA_DIR/sessions/bot_session.session" /app/bot_session.session
    echo "Linked bot_session.session -> $DATA_DIR/sessions/bot_session.session"
fi

echo "Storage configured"
echo "Starting bot on port ${PORT:-8080}..."

# Export port for uvicorn
export WEB_PORT="${PORT:-8080}"

# Start the multi-channel bot with web interface
exec python main_multi.py
