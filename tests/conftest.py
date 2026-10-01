"""Variables de entorno falsas para importar la app sin red.

`src/` lee su configuración al importarse (`getenv` lanza si falta una variable sin default),
así que esto tiene que correr antes de cualquier `import src...` o `import main`. Ningún test
abre la conexión a MongoDB ni al proveedor OIDC: eso ocurre en el `lifespan` de `main.py`,
que los tests no ejecutan.
"""
import os

FAKE_ENV = {
    "SESSION_SECRET": "test-session-secret",
    "MONGODB_URI": "mongodb://127.0.0.1:1",
    "PTERODACTYL_API_URL": "http://127.0.0.1:1/api/",
    "OIDC_CLIENT_ID": "test",
    "OIDC_CLIENT_SECRET": "test",
    "OIDC_METADATA_URL": "http://127.0.0.1:1/.well-known/openid-configuration",
}

for key, value in FAKE_ENV.items():
    os.environ[key] = value
