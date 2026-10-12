#!/bin/bash
# Installer and agent test in a real distro environment (chroot into an unpacked image).
# There is no real systemd in a chroot: the bench's stub systemctl replaces it, the rest is real
# (useradd or adduser, nologin, /etc/os-release, file permissions, system or supplied Python).
#   distro_test.sh <name> <image archive> <port> [python dir] [nosystemd]
set -u
NAME=$1; TARBALL=$2; PORT=$3; PYDIR=${4:-}; MODE=${5:-}
D=${DISTRO_DIR:-/tmp/laputa-distro}
ROOT=$D/root-$NAME
SRC=$(cd "$(dirname "$0")/.." && pwd)
pass=0; fail=0
ok(){ if [ "$1" = 0 ]; then pass=$((pass+1)); echo "  PASS  $2"; else fail=$((fail+1)); echo "  FAIL  $2   ${3:-}"; fi; }
inroot(){ chroot "$ROOT" /usr/bin/env -i PATH=/fake:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin HOME=/root LANG=C.UTF-8 FAKE_UNITS=/tmp/units.json LAPUTA_SYSTEMD=/tmp/sd $INITENV "$@"; }
cleanup(){ pkill -f "$ROOT_TAG" 2>/dev/null; sleep 0.5; umount "$ROOT/proc" 2>/dev/null; umount "$ROOT/dev" 2>/dev/null; }

echo "== $NAME"
umount "$ROOT/proc" 2>/dev/null; rm -rf "$ROOT"; mkdir -p "$ROOT"
tar -xf "$TARBALL" -C "$ROOT" 2>/dev/null
mkdir -p "$ROOT/proc" "$ROOT/tmp/sd" "$ROOT/fake" "$ROOT/srv/lp"
mount -t proc proc "$ROOT/proc"
mkdir -p "$ROOT/dev" && mount --bind /dev "$ROOT/dev"
ROOT_TAG="/srv/lp/laputa.py"
if [ "$MODE" = nosystemd ]; then INITENV="LAPUTA_SPEED=5"; else INITENV="LAPUTA_SPEED=5 LAPUTA_INIT=systemd"; fi
cp "$SRC/server/laputa.py" "$ROOT/srv/lp/laputa.py"
cp "$SRC/test/bench/bin/systemctl" "$SRC/test/bench/bin/journalctl" "$ROOT/fake/"
echo '{"demo-bot.service":{"state":"active","desc":"Demo bot"},"sshd.service":{"state":"active","desc":"OpenSSH"}}' > "$ROOT/tmp/units.json"
if [ -n "$PYDIR" ]; then   # the image has no Python: supply a separate build
  mkdir -p "$ROOT/opt/py" && cp -a "$PYDIR/." "$ROOT/opt/py/" && ln -sf /opt/py/bin/python3 "$ROOT/usr/local/bin/python3"
fi
PYV=$(inroot python3 -c 'import sys; print(sys.version.split()[0])' 2>&1)
OSV=$(. "$ROOT/etc/os-release"; echo "$PRETTY_NAME")
echo "   система: $OSV, python $PYV, свой python: ${PYDIR:+подложен}${PYDIR:-системный}"
ok $([ -n "$PYV" ] && echo "$PYV" | grep -qE '^3\.' ; echo $?) "python запускается внутри образа" "$PYV"

# 1. without openssl the installer must say what to install
if ! inroot sh -c 'command -v openssl' >/dev/null 2>&1; then
  OUT=$(inroot python3 /srv/lp/laputa.py install-hub --host 127.0.0.1 --port $PORT --name Test 2>&1)
  ok $(echo "$OUT" | grep -q 'Нужна программа openssl' ; echo $?) "без openssl установщик называет пакет" "$OUT"
  # issue the certificate outside and drop it in, as someone without openssl would
  mkdir -p "$ROOT/etc/laputa"
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 30 -subj /CN=t -keyout "$ROOT/etc/laputa/tls.key" -out "$ROOT/etc/laputa/tls.crt" >/dev/null 2>&1
else
  echo "   openssl в образе есть: сертификат выпустит сам установщик"
fi

# 2. watchman install
OUT=$(inroot python3 /srv/lp/laputa.py install-hub --host 127.0.0.1 --port $PORT --name "Test-$NAME" --place тест 2>&1); RC=$?
ok $RC "install-hub завершился без ошибки" "$(echo "$OUT" | tail -3)"
ok $(inroot id laputa >/dev/null 2>&1; echo $?) "создан пользователь laputa" "$(inroot id laputa 2>&1)"
SHELL_=$(grep '^laputa:' "$ROOT/etc/passwd" | cut -d: -f7)
ok $(echo "$SHELL_" | grep -qE 'nologin|false'; echo $?) "у пользователя нет входа ($SHELL_)"
OWN=$(inroot stat -c '%U %a' /etc/laputa/hub.json /etc/laputa/tls.key /etc/laputa/agent.json 2>&1 | tr '\n' ';')
ok $([ "$OWN" = "laputa 600;laputa 600;root 600;" ]; echo $?) "права: ключи сторожа у laputa, настройки агента у root, всё 600" "$OWN"
ok $(echo "$OUT" | grep -q '^laputa1:' && echo "$OUT" | grep -q "pinnedpubkey 'sha256//" ; echo $?) "напечатаны ключ приложения и команда для узлов"
if [ "$MODE" = nosystemd ]; then
  ok $(echo "$OUT" | grep -q 'нет systemd' && [ ! -e "$ROOT/tmp/sd/laputa-hub.service" ]; echo $?) "без systemd: юниты не созданы, напечатано, как запускать" "$(echo "$OUT" | head -4)"
