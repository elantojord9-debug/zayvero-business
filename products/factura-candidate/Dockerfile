FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY factura/requirements.txt /app/requirements.txt
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r /app/requirements.txt \
    && useradd --create-home --uid 10001 factura
COPY --chown=factura:factura factura /app/factura
COPY --chown=factura:factura zayvero-business /app/zayvero-business
COPY SOURCE_LOCK.json /app/SOURCE_LOCK.json
USER factura
WORKDIR /app/factura
ENV FACTURA_BIZ_PATH=/app/zayvero-business FACTURA_ENV=production FACTURA_HTTPS=1
EXPOSE 8801
CMD ["python", "-m", "gunicorn", "-c", "gunicorn.conf.py", "wsgi:application"]
