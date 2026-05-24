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

echo "[build] esbuild editor bundle oluşturuluyor..."
node_modules/.bin/esbuild static/js/editor/index.jsx \
  --bundle \
  --jsx=automatic \
  --minify \
  --target=es2020 \
  --loader:.css=empty \
  --outfile=static/js/bundle.js

echo "[build] esbuild hazırlık bundle oluşturuluyor..."
# NOTE: no --loader:.css=empty here — the Hazırlık entry imports
# @xyflow/react's CSS, which esbuild bundles into hazirlik.bundle.css.
node_modules/.bin/esbuild static/js/hazirlik/index.jsx \
  --bundle \
  --jsx=automatic \
  --minify \
  --target=es2020 \
  --outfile=static/js/hazirlik.bundle.js

echo "[build] esbuild keşif bundle oluşturuluyor..."
# Phase 9.a Atölye / Keşif — no CSS imports inside the JSX (kesif.css is
# served as a separate stylesheet via the template).
node_modules/.bin/esbuild static/js/kesif/index.jsx \
  --bundle \
  --jsx=automatic \
  --minify \
  --target=es2020 \
  --loader:.css=empty \
  --outfile=static/js/kesif.bundle.js

echo "[build] Tamam → static/js/bundle.js + static/js/hazirlik.bundle.js (+ .css) + static/js/kesif.bundle.js"
