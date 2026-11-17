#!/bin/sh
# Builds getools-<version>.zip for Plugins > Install from ZIP, using the
# version in getools/metadata.txt, and points the README's download link at it.
set -e
cd "$(dirname "$0")"

version=$(sed -n 's/^version=//p' getools/metadata.txt)
name="getools-$version.zip"
rm -f getools.zip getools-*.zip
zip -rq "$name" getools -x '*/__pycache__/*' '*.pyc'
sed -i.bak -E "s#getools(-[0-9][0-9.]*)?\.zip#$name#g" README.md && rm -f README.md.bak
echo "$name"
