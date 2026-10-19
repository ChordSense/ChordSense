import io
import json
import subprocess
import threading
import unittest
from email.message import Message
from types import SimpleNamespace
from unittest.mock import Mock

from pairing_directory import (
    DiscoveryError, DiscoveryPublisher, _NoRedirect,
    lan_endpoint_from_json, read_lan_endpoint,
)


WEBSITE = "https://chordsense.vercel.app"
DIRECTORY = WEBSITE + "/api/discovery/publish"
TOKEN = "test-private-publisher-token-1234567890"
PAIRING_ID = "test-pairing-window-12345"


def lan_output(*addresses):
    return json.dumps([{"ifname": "wlan0", "addr_info": [
        {"family": "inet", "local": address} for address in addresses
    ]}])


class Response(io.BytesIO):
    def __init__(self, payload=None, *, status=200, url=DIRECTORY, content_type="application/json", raw=None):
        super().__init__(raw if raw is not None else json.dumps(payload).encode())
        self.status = status
        self.url = url
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def geturl(self):
        return self.url


class DirectoryTests(unittest.TestCase):
    def publisher(self, **kwargs):
        return DiscoveryPublisher(DIRECTORY, TOKEN, WEBSITE, **kwargs)

    def test_reads_only_wlan0_once_with_bounded_read_only_subprocess(self):
        command = Mock(return_value=SimpleNamespace(stdout=lan_output("10.136.68.97")))
        self.assertEqual(read_lan_endpoint(run_command=command), "http://10.136.68.97:5051")
        command.assert_called_once_with(
            ["ip", "-j", "-4", "address", "show", "dev", "wlan0"],
            capture_output=True, text=True, check=True, timeout=2,
        )
        for error in (FileNotFoundError(), subprocess.TimeoutExpired("ip", 2), subprocess.CalledProcessError(1, "ip")):
            with self.subTest(error=type(error).__name__):
                with self.assertRaises(DiscoveryError):
                    read_lan_endpoint(run_command=Mock(side_effect=error))

    def test_selects_one_canonical_rfc1918_address_and_rejects_overlay_or_ambiguous_data(self):
        for address in ("10.136.68.97", "172.16.0.2", "172.31.255.254", "192.168.1.4"):
            self.assertEqual(lan_endpoint_from_json(lan_output(address)), f"http://{address}:5051")
        for output in ("", "{}", "[]", lan_output(), lan_output("10.1.2.3", "192.168.1.2"),
                       lan_output("10.1.2.3", "10.1.2.3"), lan_output("100.71.233.14"),
                       lan_output("127.0.0.1"), lan_output("0.0.0.0"), lan_output("8.8.8.8"),
                       lan_output("010.1.2.3"), lan_output("10.2.3.999"),
                       json.dumps([{"ifname": "tailscale0", "addr_info": []}]),
                       json.dumps([{"ifname": "wlan0", "addr_info": [{"family": "inet6", "local": "::1"}]}])):
            with self.subTest(output=output):
                with self.assertRaises(DiscoveryError):
                    lan_endpoint_from_json(output)

    def test_publishes_fresh_lan_address_with_private_bearer_and_matching_ack(self):
        opener = Mock()
        opener.open.return_value = Response({"success": True, "pairing_id": PAIRING_ID}, status=201)
        reader = Mock(return_value="http://10.136.68.97:5051")
        publisher = self.publisher(opener=opener, endpoint_reader=reader)
        publisher.publish(pairing_id=PAIRING_ID, code="000042", proof_key="p" * 43, expires_in=120)
        reader.assert_called_once_with()
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, DIRECTORY)
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer " + TOKEN)
        self.assertEqual(json.loads(request.data), {
            "pairing_id": PAIRING_ID, "code": "000042", "proof_key": "p" * 43,
            "expires_in": 120, "api_url": "http://10.136.68.97:5051",
        })
        self.assertEqual(opener.open.call_args.kwargs, {"timeout": 3})

    def test_configuration_is_optional_but_partial_or_unsafe_configuration_is_rejected(self):
        self.assertIsNone(DiscoveryPublisher.from_environment({}))
        valid = {"CHORDSENSE_DISCOVERY_URL": DIRECTORY, "CHORDSENSE_DISCOVERY_TOKEN": TOKEN,
                 "CHORDSENSE_COMPANION_URL": WEBSITE}
        self.assertIsInstance(DiscoveryPublisher.from_environment(valid), DiscoveryPublisher)
        for values in (
            {**valid, "CHORDSENSE_DISCOVERY_TOKEN": "short"},
            {**valid, "CHORDSENSE_DISCOVERY_TOKEN": "x" * 32 + "\nheader"},
            {**valid, "CHORDSENSE_DISCOVERY_URL": "http://chordsense.vercel.app/api/discovery/publish"},
            {**valid, "CHORDSENSE_DISCOVERY_URL": "https://other.vercel.app/api/discovery/publish"},
            {**valid, "CHORDSENSE_DISCOVERY_URL": WEBSITE + "/wrong"},
            {**valid, "CHORDSENSE_DISCOVERY_URL": DIRECTORY + "?secret=yes"},
            {**valid, "CHORDSENSE_DISCOVERY_URL": DIRECTORY + "#"},
            {**valid, "CHORDSENSE_DISCOVERY_URL": "https://user:password@chordsense.vercel.app/api/discovery/publish"},
            {**valid, "CHORDSENSE_COMPANION_URL": ""},
            {"CHORDSENSE_DISCOVERY_URL": DIRECTORY},
            {"CHORDSENSE_DISCOVERY_TOKEN": TOKEN},
        ):
            with self.subTest(keys=tuple(values)):
                with self.assertRaises(DiscoveryError) as error:
                    DiscoveryPublisher.from_environment(values)
                self.assertNotIn(TOKEN, str(error.exception))

    def test_redirects_and_nonmatching_or_untrusted_responses_fail_closed(self):
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://other.example"))
        for response in (
            Response({"success": True, "pairing_id": "old-window"}),
            Response({"success": False, "pairing_id": PAIRING_ID}),
            Response({"success": True, "pairing_id": PAIRING_ID}, status=302),
            Response({"success": True, "pairing_id": PAIRING_ID}, url="https://other.example"),
            Response(raw=b"<html>Login</html>", content_type="text/html"),
            Response(raw=b"not json"), Response(raw=b"x" * 8193),
        ):
            opener = Mock()
            opener.open.return_value = response
            with self.assertRaises(DiscoveryError):
                self.publisher(opener=opener, endpoint_reader=lambda: "http://10.1.2.3:5051").publish(
                    pairing_id=PAIRING_ID, code="000042", proof_key="p" * 43, expires_in=120,
                )
        for lifetime in (0, 121, True, "120"):
            with self.assertRaises(DiscoveryError):
                self.publisher(endpoint_reader=Mock(side_effect=AssertionError("no address lookup"))).publish(
                    pairing_id=PAIRING_ID, code="000042", proof_key="p" * 43, expires_in=lifetime,
                )
        opener = Mock()
        opener.open.side_effect = TimeoutError("network timeout")
        with self.assertRaises(DiscoveryError):
            self.publisher(opener=opener, endpoint_reader=lambda: "http://10.1.2.3:5051").publish(
                pairing_id=PAIRING_ID, code="000042", proof_key="p" * 43, expires_in=120,
            )
        self.assertEqual(opener.open.call_count, 1)

    def test_cleanup_is_asynchronous_bounded_and_conditionally_deletes_only_queued_ids(self):
        entered = threading.Event()
        release = threading.Event()
        deleted = []
        publisher = self.publisher(max_cleanup=2)

        def request(method, body):
            self.assertEqual(method, "DELETE")
            deleted.append(body)
            entered.set()
            self.assertTrue(release.wait(timeout=2))

        publisher._request = request
        publisher.delete_later("first-window")
        self.assertTrue(entered.wait(timeout=2))
        for number in range(20):
            publisher.delete_later(f"old-window-{number}")
        with publisher._cleanup_lock:
            self.assertEqual(len(publisher._cleanup_pending), 2)
            worker = publisher._cleanup_thread
        self.assertTrue(worker.daemon)
        release.set()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(deleted, [
            {"pairing_id": "first-window"}, {"pairing_id": "old-window-18"}, {"pairing_id": "old-window-19"},
        ])
        self.assertIsNone(publisher._cleanup_thread)

    def test_cleanup_failure_is_not_retried_or_exposed_to_caller(self):
        publisher = self.publisher()
        called = threading.Event()

        def request(_method, _body):
            called.set()
            raise DiscoveryError("offline")

        publisher._request = request
        publisher.delete_later(PAIRING_ID)
        self.assertTrue(called.wait(timeout=2))
        with publisher._cleanup_lock:
            worker = publisher._cleanup_thread
        if worker is not None:
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())


if __name__ == "__main__":
    unittest.main()
