# External CNN-BiLSTM Hailo feasibility: findings and decision

## Outcome

The existing five-fold external CNN-BiLSTM should remain on the CPU for
arbitrary-length songs. We found technically valid ways to run portions of it
on Hailo-10H, but none met both requirements:

1. preserve the original decoded chord output; and
2. improve end-to-end latency on the Raspberry Pi.

No experimental Hailo path is enabled in production. The custom CNN continues
to use Hailo independently of this decision.

## What was tested

The external pipeline is:

```text
audio -> CQT -> five CNN-BiLSTM folds -> probability average -> XHMM -> LAB
```

The work tested three partitions:

1. compile the complete CNN-BiLSTM;
2. run the CNN on Hailo and retain the BiLSTM and decoder on the CPU; and
3. split the CNN at every normalization layer, alternating between Hailo
   convolutions and CPU normalization, activation, and pooling.

The acceptance gate was the final observable result, not just similar logits.
A candidate had to preserve the six output heads, five-fold ensemble, decoded
frame labels, and final LAB labels and boundaries.

## Test environment

| Component | Environment |
|---|---|
| Runtime | Raspberry Pi 5, 8 GB (`chordsense`) |
| Accelerator | Hailo-10H over PCIe 3.0 x1 |
| Runtime software | HailoRT, driver, and firmware 5.1.1 |
| Compiler | Hailo AI Software Suite Docker on x86 Linux (`colossus-dev`) |
| Compiler version | Dataflow Compiler 5.4.0 |

Compiler time on `colossus-dev` was never counted as song-analysis latency.
All CPU, device, transfer, and end-to-end latency figures below were measured
or projected from component measurements on the Raspberry Pi.

## Reproducible baseline

`backend/tools/lstm_baseline.py` runs the untouched external pipeline and
captures:

- input and model hashes;
- CQT dimensions;
- per-fold logits and probabilities;
- six-head ensemble probabilities;
- decoded frame labels and final LAB segments;
- phase timing and peak memory.

It also compares a candidate archive against a golden archive and can fail
unless decoded frames and the final LAB output are exactly equal.

Two golden inputs were reproduced on both the development Mac and Pi:

| Input | Duration | CQT frames | LAB segments | LAB SHA-256 |
|---|---:|---:|---:|---|
| Perfect excerpt | 34.8876 s | 1,503 | 22 | `f3e842cad8e27ea00b8ac744abe63de20599094886668f702f2c7c63572f364d` |
| When I Was Your Man | 214.1576 s | 9,224 | 97 | `8b9c92fe8ce3180039fda8debf9c1c4a1f3e79f959e3fdb6fbb843c2e9184753` |

## Why the complete model did not work

Hailo's compiler represents the bidirectional LSTM by unrolling its time
steps. At only 32 input frames, the compiler expanded it into 64 repeated
blocks and 1,205 distinct layers, then rejected the graph as too large. A full
song has thousands of frames, so whole-song LSTM compilation is not practical.

Shortening or independently chunking the LSTM is not equivalent. Its backward
direction uses future frames, so changing the sequence boundary can change its
output.

## Why CNN-only acceleration did not generalize

The complete CNN for one fixed 1,503-frame input successfully compiled and ran
on Hailo-10H. With 16-bit quantization, replacing fold 0's CPU CNN preserved
every final decoded frame and the complete LAB file. Device execution was
approximately 362 ms for that fold.

This result was limited to that exact tensor length. Hailo requires a static
input shape, while the CNN uses `InstanceNorm` statistics calculated across
the whole song. Padding a song changes those statistics. On the 9,224-frame
fixture, adding only eight reflected frames changed a decoded chord frame.

Consequently, fixed-length buckets and ordinary independent chunks could not
meet the unchanged-output requirement.

## Why the exact staged CNN was slower

An equivalent staged design was possible: run each local convolution on Hailo,
stitch its chunks, and run whole-song `InstanceNorm`, SELU, and pooling on the
CPU. A 512-frame chunk with a one-frame halo preserved the host CNN math. A
physical fold-0 `conv1a` test also preserved all 1,503 final decoded frames and
the exact LAB output.

The problem was overhead. Each fold contains ten convolution stages, and the
model uses five folds. Preserving the normalization semantics therefore
requires many CPU/Hailo boundaries and repeated quantization, transfer,
stitching, normalization, and repacking.

Native `UINT16` device I/O was essential: the representative `conv1a` call was
29.06 ms at p50 with native I/O versus 67.12 ms with `FLOAT32`. Even after that
optimization, boundary handling dominated the long-song projection.

| Projected component | 34.89 s input | 214.16 s input |
|---|---:|---:|
| Non-CNN pipeline floor | 2.494 s | 6.776 s |
| Ten Hailo stages across five folds | 2.551 s | 11.668 s |
| CPU boundary handling across five folds | 2.541 s | 15.891 s |
| **Projected staged total** | **7.585 s** | **34.335 s** |

This projection is optimistic: it uses representative stages for repeated
shapes and excludes additional application orchestration.

## Same-Pi comparison

| Path | 34.89 s input | 214.16 s input |
|---|---:|---:|
| External model, current CPU request | 7.386 s | 28.524 s |
| External model, preloaded CPU estimate | 5.386 s | 25.369 s |
| Custom CNN, Hailo warm | 3.148 s | 20.569 s |
| External staged Hailo projection | 7.585 s | 34.335 s |

The staged design was already slower than the current CPU request, and the gap
grew with song length. Producing the remaining fold-specific HEFs could not
reverse that result because the projection already assumes favorable component
costs.

The custom model is a speed comparator, not a drop-in accuracy replacement. It
has one small 25-class CNN, while the external system has five sequence models,
six structured chord heads, and an XHMM decoder.

## Decision

- Keep arbitrary-length external-model inference on the existing CPU path.
- Do not merge or enable the staged Hailo implementation.
- Do not generate a library of length-specific HEFs.
- Retain the baseline capture/comparison tool as the regression contract for
  future optimization work.

## Useful follow-up options

The lowest-risk performance opportunity is to preload and reuse the unchanged
external CPU models instead of loading them in a new subprocess for every
request. Existing measurements indicate roughly 2 to 3 seconds of request
overhead could be removed without changing model math. This was identified but
not implemented by the Hailo feasibility work.

A general Hailo solution would require changing and retraining the model—for
example, replacing whole-song `InstanceNorm` and the BiLSTM with fixed-statistic
normalization and a chunkable temporal convolution network. That would be a new
model and would require a separate accuracy study.

## Baseline usage

Capture a golden result using the external model's Python environment:

```bash
python backend/tools/lstm_baseline.py capture song.wav \
  --output-dir artifacts/lstm-baseline/song \
  --warmups 1 \
  --repeats 5
```

Compare a candidate result and require the final output to remain unchanged:

```bash
python backend/tools/lstm_baseline.py compare \
  artifacts/lstm-baseline/song/baseline.npz \
  candidate.npz \
  --output artifacts/lstm-baseline/song/comparison.json \
  --require-unchanged
```

Generated fixtures belong under `artifacts/`, which is intentionally excluded
from version control.
