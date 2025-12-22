#!/bin/bash
# Server control for Google Cloud
# Usage: ./server.sh [start|stop|logs|deploy]

ZONE="us-central1-a"
INSTANCE="telegram-rassylshik-bot"

case "$1" in
  stop)
    echo "=== Stopping server ==="
    gcloud compute ssh $INSTANCE --zone=$ZONE --command="docker stop telegram-bot"
    echo "Server stopped. You can now run locally."
    ;;

  start)
    echo "=== Starting server ==="
    gcloud compute ssh $INSTANCE --zone=$ZONE --command="docker start telegram-bot && sleep 3 && docker logs telegram-bot --tail 20"
    ;;

  logs)
    echo "=== Server logs ==="
    gcloud compute ssh $INSTANCE --zone=$ZONE --command="docker logs telegram-bot --tail 50"
    ;;

  deploy)
    echo "=== Pushing to GitHub ==="
    git push origin main

    echo "=== Deploying to Google Cloud ==="
    gcloud compute ssh $INSTANCE --zone=$ZONE --command="
cd telegram_rassylshik && \
git pull && \
docker build -t telegram-bot . && \
docker stop telegram-bot 2>/dev/null || true && \
docker rm telegram-bot 2>/dev/null || true && \
docker run -d \
  --name telegram-bot \
  --restart unless-stopped \
  -p 8080:8080 \
  --env-file .env.prod \
  -v \$(pwd)/data:/app/data \
  telegram-bot && \
sleep 5 && \
docker logs telegram-bot --tail 30
"
    ;;

  *)
    echo "Usage: $0 [start|stop|logs|deploy]"
    echo ""
    echo "  stop   - Stop server (to work locally)"
    echo "  start  - Start server"
    echo "  logs   - View server logs"
    echo "  deploy - Push code and restart server"
    ;;
esac
