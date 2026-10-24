# YSOYA_Archive (Flask)
FROM python:3.12-slim
ARG INSTALL_FFMPEG=0
RUN if [ "$INSTALL_FFMPEG" = "1" ]; then apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*; fi
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# uid 1000: 서버의 첫 사용자와 같은 번호라서, ./data 폴더에 게시판 데이터를 쓸 수 있음
RUN useradd --uid 1000 --create-home web && mkdir -p /data && chown web /data
USER web
ENV DATA_DIR=/data ARCHIVE_ROOTS=/archive/ysoya_archive PORT=8000 PYTHONUNBUFFERED=1
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')" || exit 1
CMD ["gunicorn", "--workers", "2", "--threads", "4", "--bind", "0.0.0.0:8000", "--access-logfile", "-", "app:app"]
