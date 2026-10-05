# syntax=docker/dockerfile:1
ARG python=python:3.12-slim-bookworm

# The panel assets: Tailwind compiles the CSS from the templates, and htmx is copied from npm.
FROM node:24-slim AS assets
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
COPY src/web ./src/web
RUN npm run build

# The application:
FROM ${python}

# Install system dependencies
RUN python3 -m venv /venv
ENV PATH=/venv/bin:$PATH

# Install Python dependencies
COPY requirements.txt /build/requirements.txt
RUN pip3 install --upgrade pip \
 && pip3 install --no-cache-dir -r /build/requirements.txt

# Copy application code. The imports are `src.*`, so src/ keeps its name; main.py is the entry point.
# .env.yaml is not baked in: docker-compose.yaml mounts it read-only at /app/.env.yaml.
WORKDIR /app
COPY main.py .
COPY src ./src
# The compiled panel assets are not in git: they come from the assets stage.
COPY --from=assets /build/src/web/static/dist ./src/web/static/dist

# The version the OpenAPI page shows: build.sh passes the project's version and the commit.
ARG VERSION=dev
ENV VERSION=${VERSION}

# --no-access-log: RequestIdMiddleware writes one JSON line per request, with its id.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
