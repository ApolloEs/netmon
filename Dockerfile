FROM python:3.12-slim

# Ookla Speedtest CLI from Ookla's official packagecloud apt repo.
# License/GDPR acceptance happens at invocation: the app always passes
# --accept-license --accept-gdpr (see netmon/speed_test.py).
# Fetch tools are removed again in the same layer to keep the image lean.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl gnupg ca-certificates \
    && curl -fsSL https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.deb.sh | bash \
    && apt-get install -y --no-install-recommends speedtest \
    && apt-get purge -y curl gnupg \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
EXPOSE 5000

# Tables are created/migrated automatically at startup (db.ensure_schema).
CMD ["python", "-m", "netmon.main"]
