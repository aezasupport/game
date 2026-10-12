Attribute Arena Online

Локальный тест (без Telegram):
  pip install -r requirements.txt
  set BOT_TOKEN=любой  &  set DEV=1      (PowerShell: $env:BOT_TOKEN="x"; $env:DEV="1")
  uvicorn server:app --port 8000
  Открой http://localhost:8000/?dev=Аня и в другой вкладке http://localhost:8000/?dev=Борис

Render (бесплатно): New > Web Service > репозиторий с этими файлами
  Build Command: pip install -r requirements.txt
  Start Command: uvicorn server:app --host 0.0.0.0 --port $PORT
  Environment: BOT_TOKEN=токен бота
Затем в .env бота: WEBAPP_URL=https://твой-сервис.onrender.com
