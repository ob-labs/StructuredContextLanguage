FROM python:3.13-slim

# uv for fast, reproducible installs
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install dependencies from pyproject.toml for better layer caching.
# README.md is needed by setuptools during build metadata resolution.
COPY pyproject.toml README.md ./
RUN uv pip install --system --no-cache -e .

# Application code (skills submodule is intentionally not included; it is
# optional runtime data fetched separately).
COPY scl ./scl
COPY main.py prometheus.yml ./

EXPOSE 8080

CMD ["python", "main.py"]
