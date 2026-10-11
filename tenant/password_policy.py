"""SEG-05 — Política de contraseñas para NUEVAS credenciales.

- Mínimo 12 caracteres para contraseñas nuevas o modificadas.
- Máximo 128 caracteres (protege recursos del servidor sin penalizar
  frases de contraseña largas).
- Se permiten espacios y Unicode: la contraseña se trata como texto
  UTF-8 opaco, sin reglas arbitrarias de composición (mayúsculas,
  números, símbolos). La longitud es la defensa.
- La política aplica SOLO a creación/cambio. Las contraseñas heredadas
  (8–11 caracteres) siguen verificándose sin cambios: no se
  invalidan, no se migran, no se tocan sus hashes.

Validación centralizada: todo punto de creación (create_user,
scripts administrativos) usa validate_new_password(). La verificación
de credenciales existentes (verify_password) no aplica esta política.
"""

from __future__ import annotations

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128


def validate_new_password(password) -> list[str]:
    """Devuelve la lista de problemas (vacía = válida).

    Mensajes útiles para mostrar al operador, sin datos internos.
    """
    if not isinstance(password, str) or not password:
        return ["la contraseña no puede estar vacía"]
    if len(password) < MIN_PASSWORD_LENGTH:
        return [f"la contraseña debe tener al menos {MIN_PASSWORD_LENGTH} "
                f"caracteres"]
    if len(password) > MAX_PASSWORD_LENGTH:
        return [f"la contraseña no puede superar {MAX_PASSWORD_LENGTH} "
                f"caracteres"]
    return []
