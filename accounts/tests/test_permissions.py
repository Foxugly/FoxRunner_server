from django.test import TestCase
from ninja.errors import HttpError

from accounts.models import User
from accounts.permissions import require_self_or_superuser, require_superuser, require_user_scope, resolve_user


class PermissionsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(email="alice@x.com", password="x")
        cls.admin = User.objects.create_superuser(email="admin@x.com", password="x")

    def test_resolve_user_by_uuid(self):
        self.assertEqual(resolve_user(str(self.alice.id)).email, "alice@x.com")

    def test_resolve_user_by_email(self):
        self.assertEqual(resolve_user("alice@x.com").id, self.alice.id)

    def test_resolve_user_unknown_raises_404(self):
        with self.assertRaises(HttpError) as ctx:
            resolve_user("ghost@x.com")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_require_superuser_allows_admin(self):
        require_superuser(self.admin)  # no raise

    def test_require_superuser_denies_normal(self):
        with self.assertRaises(HttpError) as ctx:
            require_superuser(self.alice)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_require_user_scope_allows_self_uuid(self):
        require_user_scope(str(self.alice.id), self.alice)

    def test_require_user_scope_allows_self_email(self):
        require_user_scope("alice@x.com", self.alice)

    def test_require_user_scope_allows_admin_for_other(self):
        require_user_scope(str(self.alice.id), self.admin)

    def test_require_user_scope_denies_other(self):
        bob = User.objects.create_user(email="bob@x.com", password="x")
        with self.assertRaises(HttpError) as ctx:
            require_user_scope(str(self.alice.id), bob)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_require_self_or_superuser_self(self):
        require_self_or_superuser(self.alice, self.alice)

    def test_require_self_or_superuser_admin(self):
        require_self_or_superuser(self.admin, self.alice)

    def test_require_self_or_superuser_other_denied(self):
        bob = User.objects.create_user(email="bob@x.com", password="x")
        with self.assertRaises(HttpError) as ctx:
            require_self_or_superuser(bob, self.alice)
        self.assertEqual(ctx.exception.status_code, 403)
