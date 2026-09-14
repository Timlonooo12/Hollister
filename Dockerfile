FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    STOCKWATCH_STATE_FILE=/data/stockwatch-state.json

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY stockwatch ./stockwatch

# The state file must survive restarts, otherwise a redeploy re-announces a
# size that was already in stock.
RUN useradd --create-home --uid 10001 stockwatch \
    && mkdir -p /data && chown stockwatch:stockwatch /data
VOLUME ["/data"]
USER stockwatch

CMD ["python", "-m", "stockwatch"]
