#!/bin/zsh
set -eu
TASK_DIR="${0:A:h}"
python3 "$TASK_DIR/tools/install.py"
