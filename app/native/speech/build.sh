#!/bin/bash
# Build tandem-speech binary for macOS
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
swiftc "$DIR/tandem-speech.swift" \
  -framework Speech \
  -framework AVFoundation \
  -o "$DIR/tandem-speech"
echo "✅ tandem-speech compiled: $DIR/tandem-speech"
