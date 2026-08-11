# Fáze 14 — image pro `app` službu v docker-compose.
#
# Non-root uživatel (bezpečnostní požadavek zadání), pinované závislosti
# z requirements.txt (`pip install --no-cache-dir`), SQLite soubor jde na
# named volume `/data` (ne bind mount — Windows + SQLite zamykání, riziko R4).

FROM python:3.12-slim

WORKDIR /app

# Non-root uživatel; /data je cíl named volume pro SQLite soubor a musí být
# zapisovatelný appuserem ještě před prvním mountem (Docker při inicializaci
# prázdného named volume kopíruje obsah + oprávnění z odpovídající cesty
# v image).
RUN adduser --disabled-password --gecos "" appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /data

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
