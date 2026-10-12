#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Laputa: agent and watchman (hub) for a personal server panel.

One file, Python 3.7+ standard library only. Runs on any Linux: metrics come from /proc,
services are visible where systemd exists, containers where Docker exists.

  agent   collects metrics and service states, connects out to the watchmen (no inbound ports),
          runs whitelisted panel commands. Runs as root.
  hub     receives agent signals, decides who went silent, keeps the event log
          and answers the app. Runs as an unprivileged user.

Commands:
  laputa.py install-hub   --host ADDRESS [--port 8743] [--name NAME] [--place PLACE] [--open-port] [--lang en|ru]
  laputa.py install-agent --hub KEY [--hub KEY2] [--name NAME] [--place PLACE] [--lang en|ru]
  laputa.py keys          print the keys and the command for adding nodes
  laputa.py rekey         issue a new app key (the old one stops working)
  laputa.py status        what the local watchman sees, or which watchmen the agent reports to
  laputa.py uninstall     remove the services and settings from this server

Node actions (buttons in the app) are defined in /etc/laputa/actions.ini.
"""

import argparse
import base64
import collections
import hashlib
import hmac
import ipaddress
import http.client
import http.server
import json
import os
import re
import secrets
import shutil
import signal
import socket
import socketserver
import statistics
import ssl
import struct
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit, parse_qs

VERSION = '1.2.0'

if sys.version_info < (3, 7):
    sys.exit('Laputa needs Python 3.7+ (here %d.%d). / Laputa нужен Python 3.7 или новее.' % sys.version_info[:2])

ETC = os.environ.get('LAPUTA_ETC', '/etc/laputa')
VAR = os.environ.get('LAPUTA_VAR', '/var/lib/laputa')
OPT = os.environ.get('LAPUTA_OPT', '/opt/laputa')
HUB_CFG = os.path.join(ETC, 'hub.json')
AGENT_CFG = os.path.join(ETC, 'agent.json')
TLS_CRT = os.path.join(ETC, 'tls.crt')
TLS_KEY = os.path.join(ETC, 'tls.key')
HUB_STATE = os.path.join(VAR, 'hub-state.json')
AGENT_STATE = os.path.join(ETC, 'agent-state.json')
ACTIONS_FILE = os.path.join(ETC, 'actions.ini')


def _pick_lang():
    """Server message language (console, journal): LAPUTA_LANG, then the install-time choice, then the locale."""
    v = os.environ.get('LAPUTA_LANG', '').lower()[:2]
    if v in ('ru', 'en'):
        return v
    for p in (AGENT_CFG, HUB_CFG):
        try:
            with open(p, encoding='utf-8') as f:
                return 'en' if json.load(f).get('lang') == 'en' else 'ru'  # installs before 1.2 were Russian-only
        except (OSError, ValueError, AttributeError):
            pass
    loc = (os.environ.get('LC_ALL') or os.environ.get('LC_MESSAGES') or os.environ.get('LANG') or '').lower()[:2]
    return 'ru' if loc in ('ru', 'be', 'kk') else 'en'


SRV_LANG = _pick_lang()


def L(ru, en):
    return en if SRV_LANG == 'en' else ru


BOOT_ID_FILE = os.environ.get('LAPUTA_BOOT_ID', '/proc/sys/kernel/random/boot_id')
REBOOT_FILE = os.environ.get('LAPUTA_REBOOT_FILE', '/var/run/reboot-required')

_SPEED = float(os.environ.get('LAPUTA_SPEED', '1'))  # test bench only: speeds up all timers
BEAT_EVERY = 10 / _SPEED  # s between agent signals
STALE_AFTER = 35 / _SPEED  # s without a signal: late
DOWN_AFTER = 75 / _SPEED  # s without a signal: silent
PEER_FRESH = 40 / _SPEED  # s, max age of the other watchman's view


def _envf(name, default):
    # test bench overrides
    return float(os.environ.get(name, default))


BOOT_CHECK = _envf('LAPUTA_BOOT_CHECK', 180 / _SPEED)  # s after boot: check that all services came up
REBOOT_MUTE = 600 / _SPEED  # s of silence for a node rebooted from the panel
REBOOT_POLL = _envf('LAPUTA_REBOOT_POLL', 60 / _SPEED)  # s between "is the node empty" checks
REBOOT_HOUR = _envf('LAPUTA_REBOOT_HOUR', 3600 / _SPEED)  # seconds per hour of "when empty" waiting
WARN_LEAD = _envf('LAPUTA_WARN_LEAD', 300 / _SPEED)  # s from the first player warning to the reboot
MAINT_EVERY = 6 * 3600 / _SPEED  # s between update checks
MAINT_FIRST = _envf('LAPUTA_MAINT_FIRST', 120 / _SPEED)
SUGGEST_EVERY = _envf('LAPUTA_SUGGEST_EVERY', 1800 / _SPEED)  # s between service discovery runs
CAND_AGE = _envf('LAPUTA_CAND_AGE', 3600 / _SPEED)  # a service must run this long to be suggested
PROC = os.environ.get('LAPUTA_PROC', '/proc')
BACKUP_ROOTS = os.environ.get('LAPUTA_BACKUP_ROOTS', '/opt:/srv:/var/backups:/root:/home:/backup:/backups:/mnt:/var/lib:/data').split(':')
LOGIN_EVERY = 20 / _SPEED
GROUP_WINDOW = 180 / _SPEED  # nodes of one place going silent within this window count as one incident
SYSTEMD_DIR = os.environ.get('LAPUTA_SYSTEMD', '/etc/systemd/system')
# Init system. Services need systemd; elsewhere the node works without them.
INIT = os.environ.get('LAPUTA_INIT') or ('systemd' if os.path.isdir('/run/systemd/system') else 'none')
NO_INIT = 'На этом узле нет systemd, поэтому службы панель здесь не видит. Метрики и тревога о молчании работают.'
DISK_HIGH = 90.0
DISK_CLEAR = 85.0
KINDS = ('bot', 'panel', 'game', 'vpn', 'media', 'web', 'monitor', 'base', 'other')
UNIT_RE = re.compile(r'^[A-Za-z0-9@:._\\-]{1,200}\.service$')
DOCKER_RE = re.compile(r'^docker:[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')
NODE_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,31}$')


def log(*a):
    print(time.strftime('%Y-%m-%d %H:%M:%S'), *a, flush=True)


class Err(ValueError):
    """Error for the app: a code and params so it can show the message in the user's language, plus a fallback text."""

    def __init__(self, code, text, **p):
        super().__init__(text)
        self.code = code
        self.p = p


