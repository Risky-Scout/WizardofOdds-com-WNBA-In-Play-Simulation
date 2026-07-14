FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --system wizard && useradd --system --gid wizard --create-home wizard
WORKDIR /app

COPY pyproject.toml README.md /app/
COPY src /app/src

RUN python -m pip install --upgrade pip \
    && python -m pip install .

RUN mkdir -p /data/raw /data/normalized /data/recommendations /data/models \
    && chown -R wizard:wizard /data /app

USER wizard

EXPOSE 8080
HEALTHCHECK --interval=20s --timeout=4s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"

CMD ["wizard-wnba-api"]
