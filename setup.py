#!/usr/bin/env python3

import platform
import shutil
import subprocess
import sys
import venv
from pathlib import Path


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent

BACKEND = ROOT / "backend"
MODEL = BACKEND / "models" / "chord-cnn-lstm-model"
FRONTEND = ROOT / "frontend-tauri" / "frontend-tauri"

BACKEND_REQUIREMENTS = BACKEND / "requirements.txt"
MODEL_REQUIREMENTS = MODEL / "requirements.txt"
PATCH = BACKEND / "patches" / "chord-model-compat.patch"

BACKEND_VENV = BACKEND / ".venv"
MODEL_VENV = MODEL / "venv"

SYSTEM = platform.system()
IS_WINDOWS = SYSTEM == "Windows"
IS_LINUX = SYSTEM == "Linux"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(command, cwd=None, check=True):
    """
    Run a command and display it first.
    """

    command = [str(x) for x in command]

    print()
    print(">", " ".join(command))

    return subprocess.run(
        command,
        cwd=cwd,
        check=check,
    )


def require_command(name):
    """
    Make sure an external command exists.
    """

    path = shutil.which(name)

    if path is None:
        raise RuntimeError(
            f"Required command '{name}' was not found in PATH."
        )

    return path


def get_venv_python(environment):
    """
    Return the Python executable inside a venv.
    """

    if IS_WINDOWS:
        return environment / "Scripts" / "python.exe"

    return environment / "bin" / "python"


def create_venv(environment):
    """
    Create a Python virtual environment if it doesn't exist.
    """

    python = get_venv_python(environment)

    if python.exists():
        print(f"Virtual environment already exists: {environment}")
        return python

    print(f"Creating virtual environment: {environment}")

    builder = venv.EnvBuilder(
        with_pip=True,
        clear=False,
    )

    builder.create(environment)

    if not python.exists():
        raise RuntimeError(
            f"Failed to create virtual environment: {environment}"
        )

    return python


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_environment():
    print("=== ChordSense Setup ===")
    print()
    print(f"Operating system : {SYSTEM}")
    print(f"Python           : {sys.version.split()[0]}")
    print(f"Repository       : {ROOT}")

    if not (sys.version_info.major == 3 and sys.version_info.minor == 12):
        raise RuntimeError(
            "ChordSense setup currently requires Python 3.12.\n\n"
            "Windows:\n"
            "  py -3.12 setup.py\n\n"
            "Linux:\n"
            "  python3.12 setup.py"
        )

    if not IS_WINDOWS and not IS_LINUX:
        raise RuntimeError(
            f"Unsupported operating system: {SYSTEM}\n"
            "This setup script currently supports Windows and Linux."
        )

    require_command("git")
    require_command("npm")

    if not BACKEND.exists():
        raise RuntimeError(
            f"Backend directory does not exist: {BACKEND}"
        )

    if not FRONTEND.exists():
        raise RuntimeError(
            f"Frontend directory does not exist: {FRONTEND}"
        )

    if not BACKEND_REQUIREMENTS.exists():
        raise RuntimeError(
            f"Missing backend requirements file: {BACKEND_REQUIREMENTS}"
        )

    if not PATCH.exists():
        raise RuntimeError(
            f"Missing model compatibility patch: {PATCH}"
        )


# ---------------------------------------------------------------------------
# Step 1: Submodules
# ---------------------------------------------------------------------------

def setup_submodules():
    print()
    print("[1/7] Initializing Git submodules...")

    run(
        [
            "git",
            "submodule",
            "update",
            "--init",
            "--recursive",
        ],
        cwd=ROOT,
    )

    if not MODEL.exists():
        raise RuntimeError(
            f"Model submodule was not initialized: {MODEL}"
        )

    if not MODEL_REQUIREMENTS.exists():
        raise RuntimeError(
            f"Model requirements file does not exist: {MODEL_REQUIREMENTS}"
        )


# ---------------------------------------------------------------------------
# Step 2: Backend Python environment
# ---------------------------------------------------------------------------

