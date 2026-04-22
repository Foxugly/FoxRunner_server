from django.test import TestCase

from foxrunner.pagination import PageQuery, page_response, paginate


class PaginationTest(TestCase):
    def test_page_response_envelope(self):
        result = page_response([{"a": 1}], total=10, limit=5, offset=0)
        self.assertEqual(result, {"items": [{"a": 1}], "total": 10, "limit": 5, "offset": 0})

    def test_paginate_clamps_limit_above_500(self):
        from accounts.models import User

        for i in range(3):
            User.objects.create_user(email=f"u{i}@x.com", password="x")
        qs = User.objects.all().order_by("email")
        result = paginate(qs, page=PageQuery(limit=10000, offset=0), serialize=lambda u: u.email)
        self.assertEqual(result["limit"], 500)
        self.assertEqual(result["total"], 3)
        self.assertEqual(len(result["items"]), 3)

    def test_paginate_negative_offset_clamped_to_zero(self):
        from accounts.models import User

        User.objects.create_user(email="a@x.com", password="x")
        qs = User.objects.all()
        result = paginate(qs, page=PageQuery(limit=10, offset=-5), serialize=lambda u: u.email)
        self.assertEqual(result["offset"], 0)
        self.assertEqual(len(result["items"]), 1)
