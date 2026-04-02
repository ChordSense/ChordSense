# ChordSenseOfficial

ChordSenseOfficial is set up as a multi-part project with:

- a Python backend
- a separate Python environment for the model repository
- a Rust frontend
- Git submodules that must be initialized when cloning

## Prerequisites

Before you begin, make sure you have the following:

- Git
- Python 3.10
- `venv` support for Python 3.10
- Rust and Cargo
- Linux build dependencies for audio / Rust tooling

## Clone the Repository

For a fresh clone, use:

```bash
git clone --recurse-submodules git@github.com:ChordSense/ChordSense.git
cd ChordSense
git checkout chordsense_play_along_full
git submodule update --init --recursive
```

If you already cloned without submodules, run:

```bash
git submodule update --init --recursive
```

## Project Structure

Expected local layout:

```text
ChordSense/
├── backend/
│   ├── app.py
│   ├── requirements.txt
│   └── model_repo/
│       └── requirements.txt
└── frontend/
```

## Backend Setup

Create and activate the backend virtual environment, then install dependencies:

```bash
cd backend
python3.10 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate
```

## Model Repository Setup

The backend currently expects the model repository to use its own separate Python virtual environment.

```bash
cd ..
cd model_repo
python3.10 -m venv venv
source venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
deactivate
```

## Rust Installation

Install Rust and required system packages:

```bash
sudo apt update
sudo apt install -y curl build-essential pkg-config libasound2-dev
curl https://sh.rustup.rs -sSf | sh
source "$HOME/.cargo/env"

cargo --version
rustc --version
```

## Build the Frontend

```bash
cd ~/ChordSense/frontend
source "$HOME/.cargo/env"
cargo build --bin chordsense_audio_synced
```

## Run the Application

### Start the Backend

```bash
cd ~/ChordSense/backend
source venv/bin/activate
python app.py
```

### Start the Frontend

Open a second terminal and run:

```bash
cd ~/ChordSense/frontend
source "$HOME/.cargo/env"
cargo run --bin chordsense_audio_synced
```

## Notes

- The backend and `model_repo` use separate Python environments.
- If the frontend fails to build after installing Rust, reload your shell or run:

```bash
source "$HOME/.cargo/env"
```

- If submodules appear empty or incomplete, rerun:

```bash
git submodule update --init --recursive
```
