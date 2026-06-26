FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p data \
    && addgroup --system botuser \
    && adduser --system --ingroup botuser botuser \
    && chown -R botuser:botuser /app

USER botuser

CMD ["python", "bot.py"]
