FROM node:22-slim AS web-build

WORKDIR /app/web-ui

COPY web-ui/package*.json ./
RUN npm ci

COPY web-ui/ ./
RUN npm run build

FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY --from=web-build /app/web-ui/dist ./web-ui/dist

ENV PYTHONPATH=/app/src
ENV APP_SERVER_HOST=0.0.0.0
ENV APP_SERVER_PORT=8080

EXPOSE 8080

CMD ["python", "-m", "mech_chatbot.api.app_server"]
