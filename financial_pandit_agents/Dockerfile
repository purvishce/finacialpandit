FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PYTHONPATH=/app/financial_pandit_agents/src \
    PATH="/app/financial_pandit_agents/.venv/bin:$PATH" \
    MPLBACKEND=Agg \
    MPLCONFIGDIR=/home/app/.cache/matplotlib \
    CREWAI_STORAGE_DIR=/home/app/.cache/crewai \
    CREWAI_TRACING_ENABLED=false

# Keep the source layout so existing database and chart paths resolve correctly.
WORKDIR /app/financial_pandit_agents

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --no-cache

RUN useradd --create-home --uid 10001 app \
    && mkdir -p output/charts /home/app/.cache/crewai \
    && chown -R app:app output /home/app

COPY --chown=app:app src ./src

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).close()"]

CMD ["python", "-m", "uvicorn", "financial_pandit_agents.api:app", "--host", "0.0.0.0", "--port", "8000"]
