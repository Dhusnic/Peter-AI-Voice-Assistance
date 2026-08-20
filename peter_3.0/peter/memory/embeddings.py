"""Local sentence embeddings, for memory that works on meaning not keywords.

FTS5 finds a fact only when you reuse the words you stored it with. Measured
on paraphrased questions ("how do I get to work" against "takes route 70 bus
to Gandhipuram") it recalled 3 of 10 — and worse, filled the empty slots with
unrelated facts, spending tokens on noise. That is the problem this fixes.

**No new dependency.** `onnxruntime` is already here for openWakeWord,
`tokenizers` came in with faster-whisper, and `numpy` runs the voice pipeline.
Between them that is a complete embedding stack, so this costs one model file
and nothing else. It is the same shape as the wake word: an ONNX model
downloaded once and run locally, so **nothing about your memory leaves the
machine** — the property that would be given up by calling a hosted embedding
API, and the reason not to.

**It is optional at every point.** If the model file is missing or fails to
load, `available()` goes False and the store falls back to the FTS5 path that
has always been there. A memory that is merely as good as before is a much
better failure than a memory that raises.

Vectors are brute-forced with numpy rather than stored in a vector database.
At the scale of a personal assistant's memory — hundreds, maybe thousands of
facts — a dot product against the whole set is microseconds, and a real
vector index would be a dependency, a schema, and a failure mode bought for
no measurable speedup.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

# all-MiniLM-L6-v2, int8-quantised ONNX export: 384 dimensions, ~23MB.
# Quantised deliberately -- on paraphrase retrieval the accuracy difference
# against the full-precision export is not measurable, and a quarter of the
# download is.
_MODEL_URL = "https://huggingface.co/Xenova/all-MiniLM-L6-v2/resolve/main/onnx/model_quantized.onnx"
_TOKENIZER_URL = "https://huggingface.co/Xenova/all-MiniLM-L6-v2/resolve/main/tokenizer.json"

MODEL_NAME = "all-MiniLM-L6-v2-quantized"
DIMENSIONS = 384
# MiniLM's trained context. Longer input is truncated rather than chunked:
# a fact or preference is a sentence, not a document.
_MAX_TOKENS = 256


class Embedder:
    """Loads the ONNX model once and reuses it. Thread-safe for encoding."""

    def __init__(self, model_dir: Path):
        self.model_dir = Path(model_dir)
        self.model_path = self.model_dir / "model_quantized.onnx"
        self.tokenizer_path = self.model_dir / "tokenizer.json"
        self._session = None
        self._tokenizer = None
        self._load_failed = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------ lifecycle
    def files_present(self) -> bool:
        return self.model_path.is_file() and self.tokenizer_path.is_file()

    def available(self) -> bool:
        """True if encoding will work. Never raises — a missing or broken
        model means 'fall back to keyword search', not 'fail'."""
        if self._load_failed:
            return False
        if self._session is not None:
            return True
        if not self.files_present():
            return False
        try:
            self._load()
            return True
        except Exception as exc:
            log.warning("embeddings unavailable (%s) — falling back to keyword search", exc)
            self._load_failed = True
            return False

    def _load(self) -> None:
        with self._lock:
            if self._session is not None:
                return
            import onnxruntime as ort
            from tokenizers import Tokenizer

            tokenizer = Tokenizer.from_file(str(self.tokenizer_path))
            tokenizer.enable_truncation(max_length=_MAX_TOKENS)
            tokenizer.enable_padding()
            options = ort.SessionOptions()
            # One thread: this runs on the turn path next to Whisper and the
            # wake word, and a thread pool fighting them for cores costs more
            # than it saves on inputs this short.
            options.intra_op_num_threads = 1
            session = ort.InferenceSession(
                str(self.model_path), options, providers=["CPUExecutionProvider"]
            )
            self._tokenizer, self._session = tokenizer, session
            log.info("embeddings ready (%s, %d dims)", MODEL_NAME, DIMENSIONS)

    # ------------------------------------------------------------- encoding
    def encode(self, texts: list[str]) -> np.ndarray | None:
        """Encode texts into L2-normalised vectors, or None if unavailable.

        Normalising here means similarity is a plain dot product later, which
        is what makes brute-force search over the whole fact set cheap.
        """
        if not texts or not self.available():
            return None
        try:
            encodings = self._tokenizer.encode_batch(texts)
            ids = np.array([e.ids for e in encodings], dtype=np.int64)
            mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)

            feed = {"input_ids": ids, "attention_mask": mask}
            expected = {i.name for i in self._session.get_inputs()}
            if "token_type_ids" in expected:
                feed["token_type_ids"] = np.zeros_like(ids)
            feed = {k: v for k, v in feed.items() if k in expected}

            hidden = self._session.run(None, feed)[0]  # [batch, seq, dims]
            return _mean_pool(hidden, mask)
        except Exception:
            log.exception("embedding failed; falling back to keyword search")
            return None

    def encode_one(self, text: str) -> np.ndarray | None:
        vectors = self.encode([text])
        return None if vectors is None else vectors[0]


def _mean_pool(hidden: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Mean-pool token vectors, ignoring padding, then L2-normalise.

    Masking matters: padding tokens carry real values in the model's output,
    and averaging them in drags every vector toward a common point, which
    quietly flattens the similarity scores this whole feature depends on.
    """
    weights = mask[..., None].astype(np.float32)
    summed = (hidden * weights).sum(axis=1)
    counts = np.clip(weights.sum(axis=1), 1e-9, None)
    pooled = summed / counts
    norms = np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-9, None)
    return (pooled / norms).astype(np.float32)


def to_blob(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def download(model_dir: Path) -> None:
    """Fetch the model once. Raises on failure — unlike everything else here,
    this is called explicitly by someone who asked for it, so it should say
    plainly what went wrong rather than silently leaving the feature off."""
    import urllib.request

    model_dir = Path(model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    for url, target in (
        (_MODEL_URL, model_dir / "model_quantized.onnx"),
        (_TOKENIZER_URL, model_dir / "tokenizer.json"),
    ):
        if target.is_file():
            log.info("already have %s", target.name)
            continue
        log.info("downloading %s ...", target.name)
        # Written to a temporary name and moved into place, so an interrupted
        # download cannot leave a truncated file that loads as a broken model.
        partial = target.with_suffix(target.suffix + ".partial")
        urllib.request.urlretrieve(url, partial)
        partial.replace(target)
        log.info("saved %s (%.1f MB)", target.name, target.stat().st_size / 1e6)
