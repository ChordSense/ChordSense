import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import serve


class ProductionServerTests(unittest.TestCase):
    def test_lan_server_uses_bounded_threads_and_preserves_proxy_guard_headers(self):
        waitress = Mock()
        with patch.dict(sys.modules, {"waitress": SimpleNamespace(serve=waitress)}), \
             patch.dict(os.environ, {}, clear=True), \
             patch.object(serve.signal, "signal") as install_signal:
            serve.main()
        self.assertEqual(waitress.call_args.kwargs["host"], "0.0.0.0")
        self.assertEqual(waitress.call_args.kwargs["port"], 5051)
        self.assertEqual(waitress.call_args.kwargs["threads"], 8)
        self.assertFalse(waitress.call_args.kwargs["clear_untrusted_proxy_headers"])
        with self.assertRaises(SystemExit) as stopped:
            install_signal.call_args.args[1](None, None)
        self.assertEqual(stopped.exception.code, 0)

    def test_bind_address_is_configurable(self):
        waitress = Mock()
        with patch.dict(sys.modules, {"waitress": SimpleNamespace(serve=waitress)}), \
             patch.dict(os.environ, {"CHORDSENSE_API_HOST": "127.0.0.1"}), \
             patch.object(serve.signal, "signal"):
            serve.main()
        self.assertEqual(waitress.call_args.kwargs["host"], "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
