FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends masscan curl unzip && \
    curl -fsSL https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip -o /tmp/xray.zip && \
    unzip /tmp/xray.zip -d /usr/local/bin xray && chmod +x /usr/local/bin/xray && \
    rm -f /tmp/xray.zip && apt-get purge -y curl unzip && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p data logs

EXPOSE 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
