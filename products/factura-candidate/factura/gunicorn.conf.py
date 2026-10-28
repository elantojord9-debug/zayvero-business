"""Production HTTP entrypoint; migrations must run separately."""
import os

bind = "0.0.0.0:" + os.environ.get("PORT", "8801")
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
worker_class = "gthread"
threads = int(os.environ.get("WEB_THREADS", "4"))
timeout = 30
graceful_timeout = 30
keepalive = 5
limit_request_line = 4094
limit_request_fields = 64
limit_request_field_size = 8190
accesslog = None  # Reset URLs can include sensitive tokens.
errorlog = "-"


def on_starting(server):
    from factura_persist import db
    import config
    db.chequeo_serve()
    config.validar_superadmin_arranque()
    config.validar_origen_publico_arranque()
    config.validar_seguridad_lanzamiento()