# ---------------------------------------------------------------- common

def jload(path, default=None):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def jsave(path, obj, mode=0o600):
    tmp = path + '.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, separators=(',', ':'))
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def make_key(prefix, obj):
    raw = json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return prefix + ':' + base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def read_key(prefix, text):
    m = re.search(re.escape(prefix) + r':([A-Za-z0-9_\-=]+)', text or '')
    if not m:
        raise ValueError(L('ключ должен начинаться с «%s:»', 'the key must start with "%s:"') % prefix)
    b = m.group(1)
    b += '=' * (-len(b) % 4)
    return json.loads(base64.urlsafe_b64decode(b).decode('utf-8'))


def sh(args, timeout=20, inp=None):
    try:
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, input=inp)
        return p.returncode, p.stdout.decode('utf-8', 'replace')
    except subprocess.TimeoutExpired:
        return 124, L('команда не уложилась в %d с', 'command timed out after %d s') % timeout
    except OSError as e:
        return 127, str(e)


def slug(s):
    s = re.sub(r'[^a-z0-9_-]+', '-', (s or '').lower()).strip('-')[:32]
    return s or 'node'


def plural(n, forms):
    a = abs(n) % 100
    b = a % 10
    if 10 < a < 20:
        return forms[2]
    if 1 < b < 5:
        return forms[1]
    if b == 1:
        return forms[0]
    return forms[2]


