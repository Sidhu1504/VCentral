#!/usr/bin/env bash
# Build vCentral agent binaries for every supported platform, plus manifest.json with SHA-256 checksums.
#
#   agent/build.sh OUTDIR [VERSION] [GO120]
#
# GO120 is the path to a Go 1.20 `go` binary. It builds the "windows-legacy" agents for
# Windows Server 2008 R2 / 2012 / 2012 R2 and Windows 7 / 8.1, which newer Go releases no longer support.
# Without it, only the other platforms are built.
set -euo pipefail
cd "$(dirname "$0")"

OUT=$(mkdir -p "${1:?usage: build.sh OUTDIR [VERSION] [GO120]}" && cd "$1" && pwd)
VERSION=${2:-dev}
[[ "$VERSION" =~ ^[0-9A-Za-z._+-]+$ ]] || { echo "Invalid agent version '$VERSION' (letters, digits, . _ + - only)" >&2; exit 1; }
GO=${GO:-go}
GO120=${3:-}
LDFLAGS="-s -w -X main.Version=${VERSION}"

build() { # gobin goos goarch platform-name
  local file="vcentral-agent-$4"
  [ "$2" = windows ] && file="$file.exe"
  echo "  $4"
  CGO_ENABLED=0 GOOS=$2 GOARCH=$3 GOTOOLCHAIN=local "$1" build -trimpath -ldflags "$LDFLAGS -X main.Platform=$4" -o "$OUT/$file" .
}

echo "Building vCentral agent $VERSION into $OUT"
build "$GO" linux amd64 linux-amd64
build "$GO" linux 386 linux-386
build "$GO" linux arm64 linux-arm64
build "$GO" windows amd64 windows-amd64
build "$GO" windows 386 windows-386
if [ -n "$GO120" ]; then
  build "$GO120" windows amd64 windows-legacy-amd64
  build "$GO120" windows 386 windows-legacy-386
else
  echo "  (skipping windows-legacy: no Go 1.20 toolchain given)"
fi

# manifest.json: {"version": "...", "files": {"linux-amd64": {"file": "...", "sha256": "...", "size": N}, ...}}
{
  printf '{\n  "version": "%s",\n  "files": {\n' "$VERSION"
  first=1
  for f in "$OUT"/vcentral-agent-*; do
    name=$(basename "$f")
    platform=${name#vcentral-agent-}
    platform=${platform%.exe}
    sum=$(sha256sum "$f" | cut -d' ' -f1)
    size=$(wc -c < "$f" | tr -d ' ')
    [ $first -eq 1 ] || printf ',\n'
    first=0
    printf '    "%s": {"file": "%s", "sha256": "%s", "size": %s}' "$platform" "$name" "$sum" "$size"
  done
  printf '\n  }\n}\n'
} > "$OUT/manifest.json"
echo "Wrote $OUT/manifest.json"
