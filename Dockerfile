FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates openssl && rm -rf /var/lib/apt/lists/*
# bcv.org.ve's server sends the wrong/stale intermediate certificate for its
# own leaf cert (a real misconfiguration on their end, confirmed via
# `openssl s_client -showcerts` — the leaf is issued by "Sectigo Public
# Server Authentication CA DV R36" but the server serves a different,
# outdated Sectigo intermediate instead). No client-side trust-store update
# fixes a server sending the wrong chain, so the actual issuing intermediate
# is fetched and trusted explicitly here instead of disabling verification.
RUN curl -fsSL http://crt.sectigo.com/SectigoPublicServerAuthenticationCADVR36.crt -o /tmp/sectigo-dv-r36.der \
    && openssl x509 -inform DER -in /tmp/sectigo-dv-r36.der -out /usr/local/share/ca-certificates/sectigo-public-server-auth-ca-dv-r36.crt \
    && update-ca-certificates \
    && rm -f /tmp/sectigo-dv-r36.der
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
RUN useradd --create-home app && mkdir -p /app/uploads && chown -R app:app /app/uploads
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
