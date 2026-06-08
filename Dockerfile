FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY email_sender.py .
COPY sports_reminder.py .
COPY player_stats.py .

ENTRYPOINT ["python3", "sports_reminder.py"]
