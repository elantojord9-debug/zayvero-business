"""Tests de configuración de autenticación segura.

Verifican la remediación de seguridad de data/tenant/users.json:
- Resolución de la ruta por ZAYVERO_USERS_FILE > /etc/secrets > local.
- Comportamiento seguro cuando el archivo no existe (arranca vacío, nadie entra).
- users.json en .gitignore y no trackeado en git.
- Ningún archivo trackeado contiene hashes (patrón pbkdf2).
- Rotación: hash nuevo verifica, hash viejo no.
- Permisos 600 al guardar. Cuentas demo limitadas. Aislamiento intacto.
"""
import json
import logging
import os
import stat
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tenant import TenantStore, create_user, login, AuthError, create_company
from tenant import store as store_mod
from tenant.crypto import hash_password, verify_password


def setUpModule():
    os.chdir(os.path.join(os.path.dirname(__file__), ".."))


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")


def _store_with_company(tmp_prefix):
    """Store temporal con una empresa válida para crear usuarios."""
    tmp = tempfile.mkdtemp(prefix=tmp_prefix)
    store = TenantStore(tmp)
    company = create_company(store, name="Empresa Prueba", is_demo=True)
    return store, company.company_id


class UsersFileResolutionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="authcfg_")
        self._old_env = os.environ.get("ZAYVERO_USERS_FILE")

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("ZAYVERO_USERS_FILE", None)
        else:
            os.environ["ZAYVERO_USERS_FILE"] = self._old_env

    def test_env_var_takes_priority(self):
        alt = os.path.join(self.tmp, "alt-users.json")
        with open(alt, "w") as f:
            json.dump([], f)
        os.environ["ZAYVERO_USERS_FILE"] = alt
        store = TenantStore(os.path.join(self.tmp, "data"))
        self.assertEqual(store._users_file, alt)
        self.assertEqual(store._users_file_origin, "env")

    def test_login_works_against_env_file(self):
        alt = os.path.join(self.tmp, "secret-users.json")
        with open(alt, "w") as f:
            json.dump([], f)
        os.environ["ZAYVERO_USERS_FILE"] = alt
        store, company_id = _store_with_company("loginenv_")
        # Re-resolver: el store ya apunta al archivo del env var.
        self.assertEqual(store._users_file, alt)
        create_user(store, company_id=company_id, email="t@t.do",
                    name="T", password="clave-segura-123", role_id="viewer")
        # La misma credencial funciona contra el archivo del env var.
        token, ctx = login(store, "t@t.do", "clave-segura-123")
        self.assertTrue(token)
        self.assertEqual(ctx.email, "t@t.do")
        # Y la contraseña vieja/incorrecta no entra.
        with self.assertRaises(AuthError):
            login(store, "t@t.do", "otra-clave")

    def test_missing_file_starts_empty_with_warning(self):
        missing = os.path.join(self.tmp, "no-existe", "users.json")
        os.environ["ZAYVERO_USERS_FILE"] = missing
        with self.assertLogs("tenant.store", level="WARNING") as cm:
            store = TenantStore(os.path.join(self.tmp, "data"))
        self.assertEqual(store.list_users(), [])
        self.assertTrue(any("users.json no encontrado" in m for m in cm.output))
        # Nadie puede entrar.
        with self.assertRaises(AuthError):
            login(store, "nadie@t.do", "x")

    def test_resolve_function_priority(self):
        data_dir = os.path.join(self.tmp, "d")
        # Sin env y sin /etc/secrets/users.json -> local (en este entorno no existe).
        if not os.path.exists("/etc/secrets/users.json"):
            path, origin = store_mod.resolve_users_file(data_dir)
            self.assertEqual(origin, "local")
            self.assertTrue(path.endswith("users.json"))
        os.environ["ZAYVERO_USERS_FILE"] = "/tmp/x.json"
        path, origin = store_mod.resolve_users_file(data_dir)
        self.assertEqual((path, origin), ("/tmp/x.json", "env"))

    def test_chmod_600_on_save(self):
        alt = os.path.join(self.tmp, "u.json")
        with open(alt, "w") as f:
            json.dump([], f)
        os.environ["ZAYVERO_USERS_FILE"] = alt
        store, company_id = _store_with_company("chmod_")
        create_user(store, company_id=company_id, email="p@t.do",
                    name="P", password="clave-segura-123", role_id="viewer")
        mode = stat.S_IMODE(os.stat(store._users_file).st_mode)
        self.assertEqual(mode, 0o600)


