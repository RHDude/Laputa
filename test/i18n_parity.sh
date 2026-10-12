#!/bin/bash
# Тексты событий в приложении (Java, уведомления) и в интерфейсе (JS) собираются одинаково на всех языках.
set -e
cd "$(dirname "$0")/.."
W=$(mktemp -d); cp test/parity/evs.json $W/
(cd ui && npx esbuild ../test/parity/js.mjs --bundle --platform=node --format=esm --outfile=$W/js.mjs --log-level=warning)
node $W/js.mjs $W
export JAVA_TOOL_OPTIONS=
javac --release 8 -Xlint:-options -nowarn -cp /usr/lib/android-sdk/platforms/android-23/android.jar -d $W/cls android/src/app/laputa/Net.java android/src/app/laputa/Checker.java android/src/app/laputa/T.java android/test/CheckerCli.java
bad=0
for l in ru en uk de es zh; do
  LAPUTA_I18N=ui/i18n LAPUTA_LANG=$l LAPUTA_EV=$W/evs.json java -Dfile.encoding=UTF-8 -Dstdout.encoding=UTF-8 -cp $W/cls:/usr/share/java/com.android.json.jar CheckerCli $W/evs.json $W/evs.json 0 2>/dev/null > $W/j-$l.txt
  if diff -q $W/js-$l.txt $W/j-$l.txt >/dev/null; then echo "  PASS  $l: $(wc -l < $W/j-$l.txt) событий совпадают"; else echo "  FAIL  $l"; diff $W/js-$l.txt $W/j-$l.txt | head -6; bad=1; fi
done
rm -rf $W
exit $bad
