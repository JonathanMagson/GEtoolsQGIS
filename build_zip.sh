#!/bin/sh
# Builds dist/getools.zip for Plugins > Install from ZIP.
set -e
cd "$(dirname "$0")"
mkdir -p dist
rm -f dist/getools.zip
zip -rq dist/getools.zip getools -x '*/__pycache__/*' '*.pyc'
echo dist/getools.zip
