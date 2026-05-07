#!/usr/bin/env bash
# Build the React bundle for the presentations editor.
# Run from the presentations/ directory:
#   cd presentations && bash build.sh
# Or from repo root:
#   bash presentations/build.sh

set -e
cd "$(dirname "$0")"

if [ ! -d node_modules ]; then
  echo "[build] node_modules bulunamadı — npm install çalıştırılıyor..."
  npm install
fi

echo "[build] esbuild bundle oluşturuluyor..."
node_modules/.bin/esbuild static/js/editor/index.jsx \
  --bundle \
  --minify \
  --target=es2020 \
  --loader:.css=empty \
  --outfile=static/js/bundle.js

echo "[build] Tamam → static/js/bundle.js"
