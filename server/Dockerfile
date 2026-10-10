FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 HOST=0.0.0.0 PORT=8080 QR_DB_ENABLED=true QR_DB_PATH=/app/data/qrcodes.db
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# favicon.svg and favicon.png match the copies at the repository root.
COPY app.py favicon.svg favicon.png ./
RUN mkdir -p /app/data && useradd -r -u 10001 app && chown -R app /app/data
USER app
VOLUME ["/app/data"]
EXPOSE 8080
HEALTHCHECK CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health')" || exit 1
CMD ["python", "app.py", "serve"]
