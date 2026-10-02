#!/bin/sh
# Builds getools.zip for Plugins > Install from ZIP.
set -e
cd "$(dirname "$0")"

rm -f getools.zip
zip -rq getools.zip getools -x '*/__pycache__/*' '*.pyc'
echo getools.zip
