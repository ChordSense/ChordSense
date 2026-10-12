"""One hardware-owning process with a bounded HTTP thread pool."""

import os
import signal

# Set these before importing NumPy/SciPy through the device application.
for variable in (
    "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
):
    os.environ.setdefault(variable, "1")


def main():
    from waitress import serve
    from app import app

    def terminate(_signal, _frame):
        # SystemExit runs registered atexit handlers, releasing the local live
        # recognizer on a normal systemd stop/restart.
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, terminate)

    serve(
        app,
        host=os.environ.get("CHORDSENSE_API_HOST", "0.0.0.0"),
        port=int(os.environ.get("CHORDSENSE_API_PORT", "5051")),
        threads=int(os.environ.get("CHORDSENSE_API_THREADS", "8")),
        max_request_body_size=max(app.config["MAX_CONTENT_LENGTH"], app.config["COMPANION_MAX_CONTENT_LENGTH"]),
        # Flask must see forwarding headers so tunnel requests cannot take
        # the trusted local-desktop bypass. Do not apply ProxyFix here.
        clear_untrusted_proxy_headers=False,
    )


if __name__ == "__main__":
    main()
