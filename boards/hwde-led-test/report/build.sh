#!/bin/sh
# Rebuild the report: markdown -> tex (pandoc) -> PDF (lualatex, built in a temp dir).
# Needs pandoc on PATH (e.g. pip install pypandoc_binary) and lualatex.
set -eu
cd "$(dirname "$0")"
name=hwde-led-test-report
pandoc "$name.md" --standalone --toc --number-sections \
  -V geometry:margin=2cm -V colorlinks=true -V fontsize=10pt \
  -V mainfont="Latin Modern Roman" \
  -H preamble.tex -o "$name.tex"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
for _ in 1 2; do
  TEXINPUTS=".:$PWD//:" lualatex -interaction=nonstopmode -halt-on-error \
    -output-directory="$tmp" "$name.tex" >"$tmp/build.log" || { tail -30 "$tmp/build.log"; exit 1; }
done
cp "$tmp/$name.pdf" .
echo "built $PWD/$name.pdf"
