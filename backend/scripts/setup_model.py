"""
Model setup script — downloads all-MiniLM-L6-v2 and exports it to ONNX.

Run once before first launch:
    python scripts/setup_model.py

Output: backend/models/minilm/  (ONNX model + tokenizer files)
"""

from __future__ import annotations

import os
import sys

# Run from backend/ directory
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models", "minilm")


def main() -> None:
    print(f"Downloading {MODEL_NAME} and exporting to ONNX...")
    print(f"Output: {OUTPUT_DIR}\n")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Try ONNX export via optimum (optional, 2-3x speedup)
    try:
        from optimum.onnxruntime import ORTModelForFeatureExtraction
        from transformers import AutoTokenizer

        print("Exporting via optimum (ONNX)...")
        model = ORTModelForFeatureExtraction.from_pretrained(
            MODEL_NAME, export=True
        )
        tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
        model.save_pretrained(OUTPUT_DIR)
        tokenizer.save_pretrained(OUTPUT_DIR)
        print(f"ONNX model saved to {OUTPUT_DIR}")

    except ImportError:
        # Fall back to plain sentence-transformers save
        print("optimum not installed — saving as sentence-transformers format (no ONNX).")
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(MODEL_NAME)
        model.save(OUTPUT_DIR)
        print(f"Model saved to {OUTPUT_DIR}")

    # Quick smoke test
    print("\nSmoke test...")
    from sentence_transformers import SentenceTransformer

    try:
        m = SentenceTransformer(OUTPUT_DIR, backend="onnx")
    except Exception:
        m = SentenceTransformer(OUTPUT_DIR)

    vecs = m.encode(["hello world", "this is a test"], normalize_embeddings=True)
    assert vecs.shape == (2, 384), f"Unexpected shape: {vecs.shape}"
    print(f"Embedding shape: {vecs.shape}  ✓")
    print("\nModel setup complete.")


if __name__ == "__main__":
    main()