def dur_text(sec):
    sec = int(max(0, sec))
    if sec < 60:
        return '%d с' % sec
    m = sec // 60
    if m < 60:
        return '%d мин' % m
    h = m // 60
    if h < 24:
        return '%d ч %d мин' % (h, m % 60) if m % 60 else '%d ч' % h
    return '%d д %d ч' % (h // 24, h % 24) if h % 24 else '%d д' % (h // 24)


class HubError(Exception):
    pass


def hub_call(hub, method, path, body=None, timeout=15):
    """Request to a watchman, checking its certificate fingerprint."""
    u = urlsplit(hub['url'])
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # trust comes from the pinned fingerprint below, not the chain
    conn = http.client.HTTPSConnection(u.hostname, u.port or 443, timeout=timeout, context=ctx)
    try:
        conn.connect()
        pin = (hub.get('pin') or '').lower()
        got = hashlib.sha256(conn.sock.getpeercert(True)).hexdigest()
        if not pin or not hmac.compare_digest(pin, got):
            raise HubError(L('сертификат сторожа не совпал с отпечатком', 'hub certificate does not match the pin'))
        data = json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode('utf-8') if body is not None else None
        headers = {'Authorization': 'Bearer ' + hub['token'], 'Connection': 'close'}
        if data is not None:
            headers['Content-Type'] = 'application/json'
        conn.request(method, path, body=data, headers=headers)
        r = conn.getresponse()
        raw = r.read()
        if r.status != 200:
            raise HubError(L('сторож ответил %d', 'hub answered %d') % r.status)
        return json.loads(raw.decode('utf-8')) if raw else {}
    finally:
        conn.close()


# ---------------------------------------------------------------- metrics

FS_OK = {'ext2', 'ext3', 'ext4', 'xfs', 'btrfs', 'zfs', 'f2fs', 'jfs', 'reiserfs', 'bcachefs'}
NET_SKIP = ('lo', 'veth', 'docker', 'br-', 'virbr', 'awg', 'wg', 'tun', 'tap', 'tailscale', 'dummy', 'sit', 'ip6tnl')


def read_cpu():
    with open('/proc/stat') as f:
        p = f.readline().split()[1:]
    v = [int(x) for x in p[:8]]
    idle = v[3] + (v[4] if len(v) > 4 else 0)
    return sum(v), idle


def read_net():
    rx = tx = 0
    try:
        with open('/proc/net/dev') as f:
            for line in f.readlines()[2:]:
                name, _, rest = line.partition(':')
                name = name.strip()
                if name.startswith(NET_SKIP):
                    continue
                p = rest.split()
                rx += int(p[0])
                tx += int(p[8])
    except (OSError, ValueError, IndexError):
        pass
    return rx, tx


def read_mem():
    d = {}
    with open('/proc/meminfo') as f:
        for line in f:
            k, _, v = line.partition(':')
            d[k] = int(v.split()[0]) * 1024
    total = d.get('MemTotal', 0)
    avail = d.get('MemAvailable', d.get('MemFree', 0))
    st = d.get('SwapTotal', 0)
    return [total - avail, total], [st - d.get('SwapFree', 0), st]


def read_disks():
    seen = {}
    try:
        with open('/proc/mounts') as f:
            rows = [l.split() for l in f]
    except OSError:
        return []
    rows.sort(key=lambda r: len(r[1]) if len(r) > 1 else 0)
    out = []
    for r in rows:
        if len(r) < 3:
            continue
        dev, mnt, fs = r[0], r[1].replace('\\040', ' '), r[2]
        if fs not in FS_OK or dev in seen:
            continue
        if mnt.startswith(('/boot', '/snap', '/run', '/var/lib/docker', '/var/lib/containers')):
            continue
        try:
            s = os.statvfs(mnt)
        except OSError:
            continue
        used = (s.f_blocks - s.f_bfree) * s.f_frsize
        total = used + s.f_bavail * s.f_frsize
        if total < 512 * 1024 * 1024:
            continue
        seen[dev] = 1
        out.append({'mount': mnt, 'used': used, 'total': total})
        if len(out) >= 6:
            break
    out.sort(key=lambda d: d['mount'])
    return out


def read_temp():
    best = None
    paths = []
    try:
        for z in os.listdir('/sys/class/thermal'):
            if z.startswith('thermal_zone'):
                paths.append('/sys/class/thermal/%s/temp' % z)
    except OSError:
        pass
    try:
        for h in os.listdir('/sys/class/hwmon'):
            base = '/sys/class/hwmon/' + h
            for n in os.listdir(base):
                if re.match(r'temp\d+_input$', n):
                    paths.append(base + '/' + n)
    except OSError:
        pass
    for p in paths:
        try:
            with open(p) as f:
                v = int(f.read().strip()) / 1000.0
            if 1 < v < 125 and (best is None or v > best):
                best = v
        except (OSError, ValueError):
            pass
    return round(best, 1) if best is not None else None


def os_name():
    d = {}
    try:
        with open('/etc/os-release') as f:
            for line in f:
                k, _, v = line.strip().partition('=')
                d[k] = v.strip('"')
    except OSError:
        pass
    name = d.get('NAME', 'Linux').replace(' GNU/Linux', '')
    ver = d.get('VERSION_ID', '')
    return (name + ' ' + ver).strip()


class Sampler(threading.Thread):
    """CPU load and network rate, sampled every 5 seconds."""

    def __init__(self):
        super().__init__(daemon=True)
        self.cpu = 0.0
        self.net = [0, 0]
        self._c = read_cpu()
        self._n = read_net()
        self._t = time.monotonic()

    def run(self):
        while True:
            time.sleep(5)
            try:
                c = read_cpu()
                n = read_net()
                t = time.monotonic()
                dt = c[0] - self._c[0]
                if dt > 0:
                    self.cpu = round(max(0.0, min(100.0, 100.0 * (1 - (c[1] - self._c[1]) / dt))), 1)
                el = max(0.5, t - self._t)
                self.net = [int(max(0, n[0] - self._n[0]) / el), int(max(0, n[1] - self._n[1]) / el)]
                self._c, self._n, self._t = c, n, t
            except Exception as e:  # metrics must never crash the agent
                log(L('метрики:', 'metrics:'), e)


def metrics(sampler):
    mem, swap = read_mem()
    try:
        with open('/proc/loadavg') as f:
            load = [float(x) for x in f.read().split()[:3]]
    except (OSError, ValueError):
        load = [0, 0, 0]
    try:
        with open(os.environ.get('LAPUTA_UPTIME', '/proc/uptime')) as f:
            up = int(float(f.read().split()[0]))
    except (OSError, ValueError):
        up = 0
    return {'cpu': sampler.cpu, 'cores': os.cpu_count() or 1, 'load': load, 'mem': mem, 'swap': swap,
            'disks': read_disks(), 'uptime': up, 'temp': read_temp(), 'net': sampler.net,
            'os': os_name(), 'kernel': os.uname().release.split('-')[0], 'host': socket.gethostname(), 'init': INIT}


# ---------------------------------------------------------------- systemd

SHOW_PROPS = 'Id,LoadState,ActiveState,SubState,StateChangeTimestampMonotonic,MemoryCurrent,NRestarts'
NOISE = ('systemd-', 'dbus', 'getty@', 'serial-getty@', 'user@', 'user-runtime-dir@', 'modprobe@', 'initrd', 'plymouth',
         'apt-daily', 'e2scrub', 'emergency', 'rescue', 'kmod', 'console-', 'keyboard-', 'ifup@', 'polkit', 'udev',
         'rc-local', 'ldconfig', 'man-db', 'dpkg-db', 'fstrim', 'logrotate', 'laputa-', 'blk-availability', 'lvm2-',
         'dm-event', 'quota', 'auditd', 'apparmor', 'cloud-', 'networking', 'ifupdown', 'sysstat', 'grub', 'unattended-',
         'packagekit', 'rtkit', 'accounts-daemon', 'alsa-', 'anacron', 'binfmt', 'hwclock', 'mdmonitor', 'nftables-',
         'pam_', 'paxctld', 'selinux', 'sudo', 'x11-', 'wtmpdb', 'dracut', 'pcscd', 'tpm-', 'usb_', 'rpc-', 'nfs-')


def unit_states(units):
    """Service states in one systemctl call."""
    if not units or INIT != 'systemd':
        return {}
    rc, out = sh(['systemctl', 'show', '--no-pager', '--property=' + SHOW_PROPS, '--'] + list(units), timeout=15)
    res = {}
    mono = time.monotonic()
    wall = time.time()
    blocks = [b for b in out.split('\n\n') if 'ActiveState=' in b]
    by_order = len(blocks) == len(units)  # systemctl answers in request order; this survives unit aliases
    for i, block in enumerate(blocks):
        d = {}
        for line in block.splitlines():
            k, _, v = line.partition('=')
            d[k] = v
        uid = list(units)[i] if by_order else d.get('Id')
        if not uid:
            continue
        st = d.get('ActiveState', 'unknown')
        if st == 'reloading':
            st = 'active'
        if d.get('LoadState') == 'not-found':
            st = 'missing'
        since = None
        try:
            us = int(d.get('StateChangeTimestampMonotonic', '0'))
            if us > 0:
                since = int(wall - (mono - us / 1e6))
        except ValueError:
            pass
        mem = None
        try:
            mv = int(d.get('MemoryCurrent', ''))
            if 0 < mv < 1 << 62:
                mem = mv
        except ValueError:
            pass
        try:
            restarts = int(d.get('NRestarts', '0'))
        except ValueError:
            restarts = 0
        res[uid] = {'state': st, 'sub': d.get('SubState', ''), 'since': since, 'mem': mem if st == 'active' else None, 'restarts': restarts}
    return res


def discover_systemd():
    if INIT != 'systemd':
        return []
    found = {}
    rc, out = sh(['systemctl', 'list-units', '--type=service', '--all', '--no-legend', '--plain', '--no-pager'], timeout=15)
    for line in out.splitlines():
        p = line.split(None, 4)
        if len(p) >= 4 and p[0].endswith('.service') and p[1] != 'not-found':
            found[p[0]] = {'unit': p[0], 'state': p[2] if p[2] in ('active', 'failed') else 'inactive', 'desc': p[4].strip() if len(p) > 4 else ''}
    rc, out = sh(['systemctl', 'list-unit-files', '--type=service', '--no-legend', '--plain', '--no-pager'], timeout=15)
    for line in out.splitlines():
        p = line.split()
        if len(p) >= 2 and p[0].endswith('.service') and '@.' not in p[0] and p[1] in ('enabled', 'disabled') and p[0] not in found:
            found[p[0]] = {'unit': p[0], 'state': 'inactive', 'desc': ''}
    out = [u for u in found.values() if not u['unit'].startswith(NOISE) and UNIT_RE.match(u['unit'])]
    out.sort(key=lambda u: (u['state'] != 'failed', u['unit'].lower()))
    return out[:300]


# ---------------------------------------------------------------- Minecraft RCON

class RconError(Exception):
    pass


class Rcon:
    def __init__(self, host, port, password, timeout=5):
        self.s = socket.create_connection((host, int(port)), timeout=timeout)
        self.s.settimeout(timeout)
        self._send(1, 3, password)
        rid, _, _ = self._recv()
        if rid == -1:
            self.close()
            raise RconError(L('RCON не принял пароль', 'RCON rejected the password'))

    def _send(self, rid, typ, payload):
        data = struct.pack('<ii', rid, typ) + payload.encode('utf-8') + b'\x00\x00'
        self.s.sendall(struct.pack('<i', len(data)) + data)

    def _read(self, n):
        buf = b''
        while len(buf) < n:
            chunk = self.s.recv(n - len(buf))
            if not chunk:
                raise RconError(L('сервер закрыл соединение RCON', 'server closed the RCON connection'))
            buf += chunk
        return buf

    def _recv(self):
        (ln,) = struct.unpack('<i', self._read(4))
        if ln < 10 or ln > 1 << 20:
            raise RconError(L('непонятный ответ RCON', 'unexpected RCON reply'))
        data = self._read(ln)
        rid, typ = struct.unpack('<ii', data[:8])
        return rid, typ, data[8:-2].decode('utf-8', 'replace')

    def command(self, cmd):
        self._send(2, 2, cmd)
        _, _, body = self._recv()
        self.s.settimeout(0.25)  # long replies arrive in several packets
        try:
            while len(body) < 60000:
                _, _, more = self._recv()
                body += more
        except (socket.timeout, OSError, RconError):
            pass
        return re.sub('§.', '', body).strip()

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


