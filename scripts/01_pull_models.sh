#!/usr/bin/env bash
# Pull the 'venue' profile model set (~28 GB total).
# Ollama stores these outside the venv, in ~/.ollama - they are model weights,
# not Python packages.
set -euo pipefail
MODELS=(
  "qwen3:14b"           # reasoning / planning       ~9 GB
  "qwen3:4b"            # routing + graph extraction ~2.6 GB
  "qwen2.5-coder:7b"    # coding                     ~4.7 GB
  "qwen2.5vl:7b"        # vision / scanned drawings  ~6 GB
  "bge-m3"              # embeddings                 ~1.2 GB
)
for m in "${MODELS[@]}"; do
  echo "==> pulling $m"
  ollama pull "$m"
done
echo "==> optional hero model (14 GB, fills the card alone):"
echo "    ollama pull gpt-oss:20b"
ollama list
