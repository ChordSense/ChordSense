## Research Papers
### Bi-Directional Transformer for Musical Chord Recognition

Link: https://archives.ismir.net/ismir2019/paper/000075.pdf

This paper explores the use of a bi-directional Transformer for chord recognition (BTC) to overcome the limitations of existing CNNs and RNNs. Traditional chord recognition pipelines consist of three stages: feature extraction, pattern matching, and chord sequence decoding — where the decoding step typically requires a separate model like an HMM or CRF. BTC replaces pattern matching and decoding with a single self-attention mechanism that looks both forward and backward in time, allowing it to capture long-term dependencies and segment chord boundaries adaptively. It takes CQT features as input and outputs chord probabilities directly, requiring only one training phase instead of the multi-stage pipelines of prior work. The authors visualize the attention maps to show that the model learns to attend to musically relevant regions, achieving competitive performance on standard benchmarks

### Intelligent Guitar Chord Recognition Using Spectrogram-Based Feature Extraction and AlexNet Architecture for Categorization

Link: https://thesai.org/Downloads/Volume16No4/Paper_75-Intelligent_Guitar_Chord_Recognition.pdf

This paper classifies eight major and minor guitar chords using deep learning. They compare three feature representations — spectrograms, chromagrams, and MFCCs — and find that standard spectrograms best capture the pitch and harmonic relationships needed for chord differentiation. Several architectures were tested (CNN, ResNet50, AlexNet, VGG-19), with AlexNet outperforming the others on their limited dataset while using the fewest computational resources. Notably relevant to our project: this is essentially the same task and scale as our Phase 1 (8 chord classes, small dataset, lightweight model), and their finding that AlexNet works well on small data aligns with our earlier experience adapting AlexNet — though we found its pooling layers collapse small inputs like our 12×15 chroma features, which led us to the custom ChordCNN architecture instead.

### SpectroFusionNet a CNN approach utilizing spectrogram fusion for electric guitar play recognition

Link: https://www.nature.com/articles/s41598-025-00287-w

This paper introduces a deep learning framework for recognition of electric guitar playing techniques across nine distinct sound classes (fingerpicking, strumming, hammer-ons, pull-offs, bending, slides, tapping, palm muting, etc.). They first extract features using Mel-Frequency Cepstral Coefficients (which capture the spectral envelope of the audio, representing timbral characteristics), Continuous Wavelet Transform (which provides multi-resolution time-frequency analysis, offering fine temporal detail for transient techniques), and Gammatone Spectrograms (which model the human auditory system's cochlear filtering, emphasizing perceptually relevant frequency bands). They then use lightweight models typically used for vision — MobileNetV2, InceptionV3, and ResNet50 — to extract features from these spectrograms, of which ResNet50 got the best results. They went on to use two fusion strategies for feature representation: early fusion, where spectrograms are combined before feature extraction, and late fusion, where independent features are concatenated via weighted averaging, max-voting, or simple concatenation. The best result came from MFCC-Gammatone late fusion. This might not be as relevant due to our current scope, but once we get to further stages of the project, it might prove useful for extracting playing patterns and distinguishing techniques beyond simple chord identity.

### Large-Vocabulary Chord Transcription Via Chord Structure Decomposition

Link: https://archives.ismir.net/ismir2019/paper/000078.pdf

This paper tackles the problem of recognizing chords beyond the typical major/minor vocabulary, where rare chord qualities (7ths, 9ths, sus, dim, etc.) have too few training samples for a standard classifier. The core idea is to decompose any chord label into musically meaningful components — root, triad type, bass note, seventh, ninth, eleventh, and thirteenth — each with a small vocabulary, and train a multitask CRNN to classify all components simultaneously. The individual predictions are then reassembled into the full chord label via a CRF decoder that penalizes excessive chord transitions. They use CQT features extracted with librosa and evaluate on the Isophonics/Billboard/MARL collections. Directly relevant to our latter stage plans: rather than training a single classifier with hundreds of output classes, this decomposition approach could let us scale from 8 chords to a much larger vocabulary without needing massive amounts of data per chord type.

### Real-Time Chord Recognition for Live Performance

Link: https://quod.lib.umich.edu/cache//b/b/p/bbp2372.2009.019/bbp2372.2009.019.pdf#page=1;zoom=75

This paper presents a lightweight, frame-by-frame chord recognition system designed for single-instrument live performance. The approach computes an improved chromagram from the audio spectrum — rather than summing energy in frequency bins, it takes the maximum peak in a range around each expected harmonic, which better handles slightly inharmonic signals. Classification works by masking out the expected note positions for each candidate chord template and picking the chord that minimizes residual energy. The system was implemented as a Max/MSP external for real-time use, and the work was later expanded in Stark's PhD thesis. This is the reference implementation behind our pure DSP baseline and the direct ancestor of the chromagram template matching approach we evaluated against the CNN path.

## Github Repos

### ChordMiniApp

Link: https://github.com/ptnghia-j/ChordMiniApp

This is the repo for the ChordMini application, which does chord recognition with the use of a pre-trained chord recognition model. They also have a web interface we can use as a reference, as it displays chords along with the original song, and it displays the suggested chord shapes

