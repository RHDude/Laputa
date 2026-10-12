#!/bin/bash
# Builds the APK without Gradle: aapt2 + javac + dx + apksigner from the Ubuntu repositories.
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION=$(grep -o 'android:versionName="[^"]*"' android/AndroidManifest.xml | cut -d'"' -f2)
AJ=/usr/lib/android-sdk/platforms/android-23/android.jar
DX=/usr/lib/android-sdk/build-tools/debian/dx
OUT=android/out
export JAVA_TOOL_OPTIONS=
rm -rf $OUT && mkdir -p $OUT/assets/ui $OUT/classes $OUT/gen dist
(cd ui && ./build.sh >/dev/null)
cp -r ui/dist/. $OUT/assets/ui/
aapt2 compile --dir android/res -o $OUT/res.zip
aapt2 link -I $AJ --manifest android/AndroidManifest.xml -A $OUT/assets --java $OUT/gen -o $OUT/base.apk $OUT/res.zip
javac --release 8 -Xlint:-options -nowarn -cp $AJ -d $OUT/classes $(find android/src $OUT/gen -name '*.java')
$DX --dex --min-sdk-version=26 --output=$OUT/classes.dex $OUT/classes
cp $OUT/base.apk $OUT/unsigned.apk
(cd $OUT && zip -q -j unsigned.apk classes.dex)
zipalign -f 4 $OUT/unsigned.apk $OUT/aligned.apk
# The signing key is never in the repository. Point LAPUTA_KEYSTORE at a folder with laputa.jks and pass.txt.
# Without a key a new one is created; an APK signed with another key will not install over the old one.
KS=${LAPUTA_KEYSTORE:-keystore}; [ -f $KS/laputa.jks ] || [ -n "${LAPUTA_KEYSTORE:-}" ] || { [ -f ../keystore/laputa.jks ] && KS=../keystore; }
if [ ! -f $KS/laputa.jks ]; then
  mkdir -p $KS && head -c 24 /dev/urandom | base64 | tr -d '/+=' > $KS/pass.txt && chmod 600 $KS/pass.txt
  keytool -genkeypair -keystore $KS/laputa.jks -alias laputa -keyalg RSA -keysize 3072 -validity 10000 -dname CN=Laputa \
    -storepass "$(cat $KS/pass.txt)" -keypass "$(cat $KS/pass.txt)" >/dev/null 2>&1
  echo "Created a new signing key in $KS. Keep it: updates will not install without it."
fi
PASS=$(cat $KS/pass.txt)
apksigner sign --ks $KS/laputa.jks --ks-key-alias laputa --ks-pass "pass:$PASS" --key-pass "pass:$PASS" --out dist/Laputa-$VERSION.apk $OUT/aligned.apk
rm -f dist/Laputa-$VERSION.apk.idsig
apksigner verify -v dist/Laputa-$VERSION.apk | grep -E 'Verifies|scheme \(APK Signature Scheme v[23]\)'
aapt dump badging dist/Laputa-$VERSION.apk | grep -E "^package|sdkVersion|targetSdk|uses-permission|launchable|application-label:"
ls -la dist/Laputa-$VERSION.apk
