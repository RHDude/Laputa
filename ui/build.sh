#!/bin/sh
# Сборка интерфейса в ui/dist: один app.js, styles.css, шрифты.
set -e
cd "$(dirname "$0")"
OUT=${OUT:-dist}
rm -rf $OUT && mkdir -p $OUT/fonts
# DEV=1 оставляет макет с тестовыми данными (?mock=ok). Боевая сборка его не содержит.
npx esbuild src/app.js --bundle --minify --format=iife --target=chrome90 --define:__DEV__=${DEV:-0} --outfile=$OUT/app.js --log-level=warning
npx esbuild src/styles.css --minify --outfile=$OUT/styles.css --log-level=warning
cp index.html $OUT/
mkdir -p $OUT/i18n && cp i18n/*.json $OUT/i18n/
for f in onest-cyrillic-400 onest-latin-400 onest-cyrillic-500 onest-latin-500 onest-cyrillic-600 onest-latin-600; do cp node_modules/@fontsource/onest/files/$f-normal.woff2 $OUT/fonts/; done
for f in unbounded-cyrillic-500 unbounded-latin-500 unbounded-cyrillic-600 unbounded-latin-600; do cp node_modules/@fontsource/unbounded/files/$f-normal.woff2 $OUT/fonts/; done
du -sh $OUT | cut -f1
