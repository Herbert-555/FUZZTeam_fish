FROM python:3.8-slim

WORKDIR /app

ARG PIP_INDEX_URL=""

COPY requirements.txt .

RUN if [ -n "$PIP_INDEX_URL" ]; then \
        pip install --no-cache-dir -i "$PIP_INDEX_URL" -r requirements.txt; \
    else \
        pip install --no-cache-dir -r requirements.txt; \
    fi \
    && rm -rf /root/.cache/pip

COPY . .

VOLUME ["/app/data", "/app/uploads", "/app/output"]

EXPOSE 5000 8080

ENTRYPOINT ["python", "run.py"]
CMD ["--host", "0.0.0.0"]
