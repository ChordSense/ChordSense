import csv
import io
import json
import os
import tempfile
import unittest
import wave
import zipfile
from pathlib import Path
from unittest.mock import patch

import app as backend_app
import library_titles
import recordings_library as library
from pairing import PairingManager

ORIGIN = 'https://chordsense.vercel.app'


class RecordingExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.captures = self.root / 'captures'
        self.captures.mkdir()
        for module, name, value in (
            (library, 'CAPTURES_DIR', self.captures),
            (library, 'INDEX_FILE', self.root / 'index.json'),
            (library_titles, 'TITLES_FILE', self.root / 'titles.json'),
        ):
            p = patch.object(module, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.dict(os.environ, {'CHORDSENSE_ALLOWED_ORIGINS': ORIGIN})
        p.start()
        self.addCleanup(p.stop)
        manager = PairingManager(publisher=None)
        p = patch.dict(backend_app.app.extensions, {'chordsense_pairing': manager})
        p.start()
        self.addCleanup(p.stop)
        token = manager.claim(manager.start()['code'], ORIGIN)['token']
        self.headers = {'Origin': ORIGIN, 'Authorization': f'Bearer {token}'}
        self.client = backend_app.app.test_client()

    def capture(self, name='capture-1.wav', frames=100):
        path = self.captures / name
        with wave.open(str(path), 'wb') as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(44100)
            audio.writeframes(b'\x12\x34' * frames)
        return path

    def get(self, path):
        return self.client.get(path, headers=self.headers)

    def test_native_delete_accepts_object_body_and_removes_only_selected_recording(self):
        removed = self.capture()
        kept = self.capture('capture-2.wav')
        row = next(r for r in library.list_recordings() if r['wav_path'] == str(removed))
        endpoint = f"/api/recordings/{row['id']}"
        rejected = self.client.delete(endpoint, data='null', content_type='application/json')
        self.assertEqual(rejected.status_code, 400)
        self.assertTrue(removed.exists())
        response = self.client.delete(endpoint, json={})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])
        self.assertFalse(removed.exists())
        self.assertTrue(kept.exists())
        rows = self.client.get('/api/recordings').get_json()['recordings']
        self.assertEqual([r['wav_path'] for r in rows], [str(kept)])

    def test_listing_and_download_preserve_original_bytes_and_hide_device_paths(self):
        path = self.capture()
        row = library.list_recordings()[0]
        library.rename_recording(row['id'], 'Am session 1 take 1')
        response = self.get('/api/recordings')
        data = response.get_json()
        self.assertEqual(data['limit'], 240)
        self.assertNotIn(str(self.captures), response.get_data(as_text=True))
        recording = data['recordings'][0]
        self.assertEqual(recording['name'], 'Am session 1 take 1')
        self.assertTrue(recording['ready'])
        self.assertAlmostEqual(recording['duration'], 100 / 44100)
        with self.get(recording['download_url']) as audio:
            self.assertEqual(audio.data, path.read_bytes())
            self.assertEqual(audio.mimetype, 'audio/wav')
            self.assertIn('attachment', audio.headers['Content-Disposition'])
            self.assertEqual(audio.headers['Cache-Control'], 'private, no-store')
        # Native playback still receives its existing path-based contract.
        native = self.client.get('/api/recordings').get_json()['recordings'][0]
        self.assertEqual(native['wav_path'], str(path))

    def test_zip_has_exact_audio_and_blank_labels_not_predictions(self):
        files = [self.capture(), self.capture('capture-2.wav', 200)]
        library.save_recording_analysis(files[0], [{'chord': 'WRONG'}], 99)
        with self.get('/api/recordings/export') as response:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content_length, len(response.data))
            with zipfile.ZipFile(io.BytesIO(response.data)) as bundle:
                self.assertEqual(set(bundle.namelist()), {p.name for p in files} | {'labels.csv', 'recordings.json'})
                for path in files:
                    self.assertEqual(bundle.read(path.name), path.read_bytes())
                rows = list(csv.DictReader(io.StringIO(bundle.read('labels.csv').decode())))
                self.assertEqual({row['file'] for row in rows}, {p.name for p in files})
                self.assertTrue(all(row['label'] == '' and row['session'] == '' for row in rows))
                metadata = json.loads(bundle.read('recordings.json'))
                self.assertNotIn('wav_path', metadata[0])
                self.assertNotIn('WRONG', bundle.read('recordings.json').decode())
        self.assertTrue(all(p.exists() for p in files))

    def test_recording_routes_require_pairing_and_exact_origin(self):
        self.capture()
        for route in ['/api/recordings', '/api/recordings/1/audio', '/api/recordings/export']:
            with self.subTest(route=route):
                self.assertEqual(self.client.get(route, headers={'Origin': ORIGIN}).status_code, 401)
                bad = {**self.headers, 'Origin': 'https://wrong.example'}
                self.assertEqual(self.client.get(route, headers=bad).status_code, 403)
                response = self.client.options(route, headers={
                    'Origin': ORIGIN, 'Access-Control-Request-Method': 'GET',
                    'Access-Control-Request-Headers': 'Authorization',
                })
                self.assertEqual(response.status_code, 204)

    def test_remote_download_permission_does_not_enable_deletion_or_hardware_controls(self):
        path = self.capture()
        for route in ['/api/recordings/1', '/api/recordings/export', '/api/recordings/1/audio']:
            self.assertEqual(self.client.delete(route, headers=self.headers).status_code, 403)
        for route in ['/begin_recording', '/end_recording', '/api/library/title']:
            self.assertEqual(self.client.post(route, headers=self.headers).status_code, 403)
        self.assertTrue(path.exists())

    def test_unfinished_and_corrupt_wavs_are_not_exported(self):
        path = self.capture(frames=0)
        for data in [path.read_bytes(), b'broken WAV']:
            path.write_bytes(data)
            self.assertFalse(self.get('/api/recordings').get_json()['recordings'][0]['ready'])
            for route in ['/api/recordings/1/audio', '/api/recordings/export']:
                response = self.get(route)
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.get_json()['error_code'], 'recording_not_ready')

    def test_missing_and_traversal_requests_cannot_download_other_files(self):
        self.assertEqual(self.get('/api/recordings/export').status_code, 404)
        self.assertEqual(self.get('/api/recordings/999/audio').status_code, 404)
        self.assertEqual(self.get('/api/recordings/../titles.json/audio').status_code, 403)
        outside = self.root / 'capture-outside.wav'
        outside.write_bytes(b'private')
        library.INDEX_FILE.write_text(json.dumps({'next_id': 2, 'recordings': [
            {'id': 1, 'wav_path': str(outside), 'chords': [], 'duration': 0}
        ]}))
        self.assertEqual(self.get('/api/recordings/1/audio').status_code, 404)


if __name__ == '__main__':
    unittest.main()
