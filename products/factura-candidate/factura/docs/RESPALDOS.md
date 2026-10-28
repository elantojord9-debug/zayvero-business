> Referencia histórica de desarrollo local. Para este candidato prevalecen `../../OPERACION-LANZAMIENTO.md` e `../../INFORME-LANZAMIENTO.md`; las rutas, roles y afirmaciones de producción local de abajo no acreditan un servicio comercial activo.

# Custodia de la contraseña de descifrado del respaldo operativo

**Válido para:** `factura-operativo-*.tar.gz.enc` (copia operativa cifrada
AES-256-CBC PBKDF2).

La contraseña de descifrado **nunca** se publica, nunca va en el chat,
nunca va en un informe, nunca va en el repositorio y nunca viaja junto
al archivo cifrado. Quien tenga el ZIP y la contraseña tiene los datos;
quien tenga solo uno, no tiene nada.

## Dónde guardarla (fuera de la VM)

El propietario elige UNA de estas opciones:

1. **Gestor de contraseñas** (recomendado): crear una entrada llamada
   `factura-operativo-descifrado` en su gestor (Bitwarden, 1Password,
   KeePass, el llavero del teléfono). La contraseña se copia a mano una
   sola vez desde el medio de entrega y se verifica descifrando.

2. **Medio privado controlado por el propietario**: papel guardado en
   lugar seguro, o archivo en un USB cifrado que no salga de su poder.

## Qué NO hacer

- No guardarla en el mismo lugar que el ZIP cifrado.
- No enviarla por WhatsApp, correo sin cifrar ni redes sociales.
- No pegarla en documentos compartidos ni en el código.
- No reutilizarla para otras cuentas.

## Rotación

Si la contraseña pudo quedar expuesta (se pegó en un chat, se envió
por un canal inseguro, la vio alguien no autorizado):

1. Generar una contraseña nueva y fuerte (mínimo 32 caracteres
   aleatorios; el generador del gestor de contraseñas sirve).
2. Re-cifrar la copia operativa con la nueva contraseña:
   `openssl enc -e -aes-256-cbc -pbkdf2 -in operativo.tar.gz -out nuevo.enc`
3. Guardar la nueva en el gestor (opción 1) y destruir la vieja de
   todos los medios donde quedó.

## Verificación

Para comprobar que la contraseña guardada es la correcta, descifrar a
una carpeta temporal y comparar el SHA-256 del `.enc` con el manifiesto:

    openssl enc -d -aes-256-cbc -pbkdf2 -in factura-operativo-*.tar.gz.enc \
        -out /tmp/operativo.tar.gz
    sha256sum factura-operativo-*.tar.gz.enc   # debe coincidir con el manifiesto
