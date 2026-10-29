FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-deps .

COPY data ./data
COPY scripts ./scripts

# Default: run the offline demo (fake LLM, no API key needed).
CMD ["ipa", "demo", "--reset"]
