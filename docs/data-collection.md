# Collecting guitar recordings

This branch allows **240 saved recordings** (24 major/minor chords x 10 takes).
The cap blocks new takes when full; it does not delete existing recordings.
The uploaded-song limit remains 10.

## Download from the companion website

1. Update the Pi backend and deploy this branch's `web/` application to the
   companion website. Both halves must be updated; pulling on the Pi alone
   does not update the hosted website.
2. Keep the browser and Pi on the same Wi-Fi. Use **Pair uploads** on the Pi
   and enter its code on the companion website as usual.
3. Record a take and press Stop. In the website's **Guitar Recordings** section,
   choose **Refresh recordings**.
4. Choose **Download WAV** for one take or **Download all (ZIP)** for the whole
   library. The downloads contain the original audio, without resampling or
   model processing, and leave the originals on the Pi.
5. Extract the ZIP. Fill in `labels.csv` with the session, actual played chord,
   technique, and optional notes. Listen to verify labels. `recordings.json`
   preserves the Pi's display titles and original filenames as a reference;
   model predictions are not used as labels.
6. Check the downloaded audio before explicitly deleting any originals in the
   Pi's Browse Tracks > Recordings screen. Export each session and keep each
   session's ZIP in its own folder.

The ZIP holds a snapshot of the current library. It is rebuilt for every
request; avoid recording or deleting takes while downloading. Unfinished,
empty, or invalid WAVs block the full ZIP; finished takes can still be
retrieved individually. If a take remains unavailable after Stop, inspect it
on the Pi. No training, automatic labeling, or cloud audio storage is added.
The Pi needs temporary free disk space approximately equal to the exported
WAVs; the browser also needs memory and disk space for the download. For 240
mono 44.1 kHz PCM16 takes of 10-20 seconds, the audio is roughly 202-404 MiB.
Use individual downloads if a full export is too large for your browser.

## Collection plan

Collect 10 separate attempts per chord across at least three sessions. Re-fret
between takes and vary strum direction, strength, and pickup setting. Keep one
later session aside for evaluation. Use one chord per take, about 10-20 seconds;
quiet gaps can be separated during training preparation. Keep separate folders
for sessions so repeated full-library exports do not become duplicate training
examples. The 240 cap includes any earlier recordings, mutes, or extra chords.

## Applying on the Pi

After reviewing, committing, and pushing `tk/chord-data-collection` from the laptop:

```bash
cd /home/chordsense/workspace/ChordSense
git status --short --branch
# Continue only with a clean working tree; preserve any local changes first.
git fetch origin
git switch --track origin/tk/chord-data-collection
# If already on this branch, use: git pull --ff-only origin tk/chord-data-collection
sudo systemctl restart chordsense-api.service
sudo systemctl status chordsense-api.service --no-pager
```

Use the existing API service name if this Pi's installation differs. These
changes do not require a native UI or iod rebuild. Restarting the backend
invalidates paired sessions, so pair again afterward. Deploy the same branch's
web app using the existing Vercel project and discovery configuration. A preview
URL must be in the Pi's exact allowed-origin configuration to pair successfully.
See `docs/web-device-architecture.md` for the existing deployment setup.

## Verification

Backend checks cover the 240 boundary, no automatic eviction, exact WAV/ZIP
bytes, blank labels, pairing/origin checks, unfinished takes, missing files,
path containment, and preservation of the native playback response. Browser
API checks cover authenticated recording routes and binary downloads. The
physical guitar-to-Pi-to-browser transfer still needs checking after deployment.
