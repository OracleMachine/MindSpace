#!/usr/bin/env bash
# Launch one MindSpace agent from a profile YAML.
#
# Usage:
#   ./profiles/run.sh <profile>
#
# <profile> is any of:
#   - a profile name      (e.g. `mindspace`)      -> profiles/mindspace.yaml
#   - a bare filename     (e.g. `mindspace.yaml`) -> profiles/mindspace.yaml
#   - a relative path     (e.g. `./profiles/mindspace.yaml`)
#   - an absolute path    (e.g. `/etc/mindspace/myagent.yaml`)
#
# Each profile is a standalone bot configuration (Discord token, model
# settings, MCP servers, OpenViking block). To run multiple agents, invoke
# this script multiple times with different profiles — one process per
# agent.

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(dirname "$script_dir")"

usage() {
    local available
    available=$(
        find "$script_dir" -maxdepth 1 -type f -name '*.yaml' -printf '%f\n' \
            | sed 's/\.yaml$//' | sort | paste -sd, -
    )
    {
        echo "Usage: $0 <profile>"
        echo "  <profile> resolves against $script_dir/"
        echo "  Available profiles: ${available:-(none yet — add one to $script_dir/)}"
    } >&2
}

if [[ $# -ne 1 ]]; then
    usage
    exit 2
fi

arg="$1"
profile_path=""

if [[ "$arg" == /* || "$arg" == ./* || "$arg" == ../* || "$arg" == */* ]]; then
    # Looks like a path — use as-is.
    if [[ -f "$arg" ]]; then
        profile_path="$(readlink -f "$arg")"
    else
        echo "Profile path does not exist: $arg" >&2
        exit 2
    fi
else
    # Bare name or filename — look inside profiles/.
    for cand in \
        "$script_dir/$arg" \
        "$script_dir/$arg.yaml" \
        "$script_dir/$arg.yml"
    do
        if [[ -f "$cand" ]]; then
            profile_path="$(readlink -f "$cand")"
            break
        fi
    done
    if [[ -z "$profile_path" ]]; then
        echo "No profile found for '$arg' in $script_dir/" >&2
        usage
        exit 2
    fi
fi

# config.py reads MINDSPACE_CONFIG at import time; set it before Python runs.
export MINDSPACE_CONFIG="$profile_path"
# Make src/ importable without a separate PYTHONPATH dance from the user.
export PYTHONPATH="$repo_root/src${PYTHONPATH:+:$PYTHONPATH}"

exec python3 -m mindspace.main
