#!/bin/sh
# Prepares the source tree for a dfrunner build variant, inside the Docker builder
# (dfrunner/Dockerfile); never run on the host. Usage: prepare.sh <variant>
#   df    plain dfrunner (DataFusion 55.0.0 from crates.io); nothing to do
#   sail  Sail's JoinReorder rule: cargo feature `sail-join-reorder`; nothing to do here
#   jo    apache/datafusion#24456 backported onto 55.0.0: the crates.io 55.0.0 sources
#         of the three crates the patch touches are downloaded, checked against the
#         checksums in Cargo.lock, patched, and swapped in with [patch.crates-io];
#         Cargo.lock is replaced by patches/jo.Cargo.lock (same as Cargo.lock except
#         those three crates, which become path dependencies).
# Prints the variant detail recorded in the binary (dfrunner /status).
set -eu
variant=${1:-df}
case "$variant" in
  df) echo "DataFusion 55.0.0, default physical optimizer rules" ;;
  sail) echo "DataFusion 55.0.0 + lakehq/sail@86dea385 JoinReorder before JoinSelection, default JoinReorderOptions" ;;
  jo)
    here=$(pwd)
    patch_file=patches/datafusion-55.0.0-pr24456.patch
    src=/opt/datafusion-55.0.0
    for crate in common physical-optimizer physical-plan; do
      name="datafusion-$crate"
      sum=$(awk -v n="$name" '$0 == "name = \"" n "\"" {f=1} f && /^checksum/ {gsub(/"/, "", $3); print $3; exit}' Cargo.lock)
      [ -n "$sum" ] || { echo "no checksum for $name in Cargo.lock" >&2; exit 1; }
      curl -sSfL -o "/tmp/$name.crate" "https://static.crates.io/crates/$name/$name-55.0.0.crate"
      echo "$sum  /tmp/$name.crate" | sha256sum -c - >&2
      mkdir -p "$src/datafusion/$crate"
      tar xzf "/tmp/$name.crate" --strip-components=1 -C "$src/datafusion/$crate"
    done
    (cd "$src" && git apply --whitespace=nowarn "$here/$patch_file")
    cat patches/jo.cargo-patch.toml >> Cargo.toml
    cp patches/jo.Cargo.lock Cargo.lock
    echo "DataFusion 55.0.0 + apache/datafusion#24456@472e83e1 backport, patch sha256 $(sha256sum "$patch_file" | cut -c1-16)"
    ;;
  *) echo "unknown dfrunner variant: $variant (expected df, jo or sail)" >&2; exit 1 ;;
esac
