#!/bin/sh
# Entry point of Dockerfile.compare: run every engine against one article.
#
#   compare_all.sh /in/article.md [--full] [--max-chars N] [--ref-audio FILE] ...
#
# Extra arguments go to both runs. A failing engine is reported and the rest
# still run. Results from both runs land in /out (index.html lists them all).
set -u

article=${1:?usage: compare_all.sh ARTICLE [compare_tts.py options]}
shift

cd /app

echo "=== Kokoro and edge-tts ==="
# The NVIDIA wheels' lib directories, which the Kokoro image registers with
# ldconfig. Set only for this run so they cannot shadow PyTorch's own copies.
nvidia_libs=$(find /opt/kokoro -type d -path '*/nvidia/*/lib' | paste -sd:)
LD_LIBRARY_PATH="$nvidia_libs:${LD_LIBRARY_PATH:-}" \
KOKORO_MODEL_DIR=/app/models/kokoro \
KOKORO_PROVIDER=cuda \
/opt/kokoro/bin/python scripts/compare_tts.py "$article" --out /out \
    --engines kokoro,edge \
    --kokoro-voice af_heart --kokoro-voice bf_emma --kokoro-voice am_michael \
    --edge-voice en-US-AriaNeural --edge-voice en-US-AndrewNeural "$@"

echo
echo "=== Chatterbox Turbo and Chatterbox ==="
/opt/chatterbox/bin/python scripts/compare_tts.py "$article" --out /out \
    --engines chatterbox-turbo,chatterbox "$@"

echo
echo "Done. Open index.html in the output folder."
