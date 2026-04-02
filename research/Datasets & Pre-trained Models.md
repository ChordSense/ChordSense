
### Datasets

**GuitarChordsV3**: https://www.kaggle.com/datasets/fabianavinci/guitar-chords-v3

- ~130 audio recordings of limited chords (Am, Bb, Bdim, C, Dm, Em, F, G)
- Although limited in quantity, has a lot of variety in the recordings, as they do both acoustic and electric

**MyRecordings**

- Recordings made by the ChordSense team
- Very limited but can be tailored to what we need. Will likely use for testing models
- Can expand if needed

**rodriler/isolated-guitar-chords**: https://huggingface.co/datasets/severyn-k/isolated-guitar-chords

- Don't need to store locally, can be directly loaded into program
- 633 recordings of isolated chords
- Includes 24 chord signatures, plus noise
- Very complete, but only played in one acoustic guitar
- Already split in train/test

**Synthetically Generated Data**:

- Might be worth exploring, not a lot of information here yet

### Pre-trained Models

**MobileNetV2**: Lightweight model, can use as a benchmark against the custom CNN, or possibly fine-tuned for chord recognition

**Chord-cnn-lstm-model**: https://github.com/ptnghia-j/chord-cnn-lstm-model.git

- Model used in ChordMini, based on the *# Large-Vocabulary Chord Transcription via Chord Structure Decomposition* paper model, but further trained with student/teacher technique.
- Will use for full song chord decomposition, due to its high accuracy