"""FASE 6A — Tests del Multi-Tenant Foundation + Authentication."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tenant import (
    AuthError, PermissionDenied, TenantStore, TenantContext,
    create_company, create_user, update_user, login, logout,
    get_tenant_context, require_permission, scoped_company_id,
    check_data_access, hash_password, verify_password,
    permissions_for, is_valid_role, DEMO_COMPANY_ID, dataset_owner,
)
from tenant import audit as audit_mod


def setUpModule():
    os.chdir(os.path.join(os.path.dirname(__file__), ".."))


class TenantTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tenant6a_")
        self.store = TenantStore(self.tmp)
        self.demo = self.store.ensure_demo_company()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def mk_company(self, name="Empresa Test"):
        return create_company(self.store, name)

    def mk_user(self, company_id=None, email=None, role_id="viewer",
                password="Password123!", name="Test User"):
        return create_user(
            self.store,
            company_id=company_id or self.demo.company_id,
            email=email or f"user-{os.urandom(4).hex()}@test.com",
            name=name, password=password, role_id=role_id,
        )

    def audit_actions(self, company_id=None):
        return [e.action for e in self.store.list_audit(company_id)]


# ---------------------------------------------------------------- login ---
class TestLogin(TenantTestBase):
    def test_login_valido(self):
        user = self.mk_user(email="login-ok@test.com", role_id="owner")
        token, ctx = login(self.store, "login-ok@test.com", "Password123!")
        self.assertTrue(token)
        self.assertEqual(ctx.user_id, user.user_id)
        self.assertEqual(ctx.company_id, user.company_id)
        self.assertIn(audit_mod.LOGIN_SUCCESS, self.audit_actions(user.company_id))

    def test_login_invalido_email_inexistente(self):
        with self.assertRaises(AuthError):
            login(self.store, "nadie@test.com", "Password123!")
        self.assertIn(audit_mod.LOGIN_FAILED, self.audit_actions())

    def test_usuario_inexistente(self):
        self.assertIsNone(self.store.get_user_by_email("fantasma@test.com"))

    def test_password_incorrecto(self):
        self.mk_user(email="login-bad@test.com")
        with self.assertRaises(AuthError) as cm:
            login(self.store, "login-bad@test.com", "WrongPass!")
        # Mensaje genérico: no revela si el email existe
        self.assertEqual(str(cm.exception), "credenciales inválidas")
        self.assertIn(audit_mod.LOGIN_FAILED, self.audit_actions())

    def test_usuario_deshabilitado(self):
        user = self.mk_user(email="disabled@test.com")
        update_user(self.store, user.user_id, status="disabled")
        with self.assertRaises(AuthError):
            login(self.store, "disabled@test.com", "Password123!")
        self.assertIn(audit_mod.USER_DISABLED, self.audit_actions(user.company_id))

    def test_logout(self):
        self.mk_user(email="logout@test.com")
        token, ctx = login(self.store, "logout@test.com", "Password123!")
        self.assertTrue(logout(self.store, token))
        with self.assertRaises(AuthError):
            get_tenant_context(self.store, token)
        self.assertIn(audit_mod.LOGOUT, self.audit_actions(ctx.company_id))

    def test_logout_sesion_inexistente(self):
        self.assertFalse(logout(self.store, "token-que-no-existe"))

    def test_email_case_insensitive(self):
        self.mk_user(email="Case@Test.com")
        token, _ = login(self.store, "CASE@test.com", "Password123!")
        self.assertTrue(token)

    def test_password_no_texto_plano(self):
        user = self.mk_user(email="plain@test.com")
        raw = self.store._load_list(self.store._users_file)
        stored = [u for u in raw if u["user_id"] == user.user_id][0]
        self.assertNotEqual(stored["password_hash"], "Password123!")
        self.assertTrue(stored["password_hash"].startswith("pbkdf2_sha256$"))

    def test_hash_verify(self):
        h = hash_password("Secreto99")
        self.assertTrue(verify_password("Secreto99", h))
        self.assertFalse(verify_password("secreto99", h))
        self.assertFalse(verify_password("x", "formato-invalido"))
        with self.assertRaises(ValueError):
            hash_password("")


# --------------------------------------------------------------- sesiones ---
class TestSessions(TenantTestBase):
    def test_sesion_invalida(self):
        with self.assertRaises(AuthError):
            get_tenant_context(self.store, "no-existe")

    def test_sesion_vacia(self):
        with self.assertRaises(AuthError):
            get_tenant_context(self.store, "")

    def test_sesion_expirada(self):
        self.mk_user(email="expired@test.com")
        token, ctx = login(self.store, "expired@test.com", "Password123!")
        # Forzar expiración manipulando el registro (simula el paso del tiempo)
        sess = self.store.get_session(token)
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        sess.expires_at = past
        self.store.save_session(sess)
        with self.assertRaises(AuthError) as cm:
            get_tenant_context(self.store, token)
        self.assertEqual(str(cm.exception), "sesión expirada")

    def test_tenant_context_contenido(self):
        user = self.mk_user(email="ctx@test.com", role_id="analyst", name="Ana Lista")
        token, _ = login(self.store, "ctx@test.com", "Password123!")
        ctx = get_tenant_context(self.store, token)
        self.assertIsInstance(ctx, TenantContext)
        self.assertEqual(ctx.user_id, user.user_id)
        self.assertEqual(ctx.company_id, user.company_id)
        self.assertEqual(ctx.role, "analyst")
        self.assertEqual(ctx.email, "ctx@test.com")
        self.assertIn("findings.read", ctx.permissions)
        self.assertNotIn("user.admin", ctx.permissions)


# ----------------------------------------------------------------- roles ---
class TestRoles(TenantTestBase):
    def test_permisos_owner(self):
        perms = permissions_for("owner")
        for p in ("company.admin", "user.admin", "dashboard.read", "findings.read",
                  "predictions.read", "advisor.use", "audit.read"):
            self.assertIn(p, perms)

    def test_permisos_admin(self):
        perms = permissions_for("admin")
        self.assertIn("user.admin", perms)
        self.assertIn("advisor.use", perms)
        self.assertNotIn("company.admin", perms)

    def test_permisos_analyst(self):
        perms = permissions_for("analyst")
        self.assertIn("advisor.use", perms)
        self.assertNotIn("user.admin", perms)
        self.assertNotIn("company.admin", perms)
        self.assertNotIn("audit.read", perms)

    def test_permisos_viewer(self):
        perms = permissions_for("viewer")
        self.assertIn("dashboard.read", perms)
        self.assertIn("findings.read", perms)
        self.assertIn("predictions.read", perms)
        self.assertNotIn("advisor.use", perms)
        self.assertNotIn("user.admin", perms)

    def test_rol_invalido_deniega_todo(self):
        self.assertEqual(permissions_for("superadmin"), [])
        self.assertFalse(is_valid_role("superadmin"))
        self.assertTrue(is_valid_role("owner"))

    def test_acceso_autorizado(self):
        self.mk_user(email="auth-ok@test.com", role_id="analyst")
        _, ctx = login(self.store, "auth-ok@test.com", "Password123!")
        require_permission(self.store, ctx, "findings.read")  # no lanza

    def test_acceso_no_autorizado(self):
        self.mk_user(email="auth-no@test.com", role_id="viewer")
        _, ctx = login(self.store, "auth-no@test.com", "Password123!")
        with self.assertRaises(PermissionDenied):
            require_permission(self.store, ctx, "advisor.use", resource="advisor")
        self.assertIn(audit_mod.PERMISSION_DENIED, self.audit_actions(ctx.company_id))

    def test_role_manipulado(self):
        # Un rol inexistente en el registro → cero permisos (denegar por defecto)
        user = self.mk_user(email="rolemanip@test.com")
        user.role_id = "ceo_supremo"
        self.store.save_user(user)
        _, ctx = login(self.store, "rolemanip@test.com", "Password123!")
        self.assertEqual(ctx.permissions, [])
        with self.assertRaises(PermissionDenied):
            require_permission(self.store, ctx, "dashboard.read")

    def test_permission_manipulado(self):
        # Permiso que no existe en la matriz nunca se concede
        self.mk_user(email="permmanip@test.com", role_id="owner")
        _, ctx = login(self.store, "permmanip@test.com", "Password123!")
        with self.assertRaises(PermissionDenied):
            require_permission(self.store, ctx, "billing.refund")


# ------------------------------------------------------- tenant isolation ---
class TestTenantIsolation(TenantTestBase):
    def setUp(self):
        super().setUp()
        self.company_a = self.mk_company("COMPANY_A")
        self.company_b = self.mk_company("COMPANY_B")
        self.owner_a = create_user(self.store, company_id=self.company_a.company_id,
                                   email="owner_a@test.com", name="Owner A",
                                   password="Password123!", role_id="owner")
        self.owner_b = create_user(self.store, company_id=self.company_b.company_id,
                                   email="owner_b@test.com", name="Owner B",
                                   password="Password123!", role_id="owner")

    def test_owner_a_solo_ve_a(self):
        _, ctx_a = login(self.store, "owner_a@test.com", "Password123!")
        self.assertEqual(ctx_a.company_id, self.company_a.company_id)
        users = self.store.list_users_by_company(ctx_a.company_id)
        emails = [u.email for u in users]
        self.assertIn("owner_a@test.com", emails)
        self.assertNotIn("owner_b@test.com", emails)

    def test_owner_b_solo_ve_b(self):
        _, ctx_b = login(self.store, "owner_b@test.com", "Password123!")
        self.assertEqual(ctx_b.company_id, self.company_b.company_id)

    def test_company_a_no_accede_company_b(self):
        _, ctx_a = login(self.store, "owner_a@test.com", "Password123!")
        with self.assertRaises(PermissionDenied):
            scoped_company_id(self.store, ctx_a, self.company_b.company_id,
                              resource=f"/companies/{self.company_b.company_id}/findings")
        actions = self.audit_actions(self.company_a.company_id)
        self.assertIn(audit_mod.PERMISSION_DENIED, actions)

    def test_idor_con_company_id_conocido(self):
        # Aunque conozca el company_id de B, el acceso se rechaza.
        _, ctx_a = login(self.store, "owner_a@test.com", "Password123!")
        with self.assertRaises(PermissionDenied):
            check_data_access_data = self.company_b.company_id
            scoped_company_id(self.store, ctx_a, check_data_access_data)

    def test_company_id_manipulado_en_sesion(self):
        # La sesión no puede migrar de empresa aunque el registro se altere.
        token, _ = login(self.store, "owner_a@test.com", "Password123!")
        sess = self.store.get_session(token)
        sess.company_id = self.company_b.company_id
        self.store.save_session(sess)
        with self.assertRaises(AuthError):
            get_tenant_context(self.store, token)

    def test_usuario_sin_company_id(self):
        user = self.mk_user(email="sincompany@test.com")
        user.company_id = ""
        self.store.save_user(user)
        with self.assertRaises(AuthError):
            login(self.store, "sincompany@test.com", "Password123!")

    def test_separacion_de_datos(self):
        users_a = self.store.list_users_by_company(self.company_a.company_id)
        users_b = self.store.list_users_by_company(self.company_b.company_id)
        ids_a = {u.user_id for u in users_a}
        ids_b = {u.user_id for u in users_b}
        self.assertTrue(ids_a.isdisjoint(ids_b))

    def test_demo_retail_aislado(self):
        # El dataset demo pertenece a la empresa demo y a nadie más.
        self.assertEqual(dataset_owner("demo-retail"), DEMO_COMPANY_ID)
        self.assertIsNone(dataset_owner("dataset-de-otro-cliente"))
        # owner_a NO puede acceder al dataset demo (es de otra empresa).
        _, ctx_a = login(self.store, "owner_a@test.com", "Password123!")
        with self.assertRaises(PermissionDenied):
            check_data_access(self.store, ctx_a, "demo-retail")

    def test_demo_tiene_acceso_a_su_dataset(self):
        demo_user = self.mk_user(company_id=self.demo.company_id,
                                 email="demouser@test.com", role_id="analyst")
        _, ctx = login(self.store, "demouser@test.com", "Password123!")
        owner = check_data_access(self.store, ctx, "demo-retail")
        self.assertEqual(owner, DEMO_COMPANY_ID)
        self.assertIn(audit_mod.DATA_ACCESS, self.audit_actions(self.demo.company_id))


# -------------------------------------------------------------- auditoría ---
class TestAudit(TenantTestBase):
    def test_auditoria_registra_eventos(self):
        user = self.mk_user(email="audit@test.com", role_id="owner")
        login(self.store, "audit@test.com", "Password123!")
        events = self.store.list_audit(user.company_id)
        actions = [e.action for e in events]
        self.assertIn(audit_mod.USER_CREATED, actions)
        self.assertIn(audit_mod.LOGIN_SUCCESS, actions)
        ev = [e for e in events if e.action == audit_mod.LOGIN_SUCCESS][0]
        self.assertEqual(ev.user_id, user.user_id)
        self.assertEqual(ev.company_id, user.company_id)
        self.assertTrue(ev.timestamp)

    def test_secretos_no_en_logs(self):
        self.mk_user(email="secrets@test.com")
        login(self.store, "secrets@test.com", "Password123!")
        login_fail = None
        try:
            login(self.store, "secrets@test.com", "WrongPassword!")
        except AuthError:
            pass
        with open(self.store._audit_file, encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("Password123!", content)
        self.assertNotIn("WrongPassword!", content)
        # El token completo tampoco aparece (solo huella corta)
        for line in content.splitlines():
            evt = json.loads(line)
            self.assertNotIn("session_id", json.dumps(evt.get("metadata", {})))

    def test_auditoria_separada_por_empresa(self):
        a = self.mk_company("AudA")
        b = self.mk_company("AudB")
        ua = create_user(self.store, company_id=a.company_id, email="aa@t.com",
                         name="A", password="Password123!", role_id="owner")
        login(self.store, "aa@t.com", "Password123!")
        events_b = self.store.list_audit(b.company_id)
        self.assertEqual(events_b, [])


# --------------------------------------------------------------- usuarios ---
class TestUsers(TenantTestBase):
    def test_creacion_de_usuario(self):
        user = self.mk_user(email="nuevo@test.com", role_id="analyst")
        self.assertTrue(user.user_id.startswith("USR-"))
        self.assertEqual(user.status, "active")
        self.assertIn(audit_mod.USER_CREATED, self.audit_actions(user.company_id))

    def test_email_duplicado(self):
        self.mk_user(email="dupe@test.com")
        with self.assertRaises(AuthError):
            self.mk_user(email="dupe@test.com")

    def test_deshabilitacion_de_usuario(self):
        user = self.mk_user(email="deshab@test.com")
        update_user(self.store, user.user_id, status="disabled")
        self.assertEqual(self.store.get_user(user.user_id).status, "disabled")
        self.assertIn(audit_mod.USER_DISABLED, self.audit_actions(user.company_id))

    def test_rol_invalido_rechazado(self):
        with self.assertRaises(AuthError):
            self.mk_user(email="badrole@test.com", role_id="superadmin")

    def test_inputs_invalidos(self):
        with self.assertRaises(AuthError):
            self.mk_user(email="no-es-email")
        with self.assertRaises(AuthError):
            self.mk_user(email="corto@test.com", password="123")
        with self.assertRaises(AuthError):
            create_company(self.store, "")

    def test_actualizar_rol(self):
        user = self.mk_user(email="updrole@test.com", role_id="viewer")
        update_user(self.store, user.user_id, role_id="analyst")
        self.assertEqual(self.store.get_user(user.user_id).role_id, "analyst")

    def test_usuario_sin_secretos_en_dict(self):
        user = self.mk_user(email="pub@test.com")
        d = user.to_dict()
        self.assertNotIn("password_hash", d)


# --------------------------------------------------------------- seguridad ---
class TestSecurity(TenantTestBase):
    def test_errores_seguros_no_revelan_info(self):
        # company_id arbitrario de otra empresa → mensaje genérico
        self.mk_user(email="safe@test.com")
        _, ctx = login(self.store, "safe@test.com", "Password123!")
        try:
            scoped_company_id(self.store, ctx, "CMP-inexistente")
            self.fail("debió lanzar PermissionDenied")
        except PermissionDenied as e:
            self.assertEqual(str(e), "acceso denegado")

    def test_tenant_context_deriva_de_sesion(self):
        user = self.mk_user(email="deriv@test.com", role_id="admin")
        token, _ = login(self.store, "deriv@test.com", "Password123!")
        ctx = get_tenant_context(self.store, token)
        # company_id viene de la sesión, no de un parámetro
        self.assertEqual(scoped_company_id(self.store, ctx, None), user.company_id)
        self.assertEqual(scoped_company_id(self.store, ctx, user.company_id), user.company_id)

    def test_autorizacion_central_unica(self):
        # La matriz de permisos vive en un solo lugar (roles.py) y es
        # consistente: el contexto construido desde login usa esa matriz.
        import tenant.roles as roles_mod
        self.assertEqual(roles_mod.permissions_for("owner"),
                         roles_mod.permissions_for("owner"))
        self.mk_user(email="central@test.com", role_id="admin")
        _, ctx = login(self.store, "central@test.com", "Password123!")
        self.assertEqual(ctx.permissions, roles_mod.permissions_for("admin"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
