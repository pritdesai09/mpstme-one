# ---------- Stage 1: build the React website ----------
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# "/" = call the API on the same website (no localhost)
ENV VITE_API_URL=/
RUN npm run build

# ---------- Stage 2: Python API that also serves the website ----------
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.prod.txt .
RUN pip install --no-cache-dir -r requirements.prod.txt
COPY backend/app ./app
COPY --from=web /web/dist ./static
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]