else
  EXEC=$(grep ExecStart "$ROOT/tmp/sd/laputa-hub.service" 2>/dev/null)
  PYBIN=$(echo "$EXEC" | sed 's/ExecStart=//; s/ .*//')
  ok $([ -n "$PYBIN" ] && [ -x "$ROOT$PYBIN" ]; echo $?) "юнит ссылается на настоящий путь python ($PYBIN)" "$EXEC"
fi

# 3. watchman as user laputa, agent as root
chroot --userspec=laputa "$ROOT" /usr/bin/env -i PATH=/usr/local/bin:/usr/bin:/bin LANG=C.UTF-8 $INITENV python3 /srv/lp/laputa.py hub > "$ROOT/tmp/hub.log" 2>&1 &
sleep 2
inroot python3 /srv/lp/laputa.py agent > "$ROOT/tmp/agent.log" 2>&1 &
sleep 6
ST=$(inroot python3 /srv/lp/laputa.py status 2>&1)
ok $(echo "$ST" | grep -q "Test-$NAME.*в строю"; echo $?) "сторож (от laputa) видит узел в строю" "$ST | $(tail -2 "$ROOT/tmp/hub.log")"
ok $(inroot stat -c '%U' /var/lib/laputa >/dev/null 2>&1 && [ "$(inroot stat -c '%U' /var/lib/laputa)" = laputa ]; echo $?) "каталог состояния принадлежит laputa"

# 4. what the agent reported about the system and services
cat > "$ROOT/tmp/q.py" <<'EOF'
import json, sys
sys.path.insert(0, '/srv/lp')
import laputa
cfg = laputa.jload('/etc/laputa/hub.json')
pin, spki = laputa.cert_pins()
hub = {'url': 'https://127.0.0.1:%d' % cfg['port'], 'pin': pin, 'token': cfg['app_token']}
st = laputa.hub_call(hub, 'GET', '/v1/state')
n = st['nodes'][0]
m = n['m']
disc = laputa.hub_call(hub, 'POST', '/v1/cmd', {'node': n['id'], 'type': 'svc.discover', 'args': {}}, timeout=40)
add = laputa.hub_call(hub, 'POST', '/v1/cmd', {'node': n['id'], 'type': 'svc.add', 'args': {'unit': 'demo-bot.service', 'label': 'Демо', 'kind': 'bot'}}, timeout=40)
import time; time.sleep(3)
n2 = laputa.hub_call(hub, 'GET', '/v1/state')['nodes'][0]
print(json.dumps({'os': m.get('os'), 'init': m.get('init'), 'mem': m['mem'][1] > 0, 'cores': m['cores'], 'disks': len(m['disks']), 'uptime': m['uptime'] > 0,
                  'disc_ok': disc.get('ok'), 'disc_err': disc.get('error', ''), 'units': [u['unit'] for u in disc.get('units', [])],
                  'add_ok': add.get('ok'), 'svc': [(s['label'], s['state']) for s in n2['services']], 'spki': spki}, ensure_ascii=False))
EOF
Q=$(inroot python3 /tmp/q.py 2>&1 | tail -1)
echo "   ответ узла: $Q"
ok $(echo "$Q" | grep -q '"mem": true' && echo "$Q" | grep -q '"uptime": true'; echo $?) "метрики из /proc читаются"
ok $(echo "$Q" | grep -qE '"os": "(Debian|AlmaLinux|Alpine)[^"]* [0-9]'; echo $?) "название системы взято из /etc/os-release"
if [ "$MODE" = nosystemd ]; then
  ok $(echo "$Q" | grep -q '"init": "none"' && echo "$Q" | grep -q 'нет systemd'; echo $?) "без systemd: службы честно недоступны, узел в строю"
else
  ok $(echo "$Q" | grep -q '"disc_ok": true' && echo "$Q" | grep -q 'demo-bot.service'; echo $?) "службы перечислены"
  ok $(echo "$Q" | grep -q '"Демо", "active"'; echo $?) "добавленная служба видна как работающая"
fi
WANT=$(openssl x509 -in "$ROOT/etc/laputa/tls.crt" -pubkey -noout | openssl pkey -pubin -outform der | openssl dgst -sha256 -binary | base64)
ok $(echo "$Q" | grep -qF "\"spki\": \"$WANT\""; echo $?) "отпечаток для curl совпал с посчитанным openssl"

# 5. uninstall
inroot python3 /srv/lp/laputa.py uninstall >/dev/null 2>&1
ok $([ ! -e "$ROOT/etc/laputa" ] && [ ! -e "$ROOT/opt/laputa" ]; echo $?) "uninstall убрал настройки и программу"
cleanup
echo "   итог $NAME: прошло $pass, не прошло $fail"
[ $fail = 0 ]
