"""
Train custom OWW-compatible wake word classifiers.

Pipeline
────────
1. Load processed clips from dataset_processed/{keyword}/ + dataset_processed/{keyword}_aug/
2. Extract OWW embeddings: audio → mel → embedding model → [16, 96] windows
3. Build train/val split (80/20) with negative samples
4. Train sklearn MLPClassifier (matches OWW's binary classifier architecture)
5. Export as ONNX with input [1,16,96] → output [1,1] (float32)
6. Save to ~/.xyron/wake_models/{keyword}.onnx

Usage
─────
  python -m voice.tools.train_wake_words                    # train all
  python -m voice.tools.train_wake_words --keyword hey_xyron
  python -m voice.tools.train_wake_words --keyword hey_xyron --epochs 50

Dependencies: onnxruntime, numpy, scikit-learn, skl2onnx
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Optional

_BACKEND = Path(__file__).parent.parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATASET_DIR    = Path(__file__).parent.parent.parent.parent / "dataset_processed"
WAKE_MODELS_DIR = Path(os.path.expanduser("~/.xyron/wake_models"))

SR         = 16_000
CLIP_SAMP  = int(SR * 2.0)   # 2-second clips
FRAME_SAMP = 1280             # 80ms OWW frame
EMB_DIM    = 96
WIN_SIZE   = 16               # OWW uses 16-step embedding window

KEYWORDS = [
    "hey_xyron",
    "wakeup_xyron",
    "xyron",
    "hey_xeron",
    "hi_xyron",
]


# ── OWW Feature Extraction ────────────────────────────────────────────────────

def _get_oww_models() -> tuple[object, object]:
    """Load OWW mel + embedding ONNX models. Returns (mel_sess, emb_sess)."""
    import onnxruntime as ort
    try:
        import openwakeword as _oww
        builtin = Path(_oww.__file__).parent / "resources" / "models"
    except Exception:
        raise RuntimeError("openwakeword not installed — run: pip install openwakeword")

    mel_path = str(builtin / "melspectrogram.onnx")
    emb_path = str(builtin / "embedding_model.onnx")
    if not os.path.exists(mel_path):
        raise FileNotFoundError(f"OWW mel model not found: {mel_path}")

    mel_sess = ort.InferenceSession(mel_path)
    emb_sess = ort.InferenceSession(emb_path)
    return mel_sess, emb_sess


def _extract_frame_embedding(
    pcm_1280: np.ndarray,
    mel_sess,
    emb_sess,
) -> Optional[np.ndarray]:
    """Extract 96-dim OWW embedding from one 1280-sample frame."""
    try:
        frame = pcm_1280.astype(np.float32).reshape(1, 1280)
        mel   = mel_sess.run(None, {"input": frame})[0]       # [1,1,T,32]
        n_frames = mel.shape[2]
        if n_frames < 76:
            pad = np.zeros((1, 1, 76 - n_frames, 32), dtype=np.float32)
            mel = np.concatenate([mel, pad], axis=2)
        mel_chunk = mel[0, 0, :76, :].reshape(1, 76, 32, 1).astype(np.float32)
        emb = emb_sess.run(None, {"input_1": mel_chunk})[0]   # [1,1,1,96]
        return emb.reshape(EMB_DIM).astype(np.float32)
    except Exception as exc:
        log.debug("Embedding error: %s", exc)
        return None


def _audio_to_windows(
    audio: np.ndarray,
    mel_sess,
    emb_sess,
) -> list[np.ndarray]:
    """
    Convert an audio clip to a list of [16,96] embedding windows.
    Slides frame-by-frame through the clip (stride=1 frame = 80ms).
    """
    # Normalize
    rms = float(np.sqrt(np.mean(audio ** 2)))
    if rms > 0.001:
        audio = np.clip(audio * (0.1 / rms), -1.0, 1.0)

    # Pad to full clip length
    if len(audio) < CLIP_SAMP:
        audio = np.concatenate([audio, np.zeros(CLIP_SAMP - len(audio), dtype=np.float32)])

    # Extract embeddings frame by frame
    embeddings: list[np.ndarray] = []
    for i in range(0, len(audio) - FRAME_SAMP + 1, FRAME_SAMP):
        frame = audio[i : i + FRAME_SAMP]
        emb = _extract_frame_embedding(frame, mel_sess, emb_sess)
        if emb is not None:
            embeddings.append(emb)

    # Slide a WIN_SIZE window over the embeddings with stride 1
    windows: list[np.ndarray] = []
    buf = np.zeros((WIN_SIZE, EMB_DIM), dtype=np.float32)
    for emb in embeddings:
        buf[:-1] = buf[1:]
        buf[-1]  = emb
        windows.append(buf.copy())

    return windows


def _load_clips_from_dir(kw_dir: Path) -> list[np.ndarray]:
    if not kw_dir.exists():
        return []
    clips = []
    for wav_path in sorted(kw_dir.glob("*.wav")):
        try:
            import soundfile as sf
            audio, sr = sf.read(str(wav_path), dtype="float32", always_2d=False)
            if audio.ndim > 1:
                audio = audio.mean(axis=1)
            if sr != SR:
                import scipy.signal as sps
                audio = sps.resample(audio, int(len(audio) * SR / sr)).astype(np.float32)
            clips.append(audio.astype(np.float32))
        except Exception as exc:
            log.warning("Skipping %s: %s", wav_path.name, exc)
    return clips


# ── Training ──────────────────────────────────────────────────────────────────

def build_dataset(
    keyword: str,
    mel_sess,
    emb_sess,
) -> tuple[np.ndarray, np.ndarray]:
    """Build (X, y) training arrays for one keyword vs negatives."""
    # Positive clips: keyword + augmented keyword
    pos_clips = (
        _load_clips_from_dir(DATASET_DIR / keyword) +
        _load_clips_from_dir(DATASET_DIR / f"{keyword}_aug")
    )
    # Negative clips: all other keyword dirs + explicit negatives
    neg_clips: list[np.ndarray] = []
    for other in DATASET_DIR.iterdir():
        if other.is_dir() and other.name != keyword and not other.name.endswith("_aug"):
            neg_clips += _load_clips_from_dir(other)
    # Also negatives from the augmented other keywords (up to 2x)
    for other in DATASET_DIR.iterdir():
        if other.is_dir() and other.name.endswith("_aug") and not other.name.startswith(keyword):
            neg_clips += _load_clips_from_dir(other)

    if not pos_clips:
        raise ValueError(f"No positive clips found for {keyword} — record and preprocess first.")
    if not neg_clips:
        raise ValueError("No negative clips found — record 'negative' keyword first.")

    log.info("[%s] pos=%d neg=%d clips → extracting embeddings...",
             keyword, len(pos_clips), len(neg_clips))

    X_pos, X_neg = [], []

    for clip in pos_clips:
        windows = _audio_to_windows(clip, mel_sess, emb_sess)
        X_pos.extend(windows)

    for clip in neg_clips:
        windows = _audio_to_windows(clip, mel_sess, emb_sess)
        X_neg.extend(windows)

    log.info("[%s] windows — pos=%d neg=%d", keyword, len(X_pos), len(X_neg))

    # Balance: downsample negatives to 3× positives to avoid class imbalance
    rng = np.random.default_rng(42)
    if len(X_neg) > 3 * len(X_pos):
        idx = rng.choice(len(X_neg), 3 * len(X_pos), replace=False)
        X_neg = [X_neg[i] for i in idx]

    X = np.array(X_pos + X_neg, dtype=np.float32).reshape(-1, WIN_SIZE * EMB_DIM)
    y = np.array([1] * len(X_pos) + [0] * len(X_neg), dtype=np.int32)

    # Shuffle
    perm = rng.permutation(len(X))
    return X[perm], y[perm]


def train_keyword(keyword: str, max_iter: int = 100) -> Path:
    """Train a single keyword model and export to ONNX. Returns output path."""
    mel_sess, emb_sess = _get_oww_models()

    X, y = build_dataset(keyword, mel_sess, emb_sess)
    log.info("[%s] dataset shape: X=%s  class_balance=%.1f%%",
             keyword, X.shape, 100 * y.mean())

    # Train/val split
    n_train = int(0.8 * len(X))
    X_train, X_val = X[:n_train], X[n_train:]
    y_train, y_val = y[:n_train], y[n_train:]

    from sklearn.neural_network import MLPClassifier
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import Pipeline

    pipeline = Pipeline([
        ("scaler", StandardScaler()),
        ("mlp",    MLPClassifier(
            hidden_layer_sizes=(64, 32),
            activation="relu",
            max_iter=max_iter,
            random_state=42,
            verbose=False,
            early_stopping=True,
            validation_fraction=0.1,
            n_iter_no_change=10,
        )),
    ])

    log.info("[%s] training MLP (max_iter=%d)...", keyword, max_iter)
    pipeline.fit(X_train, y_train)

    from sklearn.metrics import classification_report
    y_pred = pipeline.predict(X_val)
    report = classification_report(y_val, y_pred, target_names=["negative", keyword], zero_division=0)
    log.info("[%s] validation report:\n%s", keyword, report)

    # Export to ONNX
    return _export_onnx(pipeline, keyword)


def _export_onnx(pipeline, keyword: str) -> Path:
    """Export sklearn pipeline to ONNX with shape [1,16,96] → [1,1]."""
    try:
        from skl2onnx import convert_sklearn
        from skl2onnx.common.data_types import FloatTensorType
    except ImportError:
        raise RuntimeError("skl2onnx not installed — run: pip install skl2onnx")

    input_dim = WIN_SIZE * EMB_DIM  # 1536

    # Convert pipeline to ONNX (input is flat [1,1536])
    initial_type = [("X", FloatTensorType([None, input_dim]))]
    onnx_model = convert_sklearn(
        pipeline,
        initial_types=initial_type,
        target_opset=12,
        options={"zipmap": False},
    )

    # We need to wrap this so the input is [1,16,96] → reshape → pipeline → [1,1] output.
    # Use onnx graph manipulation to add a reshape node at the front.
    import onnx
    from onnx import helper, TensorProto

    # Build a wrapper graph: input [1,16,96] → Reshape([1,1536]) → original model subgraph
    # Simpler approach: add a Reshape op before the model's first node.
    graph    = onnx_model.graph
    orig_in  = graph.input[0]

    # Add the shape constant for reshape
    shape_val = np.array([1, input_dim], dtype=np.int64)
    shape_init = helper.make_tensor("reshape_shape", TensorProto.INT64, [2], shape_val.tolist())
    graph.initializer.insert(0, shape_init)

    # Rename original input to an intermediate name
    new_input_name = "raw_input_16_96"
    reshape_output = orig_in.name  # keep original name flowing into the rest of graph

    reshape_node = helper.make_node(
        "Reshape",
        inputs=[new_input_name, "reshape_shape"],
        outputs=[reshape_output],
        name="input_reshape",
    )
    graph.node.insert(0, reshape_node)

    # Replace the graph input with [1,16,96] shape
    new_input = helper.make_tensor_value_info(new_input_name, TensorProto.FLOAT, [1, 16, 96])
    del graph.input[0]
    graph.input.insert(0, new_input)

    # Fix the graph output to be [1,1] float
    # skl2onnx outputs label (int64) + probabilities — we want probabilities[:,1] as [1,1]
    # Add a Gather + Reshape to extract P(positive)
    _add_probability_output(onnx_model, keyword)

    WAKE_MODELS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = WAKE_MODELS_DIR / f"{keyword}.onnx"

    onnx.save(onnx_model, str(out_path))
    log.info("[%s] Saved ONNX model → %s", keyword, out_path)
    return out_path


def _add_probability_output(onnx_model, keyword: str) -> None:
    """Post-process the ONNX graph to output [1,1] confidence float."""
    import onnx
    from onnx import helper, TensorProto

    graph = onnx_model.graph

    # skl2onnx outputs: label (output_label) + probabilities (output_probability)
    # output_probability is a sequence-of-maps in some versions, or a [N,2] array.
    # We need a single [1,1] float confidence for the positive class.

    # Find probability output name
    prob_name = None
    for out in graph.output:
        if "prob" in out.name.lower():
            prob_name = out.name
            break
    if prob_name is None and len(graph.output) >= 2:
        prob_name = graph.output[1].name

    if prob_name is None:
        log.warning("[%s] Cannot find probability output — model may not work with WakeWordService", keyword)
        return

    # Add: Gather(prob, index=1, axis=1) → Reshape([1,1])
    idx_tensor = helper.make_tensor("pos_class_idx", TensorProto.INT64, [], [1])
    graph.initializer.append(idx_tensor)

    gather_out = f"_pos_prob_gathered"
    reshape_out = f"_confidence_out"

    gather_node = helper.make_node(
        "Gather",
        inputs=[prob_name, "pos_class_idx"],
        outputs=[gather_out],
        axis=1,
    )
    shape_1x1 = helper.make_tensor("conf_shape", TensorProto.INT64, [2], [1, 1])
    graph.initializer.append(shape_1x1)
    reshape_node = helper.make_node(
        "Reshape",
        inputs=[gather_out, "conf_shape"],
        outputs=[reshape_out],
    )

    graph.node.extend([gather_node, reshape_node])

    # Replace graph outputs with just the confidence
    del graph.output[:]
    conf_out = helper.make_tensor_value_info(reshape_out, TensorProto.FLOAT, [1, 1])
    graph.output.append(conf_out)

    try:
        import onnx
        onnx.checker.check_model(onnx_model)
        log.info("[%s] ONNX graph validation passed", keyword)
    except Exception as exc:
        log.warning("[%s] ONNX validation warning: %s", keyword, exc)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Train OWW-compatible wake word models")
    parser.add_argument("--keyword", choices=KEYWORDS, help="Single keyword (default: all)")
    parser.add_argument("--epochs",  type=int, default=100, help="MLP max iterations")
    args = parser.parse_args()

    keywords = [args.keyword] if args.keyword else KEYWORDS

    for kw in keywords:
        try:
            out = train_keyword(kw, max_iter=args.epochs)
            print(f"  ✓ {kw} → {out}")
        except Exception as exc:
            log.error("[%s] training failed: %s", kw, exc)

    print(f"\nModels saved to: {WAKE_MODELS_DIR}")
    print("Restart the backend to load them.")


if __name__ == "__main__":
    main()
