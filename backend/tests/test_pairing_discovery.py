import hashlib
import hmac
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from pairing import PairingError, PairingManager
from pairing_directory import DiscoveryError, DiscoveryPublisher


ORIGIN = "https://chordsense.vercel.app"


class FakePublisher:
    def __init__(self):
        self.published = []
        self.deleted = []
        self.action = None

    def publish(self, **body):
        self.published.append(body)
        if self.action:
            self.action()

    def delete_later(self, pairing_id):
        self.deleted.append(pairing_id)


class DiscoveryPairingTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.publisher = FakePublisher()
        self.manager = PairingManager(clock=lambda: self.now, publisher=self.publisher)

    def binding(self, publication=None):
        publication = publication or self.publisher.published[-1]
        return {key: publication[key] for key in ("pairing_id", "proof_key")}

    def test_success_publishes_once_then_exposes_only_code_in_native_status(self):
        with patch("pairing.secrets.randbelow", return_value=42):
            result = self.manager.start()
        self.assertEqual(result["code"], "000042")
        self.assertEqual(result["state"], "pending")
        self.assertEqual(result["expires_in"], 120)
        self.assertEqual(len(self.publisher.published), 1)
        publication = self.publisher.published[0]
        self.assertEqual(publication["code"], result["code"])
        self.assertGreaterEqual(len(publication["pairing_id"]), 22)
        self.assertEqual(len(publication["proof_key"]), 43)
        self.assertNotIn("proof_key", result)
        self.assertNotIn("pairing_id", result)
        self.assertNotIn(publication["proof_key"], repr(self.manager.status()))

    def test_publish_hides_code_and_does_not_hold_pairing_lock(self):
        entered, release = threading.Event(), threading.Event()

        def publish():
            entered.set()
            self.assertTrue(release.wait(timeout=2))

        self.publisher.action = publish
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.manager.start)
            self.assertTrue(entered.wait(timeout=2))
            try:
                self.assertEqual(self.manager.status()["state"], "publishing")
                self.assertIsNone(self.manager.status()["code"])
                with self.assertRaises(PairingError) as error:
                    self.manager.claim(self.publisher.published[0]["code"], ORIGIN, **self.binding())
                self.assertEqual(error.exception.status, 409)
            finally:
                release.set()
            self.assertEqual(future.result(timeout=2)["state"], "pending")

    def test_publication_failure_leaves_no_usable_code_or_proof(self):
        self.publisher.action = lambda: (_ for _ in ()).throw(DiscoveryError("offline"))
        with self.assertRaises(PairingError) as error:
            self.manager.start()
        self.assertEqual((error.exception.status, error.exception.code), (503, "discovery_unavailable"))
        self.assertIsNone(self.manager.status()["code"])
        self.assertEqual(self.publisher.deleted, [self.publisher.published[0]["pairing_id"]])
        with self.assertRaises(PairingError):
            self.manager.proof(self.publisher.published[0]["pairing_id"], "a" * 64)

    def test_failed_new_publication_preserves_already_authorized_sessions(self):
        code = self.manager.start()["code"]
        token = self.manager.claim(code, ORIGIN, **self.binding())["token"]
        self.publisher.action = lambda: (_ for _ in ()).throw(DiscoveryError("offline"))
        with self.assertRaises(PairingError):
            self.manager.start()
        self.assertTrue(self.manager.authenticate(token, ORIGIN))
        self.assertEqual(self.manager.status()["session_count"], 1)
        self.assertIsNone(self.manager.status()["code"])

    def test_cancel_and_revoke_during_publish_cannot_be_undone_by_late_ack(self):
        for operation in ("cancel", "revoke_all"):
            with self.subTest(operation=operation):
                publisher = FakePublisher()
                manager = PairingManager(publisher=publisher)
                entered, release = threading.Event(), threading.Event()
                publisher.action = lambda: (entered.set(), release.wait(timeout=2))
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(manager.start)
                    self.assertTrue(entered.wait(timeout=2))
                    getattr(manager, operation)()
                    release.set()
                    with self.assertRaises(PairingError):
                        future.result(timeout=2)
                self.assertEqual(manager.status()["state"], "cancelled")
                self.assertIsNone(manager.status()["code"])
                self.assertIn(publisher.published[0]["pairing_id"], publisher.deleted)

    def test_concurrent_start_is_bounded_and_cannot_publish_out_of_order(self):
        entered, release = threading.Event(), threading.Event()
        self.publisher.action = lambda: (entered.set(), release.wait(timeout=2))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.manager.start)
            self.assertTrue(entered.wait(timeout=2))
            try:
                with self.assertRaises(PairingError) as error:
                    self.manager.start()
                self.assertEqual(error.exception.code, "discovery_unavailable")
                self.assertEqual(len(self.publisher.published), 1)
                self.manager.cancel()
            finally:
                release.set()
            with self.assertRaises(PairingError):
                future.result(timeout=2)
        self.publisher.action = None
        self.manager.start()
        self.assertEqual(len(self.publisher.published), 2)
        self.assertNotEqual(self.publisher.published[0]["pairing_id"], self.publisher.published[1]["pairing_id"])

    def test_publication_deadline_is_not_extended_by_a_late_ack(self):
        self.publisher.action = lambda: setattr(self, "now", self.now + 120)
        with self.assertRaises(PairingError):
            self.manager.start()
        self.assertEqual(self.manager.status()["state"], "expired")
        self.assertIsNone(self.manager.status()["code"])
        self.assertIn(self.publisher.published[0]["pairing_id"], self.publisher.deleted)

    def test_proof_binds_fresh_challenge_to_exact_window_and_never_exposes_key(self):
        self.manager.start()
        binding = self.binding()
        challenge = "1234567890abcdef" * 4
        result = self.manager.proof(binding["pairing_id"], challenge)
        message = f"chordsense-pairing-v1\n{binding['pairing_id']}\n{challenge}".encode()
        self.assertEqual(result, {"pairing_id": binding["pairing_id"], "proof": hmac.new(
            binding["proof_key"].encode(), message, hashlib.sha256).hexdigest()})
        self.assertNotEqual(result["proof"], self.manager.proof(binding["pairing_id"], "b" * 64)["proof"])
        self.assertNotIn("proof_key", result)
        for invalid_id in (None, 1, [], "wrong", "é" * 22):
            with self.assertRaises(PairingError):
                self.manager.proof(invalid_id, challenge)
        for invalid_challenge in (None, "a" * 63, "A" * 64, "z" * 64, [], "a" * 65):
            with self.assertRaises(PairingError):
                self.manager.proof(binding["pairing_id"], invalid_challenge)
        self.assertEqual(self.manager.status()["attempts_remaining"], 5)

    def test_missing_or_wrong_claim_binding_never_tests_code_or_burns_budget(self):
        code = self.manager.start()["code"]
        binding = self.binding()
        for invalid in ({}, {**binding, "proof_key": "x" * 43}, {**binding, "pairing_id": "x" * 22},
                        {**binding, "proof_key": []}, {**binding, "proof_key": "é" * 43}):
            with self.assertRaises(PairingError) as error:
                self.manager.claim(code, ORIGIN, **invalid)
            self.assertEqual(error.exception.code, "pairing_mismatch")
        self.assertEqual(self.manager.status()["attempts_remaining"], 5)
        result = self.manager.claim(code, ORIGIN, **binding)
        self.assertTrue(self.manager.authenticate(result["token"], ORIGIN))
        self.assertEqual(self.publisher.deleted, [binding["pairing_id"]])
        with self.assertRaises(PairingError):
            self.manager.proof(binding["pairing_id"], "a" * 64)

    def test_replacement_expiry_and_revoke_invalidate_old_proofs(self):
        first = self.manager.start()
        binding = self.binding()
        self.manager.start()
        self.assertIn(binding["pairing_id"], self.publisher.deleted)
        with self.assertRaises(PairingError):
            self.manager.claim(first["code"], ORIGIN, **binding)
        current = self.binding()
        self.now += 120
        with self.assertRaises(PairingError):
            self.manager.proof(current["pairing_id"], "a" * 64)
        self.assertEqual(self.manager.status()["state"], "expired")
        self.manager.start()
        self.manager.revoke_all()
        with self.assertRaises(PairingError):
            self.manager.proof(self.binding()["pairing_id"], "a" * 64)

    def test_concurrent_bound_claims_still_issue_exactly_one_session(self):
        code = self.manager.start()["code"]
        binding = self.binding()
        ready = threading.Barrier(8)

        def claim():
            ready.wait(timeout=2)
            try:
                return self.manager.claim(code, ORIGIN, **binding)
            except PairingError as error:
                return error.status

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: claim(), range(8)))
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        self.assertEqual(results.count(409), 7)
        self.assertEqual(self.manager.status()["session_count"], 1)
        self.assertEqual(self.publisher.deleted, [binding["pairing_id"]])

    def test_bad_private_configuration_fails_pairing_without_breaking_other_local_state(self):
        with patch.object(DiscoveryPublisher, "from_environment", side_effect=DiscoveryError("bad config")):
            manager = PairingManager()
        with self.assertRaises(PairingError) as error:
            manager.start()
        self.assertEqual(error.exception.code, "discovery_unavailable")
        self.assertEqual(manager.status()["session_count"], 0)
        self.assertIsNone(manager.status()["code"])

    def test_claim_and_revoke_complete_while_real_publisher_cleanup_is_waiting_on_network(self):
        publisher = DiscoveryPublisher(
            ORIGIN + "/api/discovery/publish", "test-publisher-token-" + "a" * 32, ORIGIN,
            endpoint_reader=lambda: "http://10.136.68.97:5051",
        )
        entered, release = threading.Event(), threading.Event()
        publications = []

        def request(method, body):
            if method == "POST":
                publications.append(body)
            else:
                entered.set()
                self.assertTrue(release.wait(timeout=2))

        publisher._request = request
        manager = PairingManager(publisher=publisher)
        code = manager.start()["code"]
        binding = {key: publications[-1][key] for key in ("pairing_id", "proof_key")}
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(manager.claim, code, ORIGIN, **binding)
            try:
                self.assertTrue(entered.wait(timeout=2))
                # The claim must return while DELETE is still held by release.
                token = future.result(timeout=0.5)["token"]
                self.assertTrue(manager.authenticate(token, ORIGIN))
                self.assertEqual(manager.status()["state"], "paired")
                self.assertEqual(manager.revoke_all()["revoked"], 1)
                self.assertFalse(manager.authenticate(token, ORIGIN))
            finally:
                release.set()
                with publisher._cleanup_lock:
                    worker = publisher._cleanup_thread
                if worker is not None:
                    worker.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
