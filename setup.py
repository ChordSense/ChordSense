#!/usr/bin/env python3

import os
import platform
import shlex
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
    Run a command and print it first.
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
    Make sure an external command exists in PATH.
    """

    path = shutil.which(name)

    if path is None:
        raise RuntimeError(
            f"Required command '{name}' was not found in PATH."
        )

    return path


def get_venv_python(environment):
    """
    Return the Python executable inside a virtual environment.
    """

    if IS_WINDOWS:
        return environment / "Scripts" / "python.exe"

    return environment / "bin" / "python"


def create_venv(environment):
    """
    Create a Python virtual environment if it doesn't already exist.
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

    if not (
        sys.version_info.major == 3
        and sys.version_info.minor == 12
    ):
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

    # Tauri requires Rust.
    require_command("cargo")
    require_command("rustc")

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
            f"Missing backend requirements file: "
            f"{BACKEND_REQUIREMENTS}"
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
            f"Model requirements file does not exist: "
            f"{MODEL_REQUIREMENTS}"
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

        print(
            "Added venv/ to the model submodule's "
            "local exclude list."
        )
    else:
        print("Model venv is already locally ignored.")


# ---------------------------------------------------------------------------
# Step 5: Apply model compatibility patch
# ---------------------------------------------------------------------------

def apply_model_patch():
    print()
    print("[5/7] Applying model compatibility patch...")

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

    # If normal application fails, check if it is already applied.
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
        "The model submodule may be modified or checked out "
        "at an unexpected commit."
    )


# ---------------------------------------------------------------------------
# Step 6: Frontend dependencies
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
# Step 7A: Windows terminals
# ---------------------------------------------------------------------------

def open_windows_terminals():
    """
    Open two separate PowerShell consoles.

    Backend:
        - Opens in backend/
        - Activates backend/.venv
        - Waits for Enter
        - Runs python app.py

    Frontend:
        - Opens in frontend-tauri/frontend-tauri/
        - Waits for Enter
        - Runs npm run tauri dev
    """

    powershell = (
        shutil.which("pwsh")
        or shutil.which("powershell")
    )

    if powershell is None:
        raise RuntimeError(
            "PowerShell could not be found."
        )

    backend_activate = (
        BACKEND_VENV
        / "Scripts"
        / "Activate.ps1"
    )

    backend_command = (
        f'Set-Location -LiteralPath "{BACKEND}"; '
        f'& "{backend_activate}"; '
        'Write-Host ""; '
        'Write-Host '
        '"ChordSense Backend Ready" '
        '-ForegroundColor Green; '
        'Write-Host ""; '
        'Read-Host '
        '"Press ENTER to start python app.py"; '
        'python app.py'
    )

    frontend_command = (
        f'Set-Location -LiteralPath "{FRONTEND}"; '
        'Write-Host ""; '
        'Write-Host '
        '"ChordSense Frontend Ready" '
        '-ForegroundColor Green; '
        'Write-Host ""; '
        'Read-Host '
        '"Press ENTER to start npm run tauri dev"; '
        'npm run tauri dev'
    )

    subprocess.Popen(
        [
            powershell,
            "-NoExit",
            "-Command",
            backend_command,
        ],
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )

    subprocess.Popen(
        [
            powershell,
            "-NoExit",
            "-Command",
            frontend_command,
        ],
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )


# ---------------------------------------------------------------------------
# Step 7B: Linux terminals
# ---------------------------------------------------------------------------

def find_linux_terminal():
    """
    Find a supported Linux graphical terminal emulator.
    """

    terminals = [
        "gnome-terminal",
        "konsole",
        "xfce4-terminal",
        "mate-terminal",
        "lxterminal",
        "xterm",
    ]

    for name in terminals:
        path = shutil.which(name)

        if path:
            return name, path

    return None, None


