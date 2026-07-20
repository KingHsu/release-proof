FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    RELEASE_PROOF_DATA_DIR=/app/runtime \
    RELEASE_PROOF_PROJECT_ROOT=/app \
    RELEASE_PROOF_OFFLINE=true

RUN groupadd --system app && useradd --system --gid app --create-home app
WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY skills ./skills
COPY evals ./evals
ARG PIP_INDEX_URL=https://pypi.org/simple/
RUN python -m pip install --upgrade pip && \
    python -m pip install . --index-url "$PIP_INDEX_URL"

RUN mkdir -p /app/runtime && chown -R app:app /app
USER app

EXPOSE 8002
HEALTHCHECK --interval=20s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8002/health', timeout=3)" || exit 1

CMD ["uvicorn", "release_proof.api.app:app", "--host", "0.0.0.0", "--port", "8002"]
