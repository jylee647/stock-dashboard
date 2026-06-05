# 주식 대시보드 — 어떤 호스트든 동작하는 Docker 이미지
FROM python:3.12-slim

WORKDIR /app

# 의존성 먼저 (캐시 활용)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 앱 코드
COPY backend ./backend
COPY web ./web

# 호스트가 주는 PORT 환경변수를 사용 (없으면 8000)
ENV PORT=8000
EXPOSE 8000

# 셸 형태로 실행해야 ${PORT} 치환됨
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