def spawn_linux_terminal(title, command):
    """
    Open one Linux terminal window and execute a bash command.
    """

    terminal_name, terminal = find_linux_terminal()

    if terminal is None:
        return False

    # GNOME Terminal
    if terminal_name == "gnome-terminal":
        subprocess.Popen([
            terminal,
            "--title",
            title,
            "--",
            "bash",
            "-lc",
            command,
        ])
        return True

    # KDE Konsole
    if terminal_name == "konsole":
        subprocess.Popen([
            terminal,
            "--new-tab",
            "-p",
            f"tabtitle={title}",
            "-e",
            "bash",
            "-lc",
            command,
        ])
        return True

    # XFCE Terminal
    if terminal_name == "xfce4-terminal":
        subprocess.Popen([
            terminal,
            "--title",
            title,
            "--command",
            f"bash -lc {shlex.quote(command)}",
        ])
        return True

    # MATE Terminal
    if terminal_name == "mate-terminal":
        subprocess.Popen([
            terminal,
            "--title",
            title,
            "--",
            "bash",
            "-lc",
            command,
        ])
        return True

    # LXTerminal
    if terminal_name == "lxterminal":
        subprocess.Popen([
            terminal,
            "--title",
            title,
            "-e",
            f"bash -lc {shlex.quote(command)}",
        ])
        return True

    # xterm
    if terminal_name == "xterm":
        subprocess.Popen([
            terminal,
            "-T",
            title,
            "-e",
            "bash",
            "-lc",
            command,
        ])
        return True

    return False


def open_linux_terminals():
    """
    Open two separate Linux terminal windows.

    Behavior mirrors Windows:
        - One backend terminal
        - One frontend terminal
        - Each waits for Enter before starting
    """

    if (
        not os.environ.get("DISPLAY")
        and not os.environ.get("WAYLAND_DISPLAY")
    ):
        print()
        print(
            "No graphical Linux session detected."
        )
        print_manual_start_commands()
        return

    backend_command = (
        f'cd {shlex.quote(str(BACKEND))}; '
        'source .venv/bin/activate; '
        'printf "\\n"; '
        'printf "\\033[32mChordSense Backend Ready\\033[0m\\n"; '
        'printf "\\n"; '
        'read -r -p '
        '"Press ENTER to start python app.py"; '
        'python app.py; '
        'printf "\\n"; '
        'read -r -p '
        '"Backend exited. Press ENTER to close."'
    )

    frontend_command = (
        f'cd {shlex.quote(str(FRONTEND))}; '
        'printf "\\n"; '
        'printf "\\033[32mChordSense Frontend Ready\\033[0m\\n"; '
        'printf "\\n"; '
        'read -r -p '
        '"Press ENTER to start npm run tauri dev"; '
        'npm run tauri dev; '
        'printf "\\n"; '
        'read -r -p '
        '"Frontend exited. Press ENTER to close."'
    )

    backend_opened = spawn_linux_terminal(
        "ChordSense Backend",
        backend_command,
    )

    frontend_opened = spawn_linux_terminal(
        "ChordSense Frontend",
        frontend_command,
    )

    if not backend_opened or not frontend_opened:
        print()
        print(
            "Could not find a supported Linux terminal emulator."
        )

        print_manual_start_commands()


# ---------------------------------------------------------------------------
# Manual fallback
# ---------------------------------------------------------------------------

def print_manual_start_commands():
    print()
    print("Open two terminals manually.")
    print()

    if IS_WINDOWS:
        print("Backend:")
        print(r"  cd backend")
        print(r"  .\.venv\Scripts\Activate.ps1")
        print(r"  python app.py")
        print()

        print("Frontend:")
        print(r"  cd frontend-tauri\frontend-tauri")
        print(r"  npm run tauri dev")

    else:
        print("Backend:")
        print(f"  cd {BACKEND}")
        print("  source .venv/bin/activate")
        print("  python app.py")
        print()

        print("Frontend:")
        print(f"  cd {FRONTEND}")
        print("  npm run tauri dev")


# ---------------------------------------------------------------------------
# Open terminals
# ---------------------------------------------------------------------------

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
    print("Backend and frontend terminals are ready.")
    print()
    print("Backend:")
    print("  Press ENTER to run: python app.py")
    print()
    print("Frontend:")
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
        print(
            f"Setup failed: {exc}",
            file=sys.stderr,
        )

        sys.exit(1)