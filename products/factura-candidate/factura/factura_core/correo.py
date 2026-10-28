"""Despachador de correo configurable (P0 #1).

Interfaz única para los correos del sistema (hoy: enlace de recuperación
de contraseña). Modos por variable de entorno FACTURA_MAIL_MODE:

- "demo" (defecto en local): NO envía nada por red; escribe el mensaje en
  la bandeja de demostración local `.demo-bandeja/` dentro del proyecto
  (un archivo por mensaje). Esa carpeta NUNCA se versiona (ver .gitignore).
- "smtp": envía de verdad con smtplib usando la configuración del entorno:
  FACTURA_SMTP_HOST, FACTURA_SMTP_PORT (def. 587), FACTURA_SMTP_USER,
  FACTURA_SMTP_PASS, FACTURA_SMTP_FROM y FACTURA_SMTP_TLS (def. "1").

En producción se conecta el SMTP real vía esas variables; el código de la
app no cambia (ver docs/SEGURIDAD.md).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import config

BANDEJA_DEMO = os.path.join(config.BASE_DIR, ".demo-bandeja")


def modo() -> str:
    return os.environ.get("FACTURA_MAIL_MODE", "demo").strip().lower()


def _escribir_bandeja_demo(asunto: str, destinatario: str,
                           cuerpo: str) -> dict:
    os.makedirs(BANDEJA_DEMO, exist_ok=True)
    sello = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    # Nombre sin datos sensibles del destinatario (hash corto).
    import hashlib
    tag = hashlib.sha256(destinatario.encode("utf-8")).hexdigest()[:12]
    ruta = os.path.join(BANDEJA_DEMO, f"{sello}-{tag}.txt")
    with open(ruta, "w", encoding="utf-8") as fh:
        fh.write(f"Para: {destinatario}\nAsunto: {asunto}\n"
                 f"Fecha: {datetime.now(timezone.utc).isoformat()}\n\n"
                 f"{cuerpo}\n")
    os.chmod(ruta, 0o600)
    return {"ok": True, "modo": "demo", "ruta": ruta}


def _enviar_smtp(destinatario: str, asunto: str, cuerpo: str) -> dict:
    import smtplib
    from email.message import EmailMessage
    host = os.environ.get("FACTURA_SMTP_HOST", "")
    port = int(os.environ.get("FACTURA_SMTP_PORT", "587") or 587)
    user = os.environ.get("FACTURA_SMTP_USER", "")
    password = os.environ.get("FACTURA_SMTP_PASS", "")
    remitente = os.environ.get("FACTURA_SMTP_FROM", user)
    tls = os.environ.get("FACTURA_SMTP_TLS", "1") == "1"
    if not host or not remitente:
        raise RuntimeError("SMTP sin configurar: defina FACTURA_SMTP_HOST y "
                           "FACTURA_SMTP_FROM en el entorno")
    msg = EmailMessage()
    msg["From"] = remitente
    msg["To"] = destinatario
    msg["Subject"] = asunto
    msg.set_content(cuerpo)
    if tls:
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls()
            if user:
                s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=20) as s:
            if user:
                s.login(user, password)
            s.send_message(msg)
    return {"ok": True, "modo": "smtp"}


def enviar_enlace_recuperacion(destinatario: str, enlace: str) -> dict:
    """Envía el enlace de restablecimiento según FACTURA_MAIL_MODE.

    Devuelve {"ok": True, "modo": ...}. Nunca expone el enlace en logs.
    """
    asunto = "Restablece tu contraseña — TALENTO"
    cuerpo = (
        "Recibimos una solicitud para restablecer tu contraseña.\n\n"
        f"Enlace (válido 30 minutos, un solo uso):\n{enlace}\n\n"
        "Si no fuiste tú, ignora este mensaje.\n"
        "— TALENTO (prototipo, datos ficticios)")
    if modo() == "smtp":
        return _enviar_smtp(destinatario, asunto, cuerpo)
    return _escribir_bandeja_demo(asunto, destinatario, cuerpo)
