#!/usr/bin/env bash
# Launch a MindSpace agent from a profile YAML.
# Usage:  ./profiles/run.sh <profile-filename>      (e.g. mindspace.yaml)
#         ./profiles/run.sh /abs/or/relative/path.yaml
#
# The repo is run directly from source (no `pip install`), so we just put
# src/ on PYTHONPATH and execute the main entry file.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(dirname "$script_dir")"

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 <profile-filename>   (e.g. mindspace.yaml)" >&2
    exit 2
fi

# Arg with a path separator is treated as a path; otherwise as a filename
# inside profiles/.
case "$1" in
    */*|/*) profile="$1" ;;
    *)      profile="$script_dir/$1" ;;
esac

if [[ ! -f "$profile" ]]; then
    echo "Profile not found: $profile" >&2
    exit 2
fi

export MINDSPACE_CONFIG="$(readlink -f "$profile")"
export PYTHONPATH="$repo_root/src${PYTHONPATH:+:$PYTHONPATH}"
exec python3 "$repo_root/src/mindspace/main.py"
