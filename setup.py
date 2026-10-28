#!/usr/bin/env python3
"""Install Python services and the native Rust UI; web/ is outside this helper's scope."""
import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
MODEL = BACKEND / "models" / "chord-cnn-lstm-model"
FRONTEND = ROOT / "device-ui"
BACKEND_VENV = BACKEND / ".venv"
MODEL_VENV = MODEL / "venv"
PATCH = BACKEND / "patches" / "chord-model-compat.patch"
IS_WINDOWS = platform.system() == "Windows"
HEALTH_URL = "http://127.0.0.1:5051/health"


def run(command, cwd=None, **kwargs):
    command = [str(part) for part in command]
    print("\n>", subprocess.list2cmdline(command), flush=True)
    return subprocess.run(command, cwd=cwd, check=True, **kwargs)


def require_command(name):
    command = shutil.which(name)
    if command is None:
        raise RuntimeError(f"Required command '{name}' was not found in PATH.")
    return command


def get_venv_python(environment):
    return environment / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def validate_environment():
    print("=== ChordSense native Rust setup ===", flush=True)
    print(f"Operating system: {platform.system()}\nPython: {sys.version.split()[0]}\nRepository: {ROOT}")
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("Setup requires Python 3.12. Run python3.12 setup.py (Linux) or a Python 3.12 interpreter on Windows.")
    if platform.system() not in {"Windows", "Linux"}:
        raise RuntimeError("This installation helper supports Windows and Linux.")
    for name in ("git", "cargo", "rustc", "ffmpeg"):
        require_command(name)
    for path in (BACKEND / "requirements.txt", BACKEND / "serve.py", FRONTEND / "Cargo.toml", PATCH):
        if not path.is_file():
            raise RuntimeError(f"Required project file is missing: {path}")
    if os.environ.get("CHORDSENSE_API_PORT", "5051") != "5051":
        raise RuntimeError("The device interface expects API port 5051. Remove or correct CHORDSENSE_API_PORT before launching.")


def setup_submodules():
    print("\n[1/7] Initializing model submodules...")
    run(["git", "submodule", "update", "--init", "--recursive"], cwd=ROOT)
    if not (MODEL / "requirements.txt").is_file():
        raise RuntimeError(f"Model submodule is incomplete: {MODEL}")


def setup_environment(environment, requirements):
    python = get_venv_python(environment)
    if not python.is_file():
        venv.EnvBuilder(with_pip=True, clear=False).create(environment)
    else:
        version = subprocess.check_output(
            [str(python), "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"], text=True
        ).strip()
        if version != "3.12":
            raise RuntimeError(f"Existing environment {environment} uses Python {version}; create a Python 3.12 environment before continuing.")
    run([python, "-m", "pip", "install", "--upgrade", "pip"])
    run([python, "-m", "pip", "install", "-r", requirements])
    return python


def ignore_model_venv():
    print("\n[4/7] Configuring the model environment's local Git ignore...")
    result = subprocess.check_output(["git", "-C", str(MODEL), "rev-parse", "--git-dir"], text=True)
    git_dir = Path(result.strip())
    if not git_dir.is_absolute():
        git_dir = (MODEL / git_dir).resolve()
    exclude = git_dir / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if "venv/" not in existing.splitlines():
        with exclude.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(("\n" if existing and not existing.endswith("\n") else "") + "venv/\n")


def apply_model_patch():
    print("\n[5/7] Applying model compatibility patch...")
    base = ["git", "-C", str(MODEL), "apply"]
    if subprocess.run(base + ["--check", str(PATCH)], capture_output=True).returncode == 0:
        run(base + [PATCH])
    elif subprocess.run(base + ["--reverse", "--check", str(PATCH)], capture_output=True).returncode != 0:
        raise RuntimeError("The model compatibility patch does not apply cleanly. Preserve local model changes and resolve them before continuing.")
    else:
        print("Model patch is already applied.")


def setup_frontend():
    print("\n[6/7] Building the native Rust device interface...")
    # Cargo reports the executable location, including a custom target directory.
    command = ["cargo", "build", "--locked", "--manifest-path", str(FRONTEND / "Cargo.toml"), "--message-format=json-render-diagnostics"]
    print("\n>", subprocess.list2cmdline(command), flush=True)
    result = subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE, text=True)
    executable = None
    for line in result.stdout.splitlines():
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if message.get("reason") == "compiler-message":
            rendered = message.get("message", {}).get("rendered")
            if rendered:
                print(rendered, file=sys.stderr, end="")
        if message.get("reason") == "compiler-artifact" and message.get("target", {}).get("name") == "device-ui" and message.get("executable"):
            executable = Path(message["executable"])
    result.check_returncode()
    if executable is None or not executable.is_file():
        raise RuntimeError("Cargo completed without reporting the device-ui executable.")
    return executable


def healthy_backend():
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=1) as response:
            body = json.load(response)
            return body.get("success") is True and body.get("message") == "ChordSense device API running"
    except (OSError, ValueError, urllib.error.URLError):
        return False


def launch(executable, python, windowed=False):
    print("\n[7/7] Starting ChordSense...")
    backend_process = None
    log = None
    try:
        if healthy_backend():
            print("Using the ChordSense backend already listening on port 5051.")
        else:
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", 5051)) == 0:
                    raise RuntimeError("Port 5051 is occupied by a service that did not return ChordSense health. Stop that service first.")
            runtime = ROOT / "runtime"
            runtime.mkdir(exist_ok=True)
            log_path = runtime / "startup.log"
            log = log_path.open("a", encoding="utf-8")
            backend_process = subprocess.Popen(
                [str(python), "serve.py"], cwd=BACKEND, stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0,
            )
            deadline = time.monotonic() + 30
            while not healthy_backend():
                if backend_process.poll() is not None:
                    raise RuntimeError(f"Backend exited during startup. See {log_path}")
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"Backend did not become healthy. See {log_path}")
                time.sleep(0.25)
        print("Backend is ready. Opening the native interface. Close the app to stop this launch.")
        # Argument arrays avoid shell interpretation and PowerShell activation policies.
        run([executable] + (["--windowed"] if windowed else []), cwd=FRONTEND)
    finally:
        # Never stop a backend that was running before this helper launched.
        if backend_process is not None and backend_process.poll() is None:
            backend_process.terminate()
            try:
                backend_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                backend_process.kill()
                backend_process.wait()
        if log is not None:
            log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-launch", action="store_true", help="Install and build without starting the backend or UI.")
    parser.add_argument("--windowed", action="store_true", help="Launch in a window instead of fullscreen.")
    args = parser.parse_args()
    validate_environment()
    setup_submodules()
    print("\n[2/7] Installing backend dependencies...")
    python = setup_environment(BACKEND_VENV, BACKEND / "requirements.txt")
    print("\n[3/7] Installing offline model dependencies...")
    setup_environment(MODEL_VENV, MODEL / "requirements.txt")
    ignore_model_venv()
    apply_model_patch()
    run([python, "-c", "import flask, waitress, librosa; from app import app; print('Backend import check passed.')"], cwd=BACKEND)
    executable = setup_frontend()
    print(f"\nInstallation and build completed: {executable}")
    print("Pi recording and live feedback still require its configured iod service and recording model hardware.")
    if not args.no_launch:
        launch(executable, python, args.windowed)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nSetup cancelled.", file=sys.stderr)
        sys.exit(130)
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"\nSetup failed: {error}", file=sys.stderr)
        sys.exit(1)