def setup_backend():
    print()
    print("[2/7] Setting up backend Python environment...")

    python = create_venv(BACKEND_VENV)

    run([
        python,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "pip",
    ])

    run([
        python,
        "-m",
        "pip",
        "install",
        "-r",
        BACKEND_REQUIREMENTS,
    ])

    return python


# ---------------------------------------------------------------------------
# Step 3: Model Python environment
# ---------------------------------------------------------------------------

def setup_model():
    print()
    print("[3/7] Setting up model Python environment...")

    python = create_venv(MODEL_VENV)

    run([
        python,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "pip",
    ])

    run([
        python,
        "-m",
        "pip",
        "install",
        "-r",
        MODEL_REQUIREMENTS,
    ])

    return python


# ---------------------------------------------------------------------------
# Step 4: Hide local model venv from submodule git status
# ---------------------------------------------------------------------------

def ignore_model_venv():
    print()
    print("[4/7] Configuring local submodule ignores...")

    result = subprocess.run(
        [
            "git",
            "-C",
            str(MODEL),
            "rev-parse",
            "--git-dir",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    git_dir = Path(result.stdout.strip())

    if not git_dir.is_absolute():
        git_dir = (MODEL / git_dir).resolve()

    info_dir = git_dir / "info"
    info_dir.mkdir(parents=True, exist_ok=True)

    exclude_file = info_dir / "exclude"

    existing = ""

    if exclude_file.exists():
        existing = exclude_file.read_text(
            encoding="utf-8",
            errors="ignore",
        )

    existing_lines = {
        line.strip()
        for line in existing.splitlines()
        if line.strip()
    }

    if "venv/" not in existing_lines:
        with exclude_file.open(
            "a",
            encoding="utf-8",
            newline="\n",
        ) as file:
            if existing and not existing.endswith("\n"):
                file.write("\n")

            file.write("venv/\n")

        print("Added venv/ to the model submodule's local exclude list.")
    else:
        print("Model venv is already locally ignored.")


# ---------------------------------------------------------------------------
# Step 5: Model compatibility patch
# ---------------------------------------------------------------------------

def apply_model_patch():
    print()
    print("[5/7] Applying model compatibility patch...")

    # Can the patch be applied?
    check = subprocess.run(
        [
            "git",
            "-C",
            str(MODEL),
            "apply",
            "--check",
            str(PATCH),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if check.returncode == 0:
        run([
            "git",
            "-C",
            MODEL,
            "apply",
            PATCH,
        ])

        print("Model compatibility patch applied.")
        return

    # If it cannot be applied, check whether it has already been applied.
    reverse_check = subprocess.run(
        [
            "git",
            "-C",
            str(MODEL),
            "apply",
            "--reverse",
            "--check",
            str(PATCH),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if reverse_check.returncode == 0:
        print("Model compatibility patch is already applied.")
        return

    raise RuntimeError(
        "The model compatibility patch does not apply cleanly.\n"
        "The model submodule may be modified or checked out at an "
        "unexpected commit."
    )


# ---------------------------------------------------------------------------
# Step 6: Frontend
# ---------------------------------------------------------------------------

def setup_frontend():
    print()
    print("[6/7] Installing frontend dependencies...")

    npm = require_command("npm")

    run(
        [
            npm,
            "install",
        ],
        cwd=FRONTEND,
    )


# ---------------------------------------------------------------------------
# Step 7: Open ready-to-start terminals
# ---------------------------------------------------------------------------

def open_windows_terminals():
    """
    Open two PowerShell terminals.

    Each terminal waits for Enter before starting its process.
    """

    powershell = (
        shutil.which("pwsh")
        or shutil.which("powershell")
    )

    if powershell is None:
        raise RuntimeError(
            "PowerShell could not be found."
        )

    backend_command = (
        f'Set-Location -LiteralPath "{BACKEND}"; '
        f'& "{BACKEND_VENV / "Scripts" / "Activate.ps1"}"; '
        'Write-Host ""; '
        'Write-Host "ChordSense Backend Ready" -ForegroundColor Green; '
        'Write-Host ""; '
        'Read-Host "Press ENTER to start python app.py"; '
        'python app.py'
    )

    frontend_command = (
        f'Set-Location -LiteralPath "{FRONTEND}"; '
        'Write-Host ""; '
        'Write-Host "ChordSense Frontend Ready" -ForegroundColor Green; '
        'Write-Host ""; '
        'Read-Host "Press ENTER to start npm run tauri dev"; '
        'npm run tauri dev'
    )

    subprocess.Popen([
        powershell,
        "-NoExit",
        "-Command",
        backend_command,
    ])

    subprocess.Popen([
        powershell,
        "-NoExit",
        "-Command",
        frontend_command,
    ])


def linux_terminal_command(command):
    """
    Open a graphical Linux terminal and run a bash command.
    """

    gnome_terminal = shutil.which("gnome-terminal")
    konsole = shutil.which("konsole")
    xfce_terminal = shutil.which("xfce4-terminal")
    xterm = shutil.which("xterm")

    if gnome_terminal:
        subprocess.Popen([
            gnome_terminal,
            "--",
            "bash",
            "-lc",
            command,
        ])
        return True

    if konsole:
        subprocess.Popen([
            konsole,
            "-e",
            "bash",
            "-lc",
            command,
        ])
        return True

    if xfce_terminal:
        subprocess.Popen([
            xfce_terminal,
            "--command",
            f"bash -lc {shlex_quote(command)}",
        ])
        return True

    if xterm:
        subprocess.Popen([
            xterm,
            "-e",
            "bash",
            "-lc",
            command,
        ])
        return True

    return False


def shlex_quote(value):
    """
    Minimal shell quoting helper without requiring shell=True.
    """

    import shlex
    return shlex.quote(value)


def open_linux_terminals():
    """
    Open two Linux terminal windows.

    Each waits for Enter before starting the backend/frontend.
    """

    backend_command = (
        f'cd {shlex_quote(str(BACKEND))}; '
        'source .venv/bin/activate; '
        'echo; '
        'echo "ChordSense Backend Ready"; '
        'echo; '
        'read -r -p "Press ENTER to start python app.py"; '
        'python app.py; '
        'echo; '
        'echo "Backend exited. Press ENTER to close."; '
        'read -r'
    )

    frontend_command = (
        f'cd {shlex_quote(str(FRONTEND))}; '
        'echo; '
        'echo "ChordSense Frontend Ready"; '
        'echo; '
        'read -r -p "Press ENTER to start npm run tauri dev"; '
        'npm run tauri dev; '
        'echo; '
        'echo "Frontend exited. Press ENTER to close."; '
        'read -r'
    )

    first = linux_terminal_command(backend_command)
    second = linux_terminal_command(frontend_command)

    if not first or not second:
        print()
        print(
            "No supported graphical Linux terminal was found."
        )
        print()
        print("Open two terminals manually:")
        print()
        print("Backend:")
        print(f"  cd {BACKEND}")
        print("  source .venv/bin/activate")
        print("  python app.py")
        print()
        print("Frontend:")
        print(f"  cd {FRONTEND}")
        print("  npm run tauri dev")


def open_ready_terminals():
    print()
    print("[7/7] Opening backend and frontend terminals...")

    if IS_WINDOWS:
        open_windows_terminals()
        return

    if IS_LINUX:
        open_linux_terminals()
        return


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    validate_environment()

    setup_submodules()

    setup_backend()

    setup_model()

    ignore_model_venv()

    apply_model_patch()

    setup_frontend()

    print()
    print("========================================")
    print("ChordSense installation is complete.")
    print("========================================")

    open_ready_terminals()

    print()
    print("Two terminals should now be ready.")
    print()
    print("Backend terminal:")
    print("  Press ENTER to run: python app.py")
    print()
    print("Frontend terminal:")
    print("  Press ENTER to run: npm run tauri dev")
    print()


if __name__ == "__main__":
    try:
        main()

    except subprocess.CalledProcessError as exc:
        print()
        print(
            f"Setup failed: command exited with "
            f"code {exc.returncode}",
            file=sys.stderr,
        )

        sys.exit(exc.returncode)

    except KeyboardInterrupt:
        print()
        print("Setup cancelled.")
        sys.exit(130)

    except Exception as exc:
        print()
        print(f"Setup failed: {exc}", file=sys.stderr)
        sys.exit(1)