class RepoHygieneTest(unittest.TestCase):
    def test_users_json_in_gitignore(self):
        with open(os.path.join(REPO_ROOT, ".gitignore"), encoding="utf-8") as f:
            content = f.read()
        self.assertIn("data/tenant/users.json", content)

    def test_users_json_not_tracked(self):
        r = subprocess.run(
            ["git", "ls-files", "data/tenant/users.json"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.stdout.strip(), "")

    def test_no_hash_pattern_in_tracked_files(self):
        # Busca el formato REAL de hash (pbkdf2_sha256$iter$salt$hash),
        # no las menciones en código/comentarios. Excluye tests/ (fixtures).
        r = subprocess.run(
            ["git", "grep", "-l", r"pbkdf2_sha256\$[0-9]\+\$[0-9a-f]\+\$[0-9a-f]\+",
             "--", ".", ":(exclude)tests/"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=60)
        # git grep sale 1 si no hay coincidencias: eso es lo esperado.
        self.assertEqual(r.returncode, 1, f"archivos con hashes: {r.stdout}")
        self.assertEqual(r.stdout.strip(), "")


class RotationTest(unittest.TestCase):
    def test_new_hash_verifies_old_does_not(self):
        old_hash = hash_password("clave-vieja-12345")
        new_hash = hash_password("clave-nueva-67890")
        self.assertTrue(verify_password("clave-nueva-67890", new_hash))
        self.assertFalse(verify_password("clave-vieja-12345", new_hash))
        self.assertTrue(verify_password("clave-vieja-12345", old_hash))

    def test_pending_marker_never_verifies(self):
        self.assertFalse(verify_password("cualquier-cosa", "PENDING_ROTATION_BY_OWNER"))
        self.assertFalse(verify_password("", "PENDING_ROTATION_BY_OWNER"))

    def test_rotation_flow_against_store(self):
        tmp = tempfile.mkdtemp(prefix="rot_")
        alt = os.path.join(tmp, "users.json")
        with open(alt, "w") as f:
            json.dump([], f)
        os.environ["ZAYVERO_USERS_FILE"] = alt
        try:
            store = TenantStore(os.path.join(tmp, "data"))
            company = create_company(store, name="Empresa Rot", is_demo=True)
            create_user(store, company_id=company.company_id, email="r@t.do",
                        name="R", password="vieja-12345678", role_id="viewer")
            login(store, "r@t.do", "vieja-12345678")  # entra con la vieja
            # Rotación: se reemplaza el hash (como haría rotate_owner_password.py).
            import dataclasses
            u2 = store.get_user_by_email("r@t.do")
            u3 = dataclasses.replace(u2, password_hash=hash_password("nueva-12345678"))
            store.save_user(u3)
            login(store, "r@t.do", "nueva-12345678")  # entra con la nueva
            with self.assertRaises(AuthError):
                login(store, "r@t.do", "vieja-12345678")  # la vieja ya no entra
        finally:
            os.environ.pop("ZAYVERO_USERS_FILE", None)


class DemoPermissionsTest(unittest.TestCase):
    def test_demo_accounts_are_limited(self):
        # Las cuentas demo no deben ser admin con permisos amplios:
        # viewer@ es viewer; demo@ es owner de la empresa demo únicamente.
        store, company_id = _store_with_company("perm_")
        create_user(store, company_id=company_id, email="viewer@t.do",
                    name="V", password="clave-segura-123", role_id="viewer")
        v = store.get_user_by_email("viewer@t.do")
        self.assertEqual(v.role_id, "viewer")
        self.assertNotEqual(v.role_id, "admin")
        self.assertEqual(v.company_id, company_id)

    def test_tenant_isolation_intact(self):
        # Dos empresas: un usuario de A no aparece en B.
        store, company_id = _store_with_company("iso_")
        create_user(store, company_id=company_id, email="a@t.do",
                    name="A", password="clave-segura-123", role_id="viewer")
        self.assertIsNotNone(store.get_user_by_email("a@t.do"))
        users_a = store.list_users_by_company(company_id)
        users_b = store.list_users_by_company("empresa-b-inexistente")
        self.assertEqual(len(users_a), 1)
        self.assertEqual(len(users_b), 0)


class RequireSecretFileGuardTest(unittest.TestCase):
    """ZAYVERO_REQUIRE_SECRET_FILE=1 impide el fallback silencioso a la
    copia local del repo: si el origen resuelto es "local", el arranque aborta."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="reqsf_")
        self._old_req = os.environ.get("ZAYVERO_REQUIRE_SECRET_FILE")
        self._old_file = os.environ.get("ZAYVERO_USERS_FILE")

    def tearDown(self):
        for k, v in (("ZAYVERO_REQUIRE_SECRET_FILE", self._old_req),
                     ("ZAYVERO_USERS_FILE", self._old_file)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_local_fallback_aborts_when_required(self):
        os.environ["ZAYVERO_REQUIRE_SECRET_FILE"] = "1"
        os.environ.pop("ZAYVERO_USERS_FILE", None)
        with self.assertRaises(RuntimeError):
            TenantStore(os.path.join(self.tmp, "data"))

    def test_env_origin_still_allowed_when_required(self):
        alt = os.path.join(self.tmp, "alt-users.json")
        with open(alt, "w") as f:
            json.dump([], f)
        os.environ["ZAYVERO_REQUIRE_SECRET_FILE"] = "1"
        os.environ["ZAYVERO_USERS_FILE"] = alt
        store = TenantStore(os.path.join(self.tmp, "data"))
        self.assertEqual(store._users_file_origin, "env")

    def test_guard_off_by_default(self):
        os.environ.pop("ZAYVERO_REQUIRE_SECRET_FILE", None)
        os.environ.pop("ZAYVERO_USERS_FILE", None)
        store = TenantStore(os.path.join(self.tmp, "data"))
        # Sin el guard, el fallback local existe (comportamiento de desarrollo).
        self.assertEqual(store._users_file_origin, "local")


if __name__ == "__main__":
    unittest.main()
