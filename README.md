If cloning fresh:

git clone --recurse-submodules git@github.com:ChordSense/ChordSense.git
cd ChordSense
git checkout chordsense_play_along_full
git submodule update --init --recursive


Backend Setup:

cd ~/projects/ChordSenseOfficial/backend
python3.10 -m venv venv
source venv/bin/activate
pip install -r requirements.txt



The backend currently expects the model repo to have its own working Python environment:

cd ~/projects/ChordSenseOfficial/backend/model_repo
python3.10 -m venv venv
source venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt



Rust Install:

sudo apt update
sudo apt install -y curl build-essential pkg-config libasound2-dev
curl https://sh.rustup.rs -sSf | sh
source "$HOME/.cargo/env"


cargo --version
rustc --version



BuildFrontend:

cd ~/projects/ChordSenseOfficial/frontend
source "$HOME/.cargo/env"
cargo build --bin chordsense_audio_synced



RUNNING APP:

backend start:

cd ~/projects/ChordSenseOfficial/backend
source venv/bin/activate
python app.py


frontend start:

cd ~/projects/ChordSenseOfficial/frontend
source "$HOME/.cargo/env"
cargo run --bin chordsense_audio_synced
