import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import app as backend_app
from pairing import PairingError, PairingManager


ORIGIN = "https://chordsense.vercel.app"


class PairingManagerTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.wall = 1_700_000_000.0
        self.manager = PairingManager(clock=lambda: self.now, wall_clock=lambda: self.wall)

    def assert_claim_error(self, code, status):
        with self.assertRaises(PairingError) as error:
            self.manager.claim(code, ORIGIN)
        self.assertEqual(error.exception.status, status)

    def test_six_digit_codes_preserve_leading_zeros_and_consume_once(self):
        with patch("pairing.secrets.randbelow", return_value=42):
            started = self.manager.start()
        self.assertEqual(started["code"], "000042")
        self.assertEqual(started["expires_in"], 120)
        self.assert_claim_error(42, 400)
        claimed = self.manager.claim("000042", ORIGIN)
        self.assertEqual(len(claimed["token"]), 43)
        self.assertEqual(claimed["permissions"], ["uploads"])
        self.assertTrue(self.manager.authenticate(claimed["token"], ORIGIN))
        self.assertFalse(self.manager.authenticate(claimed["token"], "https://other.vercel.app"))
        self.assertNotIn(claimed["token"], repr(self.manager._sessions))
        self.assertIsNone(self.manager.status()["code"])
        self.assert_claim_error("000042", 409)

    def test_pairing_and_session_expiry_use_monotonic_time(self):
        code = self.manager.start()["code"]
        self.wall += 1_000_000
        self.now += 119
        self.assertEqual(self.manager.status()["expires_in"], 1)
        self.now += 1
        self.assert_claim_error(code, 410)
        self.assertEqual(self.manager.status()["state"], "expired")
        token = self.manager.claim(self.manager.start()["code"], ORIGIN)["token"]
        self.wall -= 2_000_000
        self.now += 28_799
        self.assertTrue(self.manager.authenticate(token, ORIGIN))
        self.now += 1
        self.assertFalse(self.manager.authenticate(token, ORIGIN))
        self.assertEqual(self.manager.status()["session_count"], 0)

    def test_five_global_wrong_attempts_lock_even_the_correct_code(self):
        code = self.manager.start()["code"]
        wrong = "000000" if code != "000000" else "000001"
        for _ in range(4):
            self.assert_claim_error(wrong, 401)
        self.assert_claim_error(wrong, 429)
        self.assert_claim_error(code, 429)
        self.assertEqual(self.manager.status()["attempts_remaining"], 0)
        self.assertIsNone(self.manager.status()["code"])
        self.assertTrue(self.manager.claim(self.manager.start()["code"], ORIGIN)["token"])

    def test_concurrent_claims_issue_exactly_one_session(self):
        code = self.manager.start()["code"]
        ready = threading.Barrier(8)

        def claim():
            ready.wait(timeout=2)
            try:
                return self.manager.claim(code, ORIGIN)
            except PairingError as error:
                return error.status

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: claim(), range(8)))
        successes = [result for result in results if isinstance(result, dict)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(results.count(409), 7)
        self.assertEqual(self.manager.status()["session_count"], 1)

    def test_concurrent_wrong_attempts_share_one_limit(self):
        code = self.manager.start()["code"]
        wrong = "000000" if code != "000000" else "000001"
        ready = threading.Barrier(8)

        def claim():
            ready.wait(timeout=2)
            try:
                self.manager.claim(wrong, ORIGIN)
            except PairingError as error:
                return error.status

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: claim(), range(8)))
        self.assertEqual(results.count(401), 4)
        self.assertEqual(results.count(429), 4)
        self.assertEqual(self.manager.status()["attempts_remaining"], 0)

    def test_cancel_and_new_code_keep_existing_sessions(self):
        existing = self.manager.claim(self.manager.start()["code"], ORIGIN)["token"]
        with patch("pairing.secrets.randbelow", side_effect=[1, 2]):
            self.manager.start()
            self.manager.start()
        self.assert_claim_error("000001", 401)
        self.manager.cancel()
        self.assert_claim_error("000002", 409)
        self.assertTrue(self.manager.authenticate(existing, ORIGIN))

    def test_session_limit_and_expiry_bound_memory(self):
        self.manager = PairingManager(clock=lambda: self.now, max_sessions=2)
        tokens = [self.manager.claim(self.manager.start()["code"], ORIGIN)["token"] for _ in range(2)]
        with self.assertRaises(PairingError) as error:
            self.manager.start()
        self.assertEqual(error.exception.status, 503)
        self.assertEqual(self.manager.status()["session_count"], 2)
        self.manager.logout(tokens[0], ORIGIN)
        self.assertFalse(self.manager.authenticate(tokens[0], ORIGIN))
        self.assertTrue(self.manager.authenticate(tokens[1], ORIGIN))
        self.manager.claim(self.manager.start()["code"], ORIGIN)
        self.now += 28_800
        self.assertEqual(self.manager.start()["session_count"], 0)

    def test_revoke_all_invalidates_sessions_and_pending_code(self):
        tokens = [self.manager.claim(self.manager.start()["code"], ORIGIN)["token"] for _ in range(2)]
        code = self.manager.start()["code"]
        self.assertEqual(self.manager.revoke_all()["revoked"], 2)
        for token in tokens:
            self.assertFalse(self.manager.authenticate(token, ORIGIN))
        self.assert_claim_error(code, 409)


class PairingRouteTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.manager = PairingManager(clock=lambda: self.now)
        self.environment = patch.dict(os.environ, {
            "CHORDSENSE_ALLOWED_ORIGINS": ORIGIN,
            "CHORDSENSE_COMPANION_URL": ORIGIN,
            "CHORDSENSE_DEVICE_TOKEN": "legacy-manual-token",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        manager = patch.dict(backend_app.app.extensions, {"chordsense_pairing": self.manager})
        manager.start()
        self.addCleanup(manager.stop)
        self.client = backend_app.app.test_client()

    def pair(self):
        started = self.client.post("/api/pairing/start")
        self.assertEqual(started.status_code, 200)
        response = self.client.post("/api/pairing/claim", json={"code": started.get_json()["code"]}, headers={"Origin": ORIGIN})
        self.assertEqual(response.status_code, 200)
        return {"Origin": ORIGIN, "Authorization": f"Bearer {response.get_json()['token']}"}

    def test_claim_immediately_authorizes_health_without_bearer_or_web_poll(self):
        headers = self.pair()
        self.assertEqual(self.client.get("/health", headers=headers).status_code, 200)
        status = self.client.get("/api/pairing/status").get_json()
        self.assertEqual(status["state"], "paired")
        self.assertEqual(status["session_count"], 1)
        self.assertEqual(status["website_url"], ORIGIN)
        self.assertIsNone(status["code"])

    def test_native_pairing_controls_are_loopback_only_even_with_session(self):
        headers = self.pair()
        native = (("POST", "start"), ("GET", "status"), ("POST", "cancel"), ("POST", "revoke"))
        for method, operation in native:
            path = f"/api/pairing/{operation}"
            for unsafe_headers, remote in ((headers, "127.0.0.1"), ({}, "192.0.2.1"),
                                           ({"X-Forwarded-For": "192.0.2.1"}, "127.0.0.1"),
                                           ({"CF-Connecting-IP": "192.0.2.1"}, "127.0.0.1"),
                                           ({"Origin": ""}, "127.0.0.1")):
                with self.subTest(path=path, headers=unsafe_headers):
                    response = self.client.open(path, method=method, headers=unsafe_headers, environ_overrides={"REMOTE_ADDR": remote})
                    self.assertEqual(response.status_code, 403)
        self.assertEqual(self.manager.status()["state"], "paired")
        self.assertEqual(self.manager.status()["session_count"], 1)

    def test_claim_requires_exact_origin_and_does_not_expose_native_code(self):
        code = self.client.post("/api/pairing/start").get_json()["code"]
        for origin in (None, "", "null", ORIGIN + ".attacker.example"):
            headers = {} if origin is None else {"Origin": origin}
            with self.subTest(origin=origin):
                response = self.client.post("/api/pairing/claim", json={"code": code}, headers=headers)
                self.assertEqual(response.status_code, 403)
                self.assertNotIn("code", response.get_json())
        self.assertEqual(self.manager.status()["attempts_remaining"], 5)
        self.assertEqual(self.client.post("/api/pairing/claim", json={"code": code}, headers={"Origin": ORIGIN}).status_code, 200)

    def test_pairing_attempt_limit_is_global_across_lan_clients(self):
        code = self.client.post("/api/pairing/start").get_json()["code"]
        wrong = "000000" if code != "000000" else "000001"
        for number in range(5):
            response = self.client.post("/api/pairing/claim", json={"code": wrong}, headers={"Origin": ORIGIN},
                                        environ_overrides={"REMOTE_ADDR": f"192.0.2.{number + 1}"})
            self.assertEqual(response.status_code, 401 if number < 4 else 429)
        self.assertEqual(self.client.post("/api/pairing/claim", json={"code": code}, headers={"Origin": ORIGIN}).status_code, 429)

    def test_logout_and_native_revoke_invalidate_only_owned_sessions(self):
        first, second = self.pair(), self.pair()
        self.assertEqual(self.client.post("/api/pairing/logout", headers=first).status_code, 200)
        self.assertEqual(self.client.get("/health", headers=first).status_code, 401)
        self.assertEqual(self.client.get("/health", headers=second).status_code, 200)
        self.assertEqual(self.client.post("/api/pairing/revoke").get_json()["revoked"], 1)
        self.assertEqual(self.client.get("/health", headers=second).status_code, 401)

    def test_session_never_authorizes_native_routes_or_manual_token(self):
        headers = self.pair()
        with patch.object(backend_app.iod, "start_capture") as capture, \
             patch.object(backend_app, "analyze_source") as analysis, \
             patch.object(backend_app, "get_saved_recordings") as recordings:
            for method, path in (("POST", "/begin_recording"), ("POST", "/analyze_uploaded"),
                                 ("GET", "/api/recordings"), ("DELETE", "/api/recordings/1")):
                self.assertEqual(self.client.open(path, method=method, headers=headers).status_code, 403)
            capture.assert_not_called()
            analysis.assert_not_called()
            recordings.assert_not_called()
        headers["Authorization"] = "Bearer legacy-manual-token"
        self.assertEqual(self.client.get("/health", headers=headers).status_code, 401)

    def test_claim_preflight_allows_local_network_permission_only_for_exact_origin(self):
        headers = {"Origin": ORIGIN, "Access-Control-Request-Method": "POST",
                   "Access-Control-Request-Headers": "Content-Type",
                   "Access-Control-Request-Private-Network": "true"}
        response = self.client.options("/api/pairing/claim", headers=headers)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], ORIGIN)
        self.assertEqual(response.headers["Access-Control-Allow-Private-Network"], "true")
        headers["Origin"] = ORIGIN + ".attacker.example"
        rejected = self.client.options("/api/pairing/claim", headers=headers)
        self.assertEqual(rejected.status_code, 403)
        self.assertNotIn("Access-Control-Allow-Private-Network", rejected.headers)

    def test_unauthenticated_claim_body_is_bounded_before_json_parsing(self):
        self.client.post("/api/pairing/start")
        response = self.client.post("/api/pairing/claim", data='{"code":"' + '0' * 2048 + '"}',
                                    content_type="application/json", headers={"Origin": ORIGIN})
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.get_json()["error_code"], "invalid_request")
        self.assertEqual(self.manager.status()["attempts_remaining"], 5)

    def test_website_url_is_https_without_credentials_query_or_fragment(self):
        for value in ("", "http://chordsense.vercel.app", "https://name:password@chordsense.vercel.app",
                      ORIGIN + "?", ORIGIN + "#", ORIGIN + "/?token=private", ORIGIN + "/#private",
                      "https://chordsense.vercel.app:bad", "https://chordsense.vercel.app\n.evil"):
            with self.subTest(value=value), patch.dict(os.environ, {"CHORDSENSE_COMPANION_URL": value}):
                self.assertEqual(self.client.get("/api/pairing/status").get_json()["website_url"], "")
        with patch.dict(os.environ, {"CHORDSENSE_COMPANION_URL": ORIGIN + "/uploads"}):
            self.assertEqual(self.client.post("/api/pairing/start").get_json()["website_url"], ORIGIN + "/uploads")


if __name__ == "__main__":
    unittest.main()
