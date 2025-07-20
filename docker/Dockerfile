# syntax=docker/dockerfile:1
ARG python=python:3.12-slim-bookworm

# Single stage:
FROM ${python}

# Install system dependencies
RUN python3 -m venv /venv
ENV PATH=/venv/bin:$PATH

# Install Python dependencies
COPY requirements.txt /build/requirements.txt
RUN pip3 install --upgrade pip \
 && pip3 install --no-cache-dir -r /build/requirements.txt

# Copy application code
WORKDIR /app
COPY src .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]