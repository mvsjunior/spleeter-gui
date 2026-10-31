#!/usr/bin/env bash
# Measures each engine configuration on one audio file and appends to a TSV.
#
# Usage: scripts/benchmark.sh AUDIO OUTDIR [CASE_REGEX]
#   CASE_REGEX selects cases by name; run with "list" as AUDIO to see them.
#
# Peak RSS comes from `time -v`: it is the largest single process in the tree,
# not the sum of them. The first run of a model includes its download, so warm
# the models up before trusting the numbers.
set -uo pipefail

# awk and printf must parse "52.44" as a number under pt_BR too.
unset LC_ALL
export LC_NUMERIC=C

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODELS="$HOME/.local/share/spleeter-gui/models"
SPLEETER="$ROOT/engines/spleeter/.venv/bin/spleeter"
DEMUCS_PY="$ROOT/engines/demucs/.venv/bin/python"

# name|engine|threads|engine arguments
cases() {
    local n
    for n in 2 4 5; do
        echo "sp-${n}stems-t4|spleeter|4|-p spleeter:${n}stems"
        echo "sp-${n}stems-mwf-t4|spleeter|4|-p spleeter:${n}stems --mwf"
        echo "sp-${n}stems-16k-t4|spleeter|4|-p spleeter:${n}stems-16kHz"
        echo "sp-${n}stems-16k-mwf-t4|spleeter|4|-p spleeter:${n}stems-16kHz --mwf"
    done
    echo "sp-2stems-t2|spleeter|2|-p spleeter:2stems"
    echo "sp-4stems-t2|spleeter|2|-p spleeter:4stems"
    echo "dm-htdemucs-wav-t4|demucs|4|-n htdemucs"
    echo "dm-htdemucs-wav-t2|demucs|2|-n htdemucs"
    echo "dm-htdemucs-flac-t4|demucs|4|-n htdemucs --flac"
    echo "dm-htdemucs-mp3-t4|demucs|4|-n htdemucs --mp3"
    echo "dm-htdemucs-int24-t4|demucs|4|-n htdemucs --int24"
    echo "dm-htdemucs-float32-t4|demucs|4|-n htdemucs --float32"
    echo "dm-htdemucs_6s-t4|demucs|4|-n htdemucs_6s"
    echo "dm-htdemucs_ft-t4|demucs|4|-n htdemucs_ft"
}

if [[ "${1:-}" == "list" ]]; then
    cases | cut -d'|' -f1
    exit 0
fi
if [[ $# -lt 2 ]]; then
    sed -n '2,8p' "${BASH_SOURCE[0]}"
    exit 2
fi

audio="$(realpath "$1")"
outdir="$2"
filter="${3:-.}"
mkdir -p "$outdir" "$MODELS/spleeter" "$MODELS/demucs"
total="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$audio")" || exit 1
tsv="$outdir/results.tsv"
[[ -f "$tsv" ]] || printf 'case\taudio_s\texit\twall_s\tratio\tcpu_pct\tpeak_rss_mb\toutputs\tstem_s\n' >"$tsv"

run_case() {
    local name="$1" engine="$2" threads="$3" args="$4"
    local work="$outdir/$name" log="$outdir/$name.log" timing="$outdir/$name.time"
    local -a argv
    rm -rf "$work"
    mkdir -p "$work"
    read -ra extra <<<"$args"
    if [[ "$engine" == spleeter ]]; then
        argv=("$SPLEETER" separate "${extra[@]}" -d "$total" -o "$work" -f "{instrument}.{codec}" "$audio")
    else
        argv=("$DEMUCS_PY" -m demucs "${extra[@]}" -d cpu -o "$work" --filename "{stem}.{ext}" "$audio")
    fi

    # MKL caps itself at the physical cores unless its dynamic mode is off.
    OMP_NUM_THREADS="$threads" TF_NUM_INTRAOP_THREADS="$threads" MKL_NUM_THREADS="$threads" MKL_DYNAMIC=FALSE \
        MODEL_PATH="$MODELS/spleeter" TORCH_HOME="$MODELS/demucs" \
        /usr/bin/time -v -o "$timing" "${argv[@]}" </dev/null >"$log" 2>&1
    local code=$?

    local wall cpu rss outputs first stem_s ratio
    wall="$(awk -F': ' '/Elapsed \(wall clock\)/ {
        n = split($NF, p, ":"); s = 0; for (i = 1; i <= n; i++) s = s * 60 + p[i]; print s }' "$timing")"
    cpu="$(awk -F': ' '/Percent of CPU/ { sub("%", "", $2); print $2 }' "$timing")"
    rss="$(awk -F': ' '/Maximum resident/ { printf "%d", $2 / 1024 }' "$timing")"
    outputs="$(find "$work" -type f | wc -l)"
    first="$(find "$work" -type f | sort | head -n 1)"
    stem_s="-"
    [[ -n "$first" ]] && stem_s="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$first" 2>/dev/null)"
    ratio="$(awk -v w="$wall" -v t="$total" 'BEGIN { printf "%.2f", w / t }')"
    printf '%s\t%.1f\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$name" "$total" "$code" "$wall" "$ratio" "$cpu" "$rss" "$outputs" "$stem_s" | tee -a "$tsv"
}

while IFS='|' read -r name engine threads args; do
    if [[ "$name" =~ $filter ]]; then
        run_case "$name" "$engine" "$threads" "$args"
    fi
done < <(cases)
