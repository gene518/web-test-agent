"""更新 HTTP API 的鉴权、Origin 和 CSRF 集成测试。"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from types import SimpleNamespace

from service import UpdateRequestHandler


INTERNAL_TOKEN = "i" * 32
ORIGIN = "http://example.test"


class FakeController:
    def __init__(self) -> None:
        self.config = SimpleNamespace(
            internal_token=INTERNAL_TOKEN,
            csrf_secret=b"c" * 32,
            public_origin=ORIGIN,
        )

    @staticmethod
    def gate_open() -> bool:
        return True

    @staticmethod
    def check_latest(*, force: bool = False):  # noqa: ANN201
        return {"has_update": True, "force": force}

    @staticmethod
    def operation(operation_id: str):  # noqa: ANN201
        return {"operation_id": operation_id, "status": "running"}

    @staticmethod
    def begin_update():  # noqa: ANN201
        return {
            "operation_id": "a" * 32,
            "status": "queued",
            "phase": "waiting_for_idle",
        }


class UpdaterApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        UpdateRequestHandler.controller = FakeController()
        from http.server import ThreadingHTTPServer

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), UpdateRequestHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict, object]:
        request = urllib.request.Request(
            f"{self.base_url}{path}", method=method, headers=headers or {}
        )
        try:
            response = urllib.request.urlopen(request, timeout=3)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc), exc.headers
        with response:
            return response.status, json.load(response), response.headers

    def test_read_endpoints_require_internal_proxy_token(self) -> None:
        status, _, _ = self.request("/status")
        self.assertEqual(status, 401)
        status, payload, _ = self.request(
            "/check", headers={"X-Internal-Token": INTERNAL_TOKEN}
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["force"])

    def test_apply_requires_matching_origin_cookie_and_csrf_header(self) -> None:
        internal = {"X-Internal-Token": INTERNAL_TOKEN}
        status, _, _ = self.request("/apply", method="POST", headers=internal)
        self.assertEqual(status, 403)

        status, csrf_payload, response_headers = self.request("/csrf", headers=internal)
        self.assertEqual(status, 200)
        token = csrf_payload["token"]
        set_cookie = response_headers.get("Set-Cookie")
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("SameSite=Strict", set_cookie)
        self.assertEqual(response_headers.get("Cache-Control"), "no-store")
        cookie = set_cookie.split(";", 1)[0]
        status, operation, _ = self.request(
            "/apply",
            method="POST",
            headers={
                **internal,
                "Origin": ORIGIN,
                "Cookie": cookie,
                "X-CSRF-Token": token,
            },
        )
        self.assertEqual(status, 202)
        self.assertEqual(operation["operation_id"], "a" * 32)


if __name__ == "__main__":
    unittest.main()
