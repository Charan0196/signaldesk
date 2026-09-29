# SignalDesk — container image
#
#   docker build -t signaldesk .
#   docker run -p 8420:8420 -e HINDSIGHT_API_KEY=hsk_... signaldesk
#
# The app is stateless: all customer memory lives in the Hindsight bank, so any
# number of containers can serve the same data.

FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8420 \
    HOST=0.0.0.0

WORKDIR /app

# Install deps first so code edits don't bust the layer cache.
COPY pyproject.toml README.md ./
COPY signaldesk ./signaldesk
RUN pip install --no-cache-dir . "uvicorn[standard]" fastapi

# Run as a non-root user.
RUN useradd --create-home --shell /usr/bin/bash signaldesk
USER signaldesk

EXPOSE 8420

# The health endpoint touches no external service, so a 200 means the process
# is genuinely serving — not just bound.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8420/api/state', timeout=4).status==200 else 1)"

CMD ["python", "-m", "signaldesk.web"]
