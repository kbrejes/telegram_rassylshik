# Деплой

```bash
gcloud compute ssh telegram-rassylshik-bot --zone=us-central1-a --command="cd /home/brejestovski_kirill/telegram_rassylshik && sudo git pull && sudo pkill -f main_multi; sleep 2; sudo nohup python3 main_multi.py > /tmp/bot.log 2>&1 &"
```

