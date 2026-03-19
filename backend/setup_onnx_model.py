"""
setup_onnx_model.py — Export the local MiniLM model to ONNX format.

Run once before starting the backend for the first time:
    cd backend
    python setup_onnx_model.py

What it does:
  1. Checks that backend/models/minilm/ exists (safetensors weights)
  2. Installs optimum[onnxruntime] if not already present
  3. Exports the model to ONNX and saves it into the same directory
  4. Runs a quick smoke-test encode to confirm the ONNX backend works
  5. Reports the model file size

The SemanticClusterer loads from backend/models/minilm/ with backend="onnx".
After this script runs, inference is 2-3x faster than PyTorch on CPU.
"""

from __future__ import annotations

import os
import sys
import time

MODEL_DIR = os.path.join(os.path.dirname(__file__), "models", "minilm")
ONNX_FILE = os.path.join(MODEL_DIR, "model.onnx")
EXPECTED_SAFETENSORS = os.path.join(MODEL_DIR, "model.safetensors")


def check_prerequisites() -> None:
    if not os.path.isdir(MODEL_DIR):
        print(f"ERROR: Model directory not found: {MODEL_DIR}")
        print("Expected the MiniLM model to be saved there already.")
        sys.exit(1)

    if not os.path.isfile(EXPECTED_SAFETENSORS):
        print(f"ERROR: model.safetensors not found in {MODEL_DIR}")
        print("The base model weights are missing.")
        sys.exit(1)

    print(f"[1/4] Source model found: {MODEL_DIR}")


def ensure_optimum() -> None:
    try:
        import optimum.onnxruntime  # noqa: F401
        print("[2/4] optimum[onnxruntime] already installed")
    except ImportError:
        print("[2/4] Installing optimum[onnxruntime] ...")
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "optimum[onnxruntime]", "--quiet"],
            check=False,
        )
        if result.returncode != 0:
            print("ERROR: pip install failed. Run manually:")
            print("  pip install optimum[onnxruntime]")
            sys.exit(1)
        print("      optimum[onnxruntime] installed")


def export_to_onnx() -> None:
    if os.path.isfile(ONNX_FILE):
        size_mb = os.path.getsize(ONNX_FILE) / 1_048_576
        print(f"[3/4] model.onnx already exists ({size_mb:.1f} MB) — skipping export")
        return

    print("[3/4] Exporting model to ONNX ...")
    t0 = time.time()

    import warnings
    from optimum.onnxruntime import ORTModelForFeatureExtraction

    # Suppress deprecation warnings emitted by transformers internals during ONNX tracing.
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=".*torch_dtype.*deprecated.*", category=FutureWarning)
        warnings.filterwarnings("ignore", category=UserWarning, module="torch.jit")
        ort_model = ORTModelForFeatureExtraction.from_pretrained(MODEL_DIR, export=True)
    ort_model.save_pretrained(MODEL_DIR)

    elapsed = time.time() - t0

    onnx_files = [f for f in os.listdir(MODEL_DIR) if f.endswith(".onnx")]
    if not onnx_files:
        print("WARNING: No .onnx file found after export.")
        return

    size_mb = os.path.getsize(os.path.join(MODEL_DIR, onnx_files[0])) / 1_048_576
    print(f"      Exported {onnx_files[0]} ({size_mb:.1f} MB) in {elapsed:.1f}s")


def smoke_test() -> None:
    print("[4/4] Smoke-testing ONNX inference ...")
    from sentence_transformers import SentenceTransformer

    try:
        model = SentenceTransformer(MODEL_DIR, backend="onnx")
    except Exception:
        # Fall back to the first .onnx file found if the default path fails
        onnx_files = [f for f in os.listdir(MODEL_DIR) if f.endswith(".onnx")]
        if not onnx_files:
            print("WARNING: Could not load ONNX model — no .onnx file found")
            print("The clusterer will fall back to PyTorch at runtime (still works, just slower)")
            return
        model = SentenceTransformer(MODEL_DIR, backend="onnx")

    test_sentences = [
        "hello everyone welcome to the stream",
        "hi chat how is everyone doing today",
        "this streamer is the best check out my link",
    ]

    t0 = time.time()
    embeddings = model.encode(test_sentences, normalize_embeddings=True, show_progress_bar=False)
    elapsed_ms = (time.time() - t0) * 1000

    assert embeddings.shape == (3, 384), f"Unexpected shape: {embeddings.shape}"
    print(f"      OK — encoded {len(test_sentences)} sentences in {elapsed_ms:.1f}ms, shape={embeddings.shape}")
    print()
    print("Setup complete. The SemanticClusterer will use ONNX inference.")


def main() -> None:
    print("TwitchIDS — MiniLM ONNX setup")
    print("=" * 40)
    check_prerequisites()
    ensure_optimum()
    export_to_onnx()
    smoke_test()


if __name__ == "__main__":
    main()
