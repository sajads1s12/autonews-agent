# 🐳 AutoNews Agent — صورة تشغيل مستقلة (بديل GitHub Actions)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY config/ ./config/
COPY src/ ./src/

RUN mkdir -p state logs

# تشغيل دورة واحدة افتراضياً (استخدم runner.py للجدولة المستمرة)
ENTRYPOINT ["python", "-m", "autonews.main"]
CMD ["--once"]
