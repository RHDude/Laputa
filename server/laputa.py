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



# ---------------------------------------------------------------- Docker

def parse_iso(s):
    """'2026-10-08T12:00:00.123456789Z' -> unix time; Docker's zero date -> None."""
    if not s or s.startswith('0001-'):
        return None
    m = re.match(r'(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.\d+)?(Z|[+-]\d\d:?\d\d)?$', s.strip())
    if not m:
        return None
    import calendar
    t = calendar.timegm(time.strptime(m.group(1), '%Y-%m-%dT%H:%M:%S'))
    tz = m.group(2) or 'Z'
    if tz != 'Z':
        sign = 1 if tz[0] == '+' else -1
        hh, mm = int(tz[1:3]), int(tz[-2:])
        t -= sign * (hh * 3600 + mm * 60)
    return int(t)


def docker_ports(text):
    """'0.0.0.0:8096->8096/tcp, :::8096->8096/tcp' -> [8096]"""
    out = []
    for m in re.finditer(r':(\d+)->\d+/(tcp|udp)', text or ''):
        p = int(m.group(1))
        if p not in out:
            out.append(p)
    return out


def docker_list():
    """All containers: {name: {state, sub, since, restarts, ports, image}}. Empty without Docker."""
    if not shutil.which('docker'):
        return {}
    rc, out = sh(['docker', 'ps', '-a', '--no-trunc', '--format', '{{json .}}'], timeout=15)
    if rc != 0:
        return {}
    res = {}
    for line in out.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        name = (d.get('Names') or '').split(',')[0]
        if name:
            res[name] = {'image': d.get('Image', ''), 'ports': docker_ports(d.get('Ports', '')), 'raw': d.get('State', '')}
    if res:
        fmt = '{{.Name}}|{{.State.Status}}|{{.State.StartedAt}}|{{.State.FinishedAt}}|{{.State.ExitCode}}|{{.RestartCount}}'
        rc, out = sh(['docker', 'inspect', '--format', fmt] + list(res), timeout=15)
        for line in out.splitlines():
            p = line.strip().lstrip('/').split('|')
            if len(p) < 6 or p[0] not in res:
                continue
            st, started, finished = p[1], parse_iso(p[2]), parse_iso(p[3])
            code = int(p[4]) if p[4].lstrip('-').isdigit() else 0
            if st == 'running':
                state, since = 'active', started
            elif st == 'restarting':
                state, since = 'activating', finished
            elif st in ('exited', 'dead'):
                state, since = ('failed' if code else 'inactive'), finished
            else:
                state, since = 'inactive', finished or started
            res[p[0]].update({'state': state, 'sub': st, 'since': since, 'restarts': int(p[5]) if p[5].isdigit() else 0})
    for v in res.values():
        v.setdefault('state', 'unknown')
        v.setdefault('sub', v.pop('raw', ''))
        v.pop('raw', None)
        v.setdefault('since', None)
        v.setdefault('restarts', 0)
    return res


def service_states(units):
    """systemd units and Docker containers (named docker:NAME) in one dict."""
    sysd = [u for u in units if not u.startswith('docker:')]
    res = unit_states(sysd) if sysd else {}
    if any(u.startswith('docker:') for u in units):
        dl = docker_list()
        for u in units:
            if u.startswith('docker:'):
                d = dl.get(u[7:])
                res[u] = {'state': d['state'], 'sub': d['sub'], 'since': d['since'], 'mem': None, 'restarts': d['restarts']} if d else \
                    {'state': 'missing', 'sub': '', 'since': None, 'mem': None, 'restarts': 0}
    return res


def discover_units():
    out = discover_systemd()
    for name, d in sorted(docker_list().items()):
        out.append({'unit': 'docker:' + name, 'state': d['state'] if d['state'] in ('active', 'failed') else 'inactive',
                    'desc': d.get('image', ''), 'ports': d.get('ports', [])})
    return out


# ---------------------------------------------------------------- network: who listens, who is connected

def unit_of_pid(pid):
    """The systemd unit a process belongs to, from its cgroup."""
    try:
        with open('%s/%d/cgroup' % (PROC, pid)) as f:
            text = f.read()
    except OSError:
        return None
    m = re.findall(r'/([^/\n]+\.service)', text)
    return m[-1] if m else None


def listen_map():
    """{unit: [ports]} for sockets that service processes listen on. Needs ss (iproute2)."""
    if not shutil.which('ss'):
        return {}
    rc, out = sh(['ss', '-Hltnup'], timeout=10)
    res = {}
    for line in out.splitlines():
        p = line.split()
        if len(p) < 6:
            continue
        local = p[4]
        port = local.rsplit(':', 1)[-1]
        if not port.isdigit():
            continue
        for pid in re.findall(r'pid=(\d+)', line):
            u = unit_of_pid(int(pid))
            if u:
                lst = res.setdefault(u, [])
                if int(port) not in lst:
                    lst.append(int(port))
    return res


def established_counts():
    """{local port: number of established TCP connections from outside}."""
    if not shutil.which('ss'):
        return {}
    rc, out = sh(['ss', '-Htn', 'state', 'established'], timeout=10)
    res = {}
    for line in out.splitlines():
        p = line.split()
        if len(p) < 4:
            continue
        local, peer = p[-2], p[-1]
        lport = local.rsplit(':', 1)[-1]
        phost = peer.rsplit(':', 1)[0].strip('[]')
        if not lport.isdigit():
            continue
        try:
            if ipaddress.ip_address(phost.split('%')[0]).is_loopback:
                continue
        except ValueError:
            pass
        res[int(lport)] = res.get(int(lport), 0) + 1
    return res


def tunnel_peers(window=180):
    """Active AmneziaWG and WireGuard peers: a handshake within the last 3 minutes."""
    res = {}
    now = time.time()
    for tool in ('awg', 'wg'):
        if not shutil.which(tool):
            continue
        rc, out = sh([tool, 'show', 'all', 'latest-handshakes'], timeout=10)
        if rc != 0:
            continue
        for line in out.splitlines():
            p = line.split()
            if len(p) >= 3 and p[2].isdigit() and int(p[2]) > 0 and now - int(p[2]) < window:
                res[p[0]] = res.get(p[0], 0) + 1
    return [{'iface': k, 'n': v} for k, v in sorted(res.items())]


# ---------------------------------------------------------------- game console (RCON)

GAME_TYPES = ('minecraft', 'rcon')
LIST_RE = re.compile(r'There are (\d+) of a max(?:imum)? of (\d+) players online:?\s*(.*)', re.S)


def rcon_run(cfg, commands):
    """Run several commands over one connection. Returns the replies in order."""
    try:
        r = Rcon(cfg['host'], cfg['port'], cfg.get('password', ''))
    except RconError as e:
        raise Err('rcon_auth', str(e))
    except OSError:
        raise Err('rcon_down', 'Сервер не отвечает по RCON на %s:%s' % (cfg.get('host'), cfg.get('port')), host=cfg.get('host'), port=cfg.get('port'))
    try:
        return [r.command(c) for c in commands]
    finally:
        r.close()


def game_probe(cfg):
    info = {'type': cfg.get('type', 'minecraft'), 'online': False, 'players': None, 'max': 0,
            'cfg': {'host': cfg.get('host'), 'port': cfg.get('port'), 'type': cfg.get('type', 'minecraft')}, 'quick': cfg.get('quick', [])}
    try:
        if info['type'] == 'minecraft':
            out = rcon_run(cfg, ['minecraft:list'])[0]
            m = LIST_RE.search(out)
            if not m:
                out = rcon_run(cfg, ['list'])[0]
                m = LIST_RE.search(out)
            info['players'] = []
            if m:
                info['max'] = int(m.group(2))
                info['players'] = [p.strip() for p in m.group(3).split(',') if p.strip()][:100]
        else:
            Rcon(cfg['host'], cfg['port'], cfg.get('password', '')).close()
        info['online'] = True
    except Err as e:
        if e.code == 'rcon_auth':
            info['error'] = str(e)
    except (RconError, OSError, ValueError):
        pass
    return info


# ---------------------------------------------------------------- backups

ARCHIVE_RE = re.compile(r'\.(tar|tgz|gz|zst|xz|bz2|zip|7z|rar|sql|dump|bak|img|qcow2|borg)$', re.I)


def backup_info(path, label=''):
    info = {'path': path, 'label': label or os.path.basename(path.rstrip('/')) or path, 'ts': None, 'size': None, 'count': 0, 'every': None}
    try:
        items = []
        with os.scandir(path) as it:
            for e in it:
                if e.name.startswith('.'):
                    continue
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                items.append((st.st_mtime, st.st_size if e.is_file(follow_symlinks=False) else 0))
    except OSError:
        info['error'] = 'missing'
        return info
    if not items:
        return info
    items.sort(reverse=True)
    info.update({'ts': int(items[0][0]), 'size': items[0][1], 'count': len(items)})
    gaps = [items[i][0] - items[i + 1][0] for i in range(min(len(items), 30) - 1)]
    gaps = [g for g in gaps if g >= 30]
    if len(gaps) >= 2:
        info['every'] = int(statistics.median(gaps))
    return info


def backup_suggest(known, budget=2.5):
    """Backup-like folders, plus hints from timers and cron."""
    t0 = time.time()
    roots = BACKUP_ROOTS
    seen, found = 0, []
    stack = [(r, 0) for r in roots if os.path.isdir(r)]
    while stack and time.time() - t0 < budget and seen < 20000:
        d, depth = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        seen += len(entries)
        archives = sum(1 for e in entries if ARCHIVE_RE.search(e.name))
        named = re.search(r'back|bak|snapshot|dump', os.path.basename(d), re.I)
        if d not in known and (named or archives >= 3) and len(entries) >= 2:
            inf = backup_info(d)
            if inf['count'] >= 2:
                inf['reason'] = 'name' if named else 'archives'
                found.append(inf)
        if depth < 3 and len(entries) < 3000:
            for e in entries:
                if e.is_dir(follow_symlinks=False) and not e.name.startswith('.') and e.name not in ('proc', 'sys', 'dev', 'node_modules', '.git', 'docker', 'containers', 'overlay2'):
                    stack.append((e.path, depth + 1))
    found.sort(key=lambda x: -(x['ts'] or 0))
    hints = []
    if INIT == 'systemd':
        rc, out = sh(['systemctl', 'list-timers', '--all', '--no-legend', '--no-pager'], timeout=10)
        for line in out.splitlines():
            if re.search(r'back|bak|dump|snapshot', line, re.I):
                m = re.search(r'(\S+\.timer)', line)
                if m:
                    hints.append('timer: ' + m.group(1))
    cron = ['/etc/crontab'] + [os.path.join(d, f) for d in ('/etc/cron.d', '/var/spool/cron/crontabs', '/var/spool/cron')
                               if os.path.isdir(d) for f in sorted(os.listdir(d))]
    for path in cron:
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                for line in f:
                    if not line.lstrip().startswith('#') and re.search(r'back|bak|dump|snapshot', line, re.I):
                        hints.append('cron %s: %s' % (os.path.basename(path), line.strip()[:160]))
        except OSError:
            pass
    return {'dirs': found[:12], 'hints': hints[:12]}


# ---------------------------------------------------------------- node actions

ACTIONS_EXAMPLE_RU = """# Действия узла: кнопки в приложении Laputa. Каждый раздел в квадратных скобках — одна кнопка.
# Добавлять действия можно только здесь, на самом сервере: приложение их показывает и запускает, но не создаёт.
#
# [Обновить бота]
# command = cd /opt/mybot && git pull && systemctl restart mybot
# service = mybot.service      ; необязательно: кнопка появится в карточке этой службы
# confirm = yes                ; спросить подтверждение перед запуском (по умолчанию yes)
# timeout = 120                ; сколько секунд ждать (по умолчанию 60, не больше 600)
# user = mybot                 ; необязательно: от какого пользователя запускать
"""

ACTIONS_EXAMPLE_EN = """# Node actions: buttons in the Laputa app. Each section in square brackets is one button.
# Actions can only be added here, on the server itself: the app shows and runs them but never creates them.
#
# [Update bot]
# command = cd /opt/mybot && git pull && systemctl restart mybot
# service = mybot.service      ; optional: the button appears on this service's card
# confirm = yes                ; ask before running (default yes)
# timeout = 120                ; seconds to wait (default 60, at most 600)
# user = mybot                 ; optional: which user to run as
"""


def load_actions(path=None, _cache={}):
    import configparser
    path = path or ACTIONS_FILE
    try:
        mt = os.stat(path).st_mtime
    except OSError:
        return []
    if _cache.get('key') == (path, mt):
        return _cache['val']
    cp = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(';',))
    try:
        cp.read(path, encoding='utf-8')
    except configparser.Error as e:
        log(L('actions.ini не читается:', 'cannot read actions.ini:'), e)
        return []
    out = []
    for name in cp.sections()[:40]:
        sec = cp[name]
        cmd = sec.get('command', '').strip()
        if not cmd:
            continue
        try:
            timeout = max(5, min(600, int(sec.get('timeout', '60'))))
        except ValueError:
            timeout = 60
        out.append({'id': hashlib.sha1(name.encode('utf-8')).hexdigest()[:10], 'name': name[:60], 'command': cmd[:500],
                    'service': sec.get('service', '').strip(), 'confirm': sec.get('confirm', 'yes').strip().lower() not in ('no', 'false', '0'),
                    'timeout': timeout, 'user': sec.get('user', '').strip()})
    _cache.update(key=(path, mt), val=out)
    return out


def run_action(a):
    if a['user']:
        if shutil.which('runuser'):
            args = ['runuser', '-u', a['user'], '--', '/bin/sh', '-c', a['command']]
        else:
            args = ['su', '-s', '/bin/sh', a['user'], '-c', a['command']]
    else:
        args = ['/bin/sh', '-c', a['command']]
    rc, out = sh(args, timeout=a['timeout'])
    return rc, out[-16000:]


# ---------------------------------------------------------------- maintenance: updates and reboot

def _ver_key(v):
    # 6.1.0-25-amd64 -> [6, 1, 0, 25, 64]: the Debian/Ubuntu build number counts too
    return [int(x) for x in re.findall(r'\d+', v)]


def kernel_reboot_needed():
    """A newer kernel than the running one is installed: reboot needed. Works on any distro."""
    running = _ver_key(os.uname().release)
    try:
        mods = [m for m in os.listdir('/lib/modules') if re.match(r'\d', m)]
    except OSError:
        return False
    return any(_ver_key(m) > running for m in mods)


def maint_check():
    res = {'pm': None, 'updates': None, 'security': None, 'reboot': False, 'why': '', 'pkgs': [], 'ts': int(time.time())}
    if os.path.exists(REBOOT_FILE):
        res.update(reboot=True, why='file')
        try:
            with open(REBOOT_FILE + '.pkgs') as f:
                res['pkgs'] = sorted({l.strip() for l in f if l.strip()})[:8]
        except OSError:
            pass
    try:
        if shutil.which('apt-get'):
            res['pm'] = 'apt'
            rc, out = sh(['apt-get', '-s', '-o', 'Debug::NoLocking=1', 'dist-upgrade'], timeout=180)
            if rc == 0:
                inst = [l for l in out.splitlines() if l.startswith('Inst ')]
                res['updates'] = len(inst)
                res['security'] = sum(1 for l in inst if re.search(r'securi', l, re.I))
        elif shutil.which('dnf') or shutil.which('yum'):
            pm = 'dnf' if shutil.which('dnf') else 'yum'
            res['pm'] = pm
            rc, out = sh([pm, '-q', '--cacheonly', 'check-update'], timeout=180)
            if rc in (0, 100):
                body = out.split('Obsoleting')[0]
                res['updates'] = sum(1 for l in body.splitlines() if len(l.split()) == 3 and '.' in l.split()[0])
                rc2, out2 = sh([pm, '-q', '--cacheonly', 'updateinfo', 'list', '--security'], timeout=120)
                if rc2 == 0:
                    res['security'] = sum(1 for l in out2.splitlines() if len(l.split()) >= 3)
            if not res['reboot']:
                rc3, _ = sh([pm, 'needs-restarting', '-r'], timeout=60)
                if rc3 == 1:
                    res.update(reboot=True, why='needs-restarting')
        elif shutil.which('pacman'):
            res['pm'] = 'pacman'
            rc, out = sh(['pacman', '-Qu'], timeout=60)
            res['updates'] = len([l for l in out.splitlines() if l.strip()]) if rc in (0, 1) else None
        elif shutil.which('zypper'):
            res['pm'] = 'zypper'
            rc, out = sh(['zypper', '-q', '--non-interactive', '--no-refresh', 'list-updates'], timeout=180)
            if rc == 0:
                res['updates'] = sum(1 for l in out.splitlines() if l.startswith('v '))
            rc2, out2 = sh(['zypper', '-q', '--non-interactive', '--no-refresh', 'list-patches', '--category', 'security'], timeout=180)
            if rc2 == 0:
                res['security'] = sum(1 for l in out2.splitlines() if '| security' in l)
            if not res['reboot'] and (sh(['zypper', 'needs-rebooting'], timeout=60)[0] == 102 or os.path.exists('/run/reboot-needed')):
                res.update(reboot=True, why='needs-restarting')
        elif shutil.which('apk'):
            res['pm'] = 'apk'
            rc, out = sh(['apk', 'version', '-l', '<'], timeout=60)
            if rc == 0:
                res['updates'] = sum(1 for l in out.splitlines() if '<' in l)
    except Exception as e:
        log(L('проверка обновлений:', 'update check:'), e)
    if not res['reboot'] and kernel_reboot_needed():
        res.update(reboot=True, why='kernel')
    return res


# ---------------------------------------------------------------- SSH logins

ACCEPT_RE = re.compile(r'Accepted (\S+) for (\S+) from (\S+) port (\d+)')


class LoginWatch:
    """Watches successful SSH logins: the systemd journal, or log files where there is none."""

    def __init__(self):
        self.since = time.time()
        self.files = {}
        self.items = collections.deque(maxlen=20)

    def poll(self):
        if INIT == 'systemd' and shutil.which('journalctl'):
            rc, out = sh(['journalctl', '-t', 'sshd', '-t', 'sshd-session', '--since', '@%d' % int(self.since),
                          '-o', 'short-unix', '--no-pager', '-q'], timeout=15)
            last = self.since
            for line in out.splitlines():
                m = ACCEPT_RE.search(line)
                try:
                    ts = float(line.split()[0])
                except (ValueError, IndexError):
                    continue
                if ts <= self.since:
                    continue
                last = max(last, ts)
                if m:
                    self.add(ts, m)
            self.since = last + 0.001
            return
        for path in ('/var/log/auth.log', '/var/log/secure', '/var/log/messages'):
            try:
                st = os.stat(path)
            except OSError:
                continue
            ino, off = self.files.get(path, (st.st_ino, st.st_size))  # the first look skips history
            if ino != st.st_ino or off > st.st_size:
                off = 0
            try:
                with open(path, 'rb') as f:
                    f.seek(off)
                    data = f.read(1 << 20)
                    off = f.tell()
            except OSError:
                continue
            self.files[path] = (st.st_ino, off)
            for line in data.decode('utf-8', 'replace').splitlines():
                m = ACCEPT_RE.search(line)
                if m:
                    self.add(time.time(), m)

    def add(self, ts, m):
        self.items.append({'ts': round(ts, 3), 'method': m.group(1), 'user': m.group(2)[:32], 'ip': m.group(3)[:64]})


# ---------------------------------------------------------------- checking a neighbour from outside

def probe(ip, port):
    try:
        addr = ipaddress.ip_address(str(ip))
    except ValueError:
        raise Err('bad_ip', 'Это не IP-адрес: %s' % ip, ip=str(ip))
    if addr.is_loopback or addr.is_unspecified or addr.is_multicast:
        raise Err('bad_ip', 'Такой адрес проверять нельзя: %s' % ip, ip=str(ip))
    ip = str(addr)
    res = {'ip': ip, 'ping': None, 'tcp': None, 'trace': None, 'raw': ''}
    if shutil.which('ping'):
        rc, out = sh(['ping', '-c', '3', '-W', '2', ip], timeout=15)
        m = re.search(r'(\d+) packets transmitted, (\d+) (?:packets )?received', out)
        avg = re.search(r'= [\d.]+/([\d.]+)/', out)
        if m:
            res['ping'] = {'sent': int(m.group(1)), 'recv': int(m.group(2)), 'avg': float(avg.group(1)) if avg else None}
        res['raw'] += out.strip()[-800:] + '\n'
    try:
        port = int(port or 22)
    except (TypeError, ValueError):
        port = 22
    if 0 < port < 65536:
        t0 = time.time()
        try:
            socket.create_connection((ip, port), timeout=4).close()
            res['tcp'] = {'port': port, 'ok': True, 'ms': int((time.time() - t0) * 1000)}
        except OSError as e:
            res['tcp'] = {'port': port, 'ok': False, 'err': type(e).__name__}
    for args in (['tracepath', '-n', '-m', '20', ip], ['traceroute', '-n', '-w', '1', '-q', '1', '-m', '20', ip],
                 ['mtr', '-r', '-n', '-c', '2', '-w', ip]):
        if shutil.which(args[0]):
            rc, out = sh(args, timeout=40)
            hops = []
            for line in out.splitlines():
                m = re.match(r'\s*(\d+)[:.|\-\s]+(?:\|--\s+)?(\S+)', line)
                if m and m.group(2) not in ('no', 'traceroute'):
                    ms = re.search(r'([\d.]+)\s*ms', line)
                    hop = {'n': int(m.group(1)), 'ip': m.group(2) if m.group(2) not in ('???', '*') else '*', 'ms': float(ms.group(1)) if ms else None}
                    if not hops or hops[-1]['n'] != hop['n']:
                        hops.append(hop)
            res['trace'] = {'tool': args[0], 'hops': hops[:30]}
            res['raw'] += out.strip()[-2500:]
            break
    return res


# ---------------------------------------------------------------- previous boot analysis

def read_boot_id():
    try:
        with open(BOOT_ID_FILE) as f:
            return f.read().strip()
    except OSError:
        return ''


def postmortem():
    """How the previous boot ended, from its journal if it is kept across boots."""
    if not shutil.which('journalctl'):
        return {'cause': 'unknown', 'why': 'nojournal', 'lines': []}
    rc, out = sh(['journalctl', '-b', '-1', '-n', '400', '-o', 'short-unix', '--no-pager', '-q'], timeout=30)
    lines = [l for l in out.splitlines() if re.match(r'\d+\.\d+ ', l)]
    if rc != 0 or not lines:
        return {'cause': 'unknown', 'why': 'nojournal', 'lines': []}
    text = '\n'.join(lines)
    clean = re.search(r'systemd-shutdown|Journal stopped|Reached target .*(Reboot|Power.?Off|Halt|Shutdown)|reboot: (Restarting|Power down)', text)
    oom = re.search(r'Out of memory: Kill|invoked oom-killer|oom-kill:', text)
    panic = re.search(r'Kernel panic', text)
    lockup = re.search(r'soft lockup|hard LOCKUP|hung_task|blocked for more than', text)
    cause = 'clean' if clean else 'panic' if panic else 'oom' if oom else 'lockup' if lockup else 'abrupt'
    last_ts = float(lines[-1].split()[0])
    tail = []
    for l in lines[-14:]:
        ts, _, rest = l.partition(' ')
        rest = re.sub(r'^\S+ ', '', rest, count=1)
        tail.append([int(float(ts)), rest[:240]])
    return {'cause': cause, 'oom': bool(oom), 'last_ts': int(last_ts), 'lines': tail}

# ---------------------------------------------------------------- agent

KNOWN_RE = re.compile(r'^(nginx|apache2|httpd|caddy|traefik|haproxy|docker|containerd|postgres|postgresql|mariadb|mysql|redis|mongod|'
                      r'jellyfin|plex|emby|navidrome|x-ui|3x-ui|xray|sing-box|hysteria|tuic|awg-quick@|wg-quick@|openvpn|'
                      r'minecraft|paper|beszel|netdata|grafana|prometheus|uptime-kuma|gitea|forgejo|nextcloud|vaultwarden)', re.I)


def unit_props(units, props):
    """Arbitrary unit properties, one systemctl call per batch."""
    res = {}
    units = list(units)
    for i in range(0, len(units), 80):
        chunk = units[i:i + 80]
        rc, out = sh(['systemctl', 'show', '--no-pager', '--property=' + props, '--'] + chunk, timeout=20)
        blocks = [b for b in out.split('\n\n') if '=' in b]
        if len(blocks) != len(chunk):
            continue
        for u, block in zip(chunk, blocks):
            d = {}
            for line in block.splitlines():
                k, _, v = line.partition('=')
                d[k] = v
            res[u] = d
    return res


class Agent:
    def __init__(self, path=AGENT_CFG):
        self.path = path
        self.cfg = jload(path, {}) or {}
        if not self.cfg.get('hubs'):
            raise SystemExit(L('В %s нет ни одного сторожа. Запустите install-agent.', 'No hubs in %s. Run install-agent.') % path)
        self.lock = threading.RLock()
        self.sampler = Sampler()
        self.game = None
        self.svc_cache = (0, {})
        self.lm_cache = (0, {})
        self.bk_cache = (0, [])
        self.kick = threading.Event()
        self.logins = LoginWatch()
        self.maint = None
        self.candidates = []
        self.pending = None
        self.cancel = threading.Event()
        self.flags = {}
        self.going_down = 0
        self.boot_id = read_boot_id()
        self.pm = None
        self.boot_check = None
        self.state_ready = True
        self.started = time.time()
        self.migrate()

    def save(self):
        with self.lock:
            jsave(self.path, self.cfg)

    def migrate(self):
        """1.0 settings: the mc block becomes the game console, its backup folder joins the common list."""
        mc = self.cfg.pop('mc', None)
        if mc:
            self.cfg['game'] = {'type': 'minecraft', 'host': mc.get('host', '127.0.0.1'), 'port': mc.get('port', 25575),
                                'password': mc.get('password', ''), 'quick': []}
            if mc.get('backups'):
                bl = self.cfg.setdefault('backups', [])
                if not any(b['path'] == mc['backups'] for b in bl):
                    bl.append({'path': mc['backups'], 'label': 'Minecraft'})
            self.save()

    # --- data

    def services(self, fresh=False):
        with self.lock:
            conf = list(self.cfg.get('services', []))
            if not fresh and time.time() - self.svc_cache[0] < 4:
                states = self.svc_cache[1]
            else:
                states = service_states([s['unit'] for s in conf])
                self.svc_cache = (time.time(), states)
        out = []
        for s in conf:
            st = states.get(s['unit'], {'state': 'unknown', 'sub': '', 'since': None, 'mem': None, 'restarts': 0})
            out.append({'unit': s['unit'], 'label': s.get('label') or s['unit'], 'kind': s.get('kind', 'other'), 'link': s.get('link', ''), **st})
        return out

    def listen(self):
        if time.time() - self.lm_cache[0] > 60 / _SPEED:
            self.lm_cache = (time.time(), listen_map())
        return self.lm_cache[1]

    def ssh_port(self):
        lm = self.listen()
        ports = sorted(p for u in ('ssh.service', 'sshd.service') for p in lm.get(u, []))
        if ports:
            return ports[0]
        try:
            with open('/etc/ssh/sshd_config') as f:
                for line in f:
                    m = re.match(r'\s*Port\s+(\d+)', line)
                    if m:
                        return int(m.group(1))
        except OSError:
            pass
        return 22

    def backups(self):
        if time.time() - self.bk_cache[0] > 60 / _SPEED:
            with self.lock:
                conf = list(self.cfg.get('backups', []))
            self.bk_cache = (time.time(), [backup_info(b['path'], b.get('label', '')) for b in conf])
        return self.bk_cache[1]

    def game_public(self):
        g = self.game
        if not self.cfg.get('game'):
            return None
        if g is None:
            c = self.cfg['game']
            g = {'type': c.get('type', 'minecraft'), 'online': False, 'players': None, 'max': 0, 'cfg': {'host': c.get('host'), 'port': c.get('port'), 'type': c.get('type', 'minecraft')}}
        g = dict(g)
        g['quick'] = self.cfg['game'].get('quick', [])
        return g

    def beat_body(self, hub):
        m = metrics(self.sampler)
        m['ssh_port'] = self.ssh_port()
        game = self.game_public()
        now = time.time()
        body = {'node': self.cfg['id'], 'name': self.cfg.get('name') or self.cfg['id'], 'place': self.cfg.get('place', ''),
                'hub': bool(self.cfg.get('hub')), 'agent': VERSION, 'ts': int(now), 'm': m, 'services': self.services(),
                'game': game, 'mc': game if game and game['type'] == 'minecraft' else None,
                'actions': [{k: a[k] for k in ('id', 'name', 'service', 'confirm', 'command')} for a in load_actions()],
                'backups': self.backups(), 'maint': self.maint, 'candidates': self.candidates, 'pending': self.pending,
                'logins': list(self.logins.items), 'flags': dict(self.flags), 'going_down': self.going_down, 'boot_id': self.boot_id}
        if self.pm and now - self.started < 3600:
            body['pm'] = self.pm
        if self.boot_check and now - self.boot_check['ts'] < 3600:
            body['boot_check'] = self.boot_check
        local = next((h for h in self.cfg['hubs'] if h.get('local')), None)
        if local and not hub.get('local'):
            try:  # the watchman tells its neighbour whom it sees
                body['view'] = hub_call(local, 'GET', '/a/view', timeout=5).get('ages', {})
                body['hub_ok'] = True
            except Exception:
                body['hub_ok'] = False  # node alive but its watchman is not answering: the neighbour reports it
        return body

    # --- after boot

    def boot_start(self):
        st = jload(AGENT_STATE, {}) or {}
        if st.get('boot_id') and self.boot_id and st['boot_id'] != self.boot_id:
            log(L('новая загрузка: разбираю прошлую', 'new boot: looking at the previous one'))
            self.pm = postmortem()
            self.pm.update(boot_id=self.boot_id, prev_ts=st.get('ts'))
            self.state_ready = False  # keep the previous list of running services for the post-boot check
            t = threading.Timer(BOOT_CHECK, self.do_boot_check, args=(st.get('active', []),))
            t.daemon = True
            t.start()

    def do_boot_check(self, prev_active):
        try:
            cur = self.services(fresh=True)
            missing = [{'unit': s['unit'], 'label': s['label']} for s in cur if s['unit'] in prev_active and s['state'] != 'active']
            self.boot_check = {'boot_id': self.boot_id, 'missing': missing, 'ts': int(time.time())}
            log(L('сверка после загрузки: не поднялись %d', 'after boot: %d services did not come up') % len(missing))
        finally:
            self.state_ready = True
            self.kick.set()

    def state_loop(self):
        last = None
        while True:
            try:
                if self.state_ready:
                    active = sorted(s['unit'] for s in self.services() if s['state'] == 'active')
                    key = (self.boot_id, tuple(active))
                    if key != last:
                        jsave(AGENT_STATE, {'boot_id': self.boot_id, 'active': active, 'ts': int(time.time())})
                        last = key
            except Exception as e:
                log(L('состояние агента:', 'agent state:'), e)
            time.sleep(30 / _SPEED)

    # --- threads

    def beat_once(self, hub, timeout=12):
        hub_call(hub, 'POST', '/a/beat', self.beat_body(hub), timeout=timeout)

    def beat_loop(self, hub):
        fails = 0
        while True:
            try:
                self.beat_once(hub)
                if fails:
                    log(L('сторож %s снова отвечает', 'hub %s is answering again') % hub['url'])
                fails = 0
            except Exception as e:
                fails += 1
                if fails in (1, 6) or fails % 60 == 0:
                    log(L('сторож %s недоступен: %s', 'hub %s unreachable: %s') % (hub['url'], e))
            self.kick.wait(BEAT_EVERY)
            self.kick.clear()

    def poll_loop(self, hub):
        while True:
            try:
                r = hub_call(hub, 'GET', '/a/poll?node=%s&wait=25' % self.cfg['id'], timeout=40)
                for c in r.get('cmds', []):
                    res = self.run_cmd(c)
                    after = res.pop('_after', None)
                    res['id'] = c.get('id')
                    try:
                        hub_call(hub, 'POST', '/a/result', res, timeout=12)
                    except Exception as e:
                        log(L('не удалось отдать результат команды:', 'could not deliver command result:'), e)
                    self.kick.set()
                    if after:
                        threading.Thread(target=after, daemon=True).start()
            except Exception:
                time.sleep(5)

    def every(self, fn, period, first=0):
        def loop():
            time.sleep(first)
            while True:
                try:
                    fn()
                except Exception as e:
                    log('%s: %r' % (fn.__name__, e))
                time.sleep(period)
        threading.Thread(target=loop, daemon=True).start()

    def game_tick(self):
        cfg = self.cfg.get('game')
        self.game = game_probe(cfg) if cfg else None

    def maint_tick(self):
        self.maint = maint_check()

    def suggest_tick(self):
        dismissed = set(self.cfg.get('dismissed', []))
        out = []
        now = time.time()
        for u in self.suggest():
            if u['monitored'] or not u['recommended'] or u['unit'] in dismissed or u['state'] != 'active':
                continue
            if u.get('since') and now - u['since'] < CAND_AGE:
                continue
            out.append({'unit': u['unit'], 'reasons': u['reasons'], 'ports': u['ports'], 'desc': u.get('desc', '')})
        with self.lock:  # the service may have been added or dismissed meanwhile
            skip = {s['unit'] for s in self.cfg.get('services', [])} | set(self.cfg.get('dismissed', []))
            self.candidates = [c for c in out if c['unit'] not in skip][:5]

    def run(self):
        log(L('агент %s, узел %s, сторожей: %d, systemd: %s, docker: %s', 'agent %s, node %s, hubs: %d, systemd: %s, docker: %s') % (VERSION, self.cfg['id'], len(self.cfg['hubs']), INIT == 'systemd', bool(shutil.which('docker'))))
        self.boot_start()
        self.sampler.start()
        self.every(self.game_tick, 30 / _SPEED)
        self.every(self.logins.poll, LOGIN_EVERY)
        self.every(self.maint_tick, MAINT_EVERY, MAINT_FIRST)
        self.every(self.suggest_tick, SUGGEST_EVERY, 60 / _SPEED)
        threading.Thread(target=self.state_loop, daemon=True).start()
        time.sleep(1)
        for hub in self.cfg['hubs']:
            threading.Thread(target=self.beat_loop, args=(hub,), daemon=True).start()
            threading.Thread(target=self.poll_loop, args=(hub,), daemon=True).start()
        signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
        while True:
            time.sleep(3600)

    # --- service discovery

    def suggest(self):
        units = discover_units()
        sysd = [u['unit'] for u in units if not u['unit'].startswith('docker:')]
        props = unit_props(sysd, 'FragmentPath,UnitFileState,ActiveState,NRestarts,StateChangeTimestampMonotonic') if sysd else {}
        lm = self.listen()
        with self.lock:
            monitored = {s['unit'] for s in self.cfg.get('services', [])}
        mono, wall = time.monotonic(), time.time()
        out = []
        for u in units:
            name = u['unit']
            reasons, score, since = [], 0, None
            if name.startswith('docker:'):
                ports = u.get('ports', [])
                score += 3
                reasons.append('container')
                state = u['state']
            else:
                p = props.get(name, {})
                ports = sorted(lm.get(name, []))
                state = p.get('ActiveState', u['state'])
                if p.get('FragmentPath', '').startswith('/etc/systemd/system/'):
                    score += 3
                    reasons.append('manual')
                if KNOWN_RE.match(name):
                    score += 2
                    reasons.append('known')
                if p.get('UnitFileState') in ('enabled', 'enabled-runtime') and state == 'active':
                    score += 1
                    reasons.append('autostart')
                try:
                    if int(p.get('NRestarts', '0')) > 0 or state == 'failed':
                        score += 1
                        reasons.append('crashed')
                except ValueError:
                    pass
                try:
                    us = int(p.get('StateChangeTimestampMonotonic', '0'))
                    since = int(wall - (mono - us / 1e6)) if us else None
                except ValueError:
                    pass
            if ports:
                score += 3
                reasons.insert(0, 'port')
            out.append({'unit': name, 'desc': u.get('desc', ''), 'state': 'active' if state == 'active' else u['state'], 'score': score,
                        'reasons': reasons, 'ports': ports[:6], 'since': since, 'monitored': name in monitored, 'recommended': score >= 3})
        out.sort(key=lambda x: (-x['score'], x['unit']))
        return out[:150]

    # --- who is affected

    def impact(self, unit=None):
        svcs = [s for s in self.services(fresh=True) if unit is None or s['unit'] == unit]
        lm = listen_map()
        est = established_counts()
        dl = docker_list() if any(s['unit'].startswith('docker:') for s in svcs) else {}
        conns = []
        for s in svcs:
            if s['unit'].startswith('docker:'):
                ports = (dl.get(s['unit'][7:]) or {}).get('ports', [])
            else:
                ports = lm.get(s['unit'], [])
            if ports:
                conns.append({'unit': s['unit'], 'label': s['label'], 'ports': sorted(ports)[:6], 'n': sum(est.get(p, 0) for p in ports)})
        players = None
        gcfg = self.cfg.get('game')
        kind = next((s['kind'] for s in svcs if unit and s['unit'] == unit), None)
        if gcfg and (unit is None or kind == 'game'):
            players = game_probe(gcfg).get('players')
        tunnels = tunnel_peers() if unit is None or re.match(r'(awg|wg)-quick@', unit or '') else []
        total = len(players or []) + sum(c['n'] for c in conns) + sum(t['n'] for t in tunnels)
        return {'players': players, 'conns': conns, 'tunnels': tunnels, 'total': total}

    # --- reboot

    def say(self, text):
        g = self.cfg.get('game')
        if g and g.get('type') == 'minecraft' and text:
            try:
                rcon_run(g, ['say ' + text[:200]])
            except Err:
                pass

    def save_world(self):
        g = self.cfg.get('game')
        if g and g.get('type') == 'minecraft':
            try:
                rcon_run(g, ['save-all flush'])
            except Err:
                pass

    def do_reboot(self):
        self.going_down = int(time.time())
        for hub in self.cfg['hubs']:  # last signal: watchmen mute the node's alerts and record the reboot
            try:
                self.beat_once(hub, timeout=5)
            except Exception:
                pass
        time.sleep(1)
        for cmd in (['systemctl', 'reboot'], ['reboot'], ['shutdown', '-r', 'now']):
            if shutil.which(cmd[0]) and sh(cmd, timeout=30)[0] == 0:
                return
        self.going_down = 0
        log(L('перезагрузка не удалась', 'reboot failed'))

    def reboot_worker(self, mode, hours, say5, say1):
        try:
            if mode == 'warn':
                self.pending = {'mode': 'warn', 'since': int(time.time()), 'at': int(time.time() + WARN_LEAD)}
                self.say(say5)
                if self.cancel.wait(WARN_LEAD * 0.8):
                    return
                self.say(say1)
                if self.cancel.wait(WARN_LEAD * 0.2):
                    return
                self.save_world()
                self.do_reboot()
            elif mode == 'empty':
                deadline = time.time() + hours * REBOOT_HOUR
                self.pending = {'mode': 'empty', 'since': int(time.time()), 'until': int(deadline), 'hours': hours}
                while True:
                    imp = self.impact()
                    self.pending['left'] = imp['total']
                    self.kick.set()
                    if imp['total'] == 0:
                        self.save_world()
                        self.do_reboot()
                        return
                    if time.time() > deadline:
                        self.flags['reboot_gaveup'] = {'ts': int(time.time()), 'hours': hours}
                        return
                    if self.cancel.wait(REBOOT_POLL):
                        return
            else:
                time.sleep(2)
                self.do_reboot()
        finally:
            if not self.going_down:
                self.pending = None
                self.kick.set()

    # --- panel commands

    def _svc(self, unit):
        unit = str(unit or '')
        with self.lock:
            for s in self.cfg.get('services', []):
                if s['unit'] == unit:
                    return s
        raise Err('svc_not_listed', 'Службы %s нет в списке панели' % unit, unit=unit)

    @staticmethod
    def _label(args, unit, old=None):
        label = str(args.get('label') or '').strip()[:40] or (old or {}).get('label') or re.sub(r'^docker:|\.service$', '', unit)
        kind = args.get('kind') if args.get('kind') in KINDS else (old or {}).get('kind', 'other')
        link = str(args.get('link') or '').strip()[:300]
        if link and not re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', link):
            link = 'https://' + link
        if link and not re.match(r'^(https?|tg):', link):
            raise Err('link_scheme', 'Ссылка должна начинаться с https://, http:// или tg://')
        return {'unit': unit, 'label': label, 'kind': kind, 'link': link}

    def _add(self, a):
        unit = str(a.get('unit') or '')
        if not (UNIT_RE.match(unit) or DOCKER_RE.match(unit)):
            raise Err('svc_name', 'Имя службы должно оканчиваться на .service или начинаться с docker:')
        if not unit.startswith('docker:') and INIT != 'systemd':
            raise Err('no_init', NO_INIT)
        if service_states([unit]).get(unit, {}).get('state', 'missing') == 'missing':
            raise Err('svc_missing', 'Службы %s нет на этом узле' % unit, unit=unit)
        with self.lock:
            lst = self.cfg.setdefault('services', [])
            if any(s['unit'] == unit for s in lst):
                raise Err('svc_exists', 'Эта служба уже в панели')
            if len(lst) >= 40:
                raise Err('svc_limit', 'На узле уже 40 служб в панели, больше нельзя')
            lst.append(self._label(a, unit))
            self.cfg['dismissed'] = [d for d in self.cfg.get('dismissed', []) if d != unit]
            self.save()
        self.candidates = [c for c in self.candidates if c['unit'] != unit]

    def run_cmd(self, c):
        t = c.get('type') or ''
        a = c.get('args') or {}
        try:
            return self.dispatch(t, a)
        except Err as e:
            return {'ok': False, 'error': str(e), 'code': e.code, 'p': e.p}
        except ValueError as e:
            return {'ok': False, 'error': str(e)}
        except Exception as e:
            log(L('команда %s: %r', 'command %s: %r') % (t, e))
            return {'ok': False, 'error': 'Сбой агента: %s' % e, 'code': 'agent_crash', 'p': {'err': str(e)[:200]}}

    def dispatch(self, t, a):
        # --- services
        if t in ('svc.restart', 'svc.start', 'svc.stop'):
            s = self._svc(a.get('unit'))
            verb = t.split('.')[1]
            if s['unit'].startswith('docker:'):
                rc, out = sh(['docker', verb, s['unit'][7:]], timeout=60)
            else:
                if INIT != 'systemd':
                    raise Err('no_init', NO_INIT)
                rc, out = sh(['systemctl', verb, '--', s['unit']], timeout=50)
            self.services(fresh=True)
            if rc != 0:
                raise Err('verb_failed', '%s не удался: %s' % (verb, out.strip()[-400:] or 'код %d' % rc), verb=verb, out=out.strip()[-400:])
            return {'ok': True, 'output': ''}
        if t == 'svc.logs':
            s = self._svc(a.get('unit'))
            n = max(10, min(200, int(a.get('lines') or 80)))
            if s['unit'].startswith('docker:'):
                rc, out = sh(['docker', 'logs', '--tail', str(n), '--timestamps', s['unit'][7:]], timeout=15)
            else:
                rc, out = sh(['journalctl', '-u', s['unit'], '-n', str(n), '--no-pager', '-o', 'short'], timeout=15)
            return {'ok': True, 'output': out[-16000:]}
        if t == 'svc.discover':
            units = discover_units()
            if not units and INIT != 'systemd':
                raise Err('no_init', NO_INIT)
            return {'ok': True, 'units': units}
        if t == 'svc.suggest':
            return {'ok': True, 'units': self.suggest()}
        if t == 'svc.add':
            self._add(a)
            self.services(fresh=True)
            return {'ok': True}
        if t == 'svc.add_many':
            added, errors = 0, []
            for item in (a.get('items') or [])[:40]:
                try:
                    self._add(item)
                    added += 1
                except Err as e:
                    errors.append({'unit': item.get('unit'), 'code': e.code, 'error': str(e)})
            self.services(fresh=True)
            return {'ok': True, 'added': added, 'errors': errors}
        if t == 'svc.edit':
            s = self._svc(a.get('unit'))
            with self.lock:
                s.update(self._label(a, s['unit'], s))
                self.save()
            return {'ok': True}
        if t == 'svc.remove':
            s = self._svc(a.get('unit'))
            with self.lock:
                self.cfg['services'] = [x for x in self.cfg['services'] if x['unit'] != s['unit']]
                self.save()
            return {'ok': True}
        if t == 'svc.dismiss':
            unit = str(a.get('unit') or '')[:200]
            with self.lock:
                d = self.cfg.setdefault('dismissed', [])
                if unit not in d:
                    d.append(unit)
                    del d[:-200]
                self.save()
            self.candidates = [x for x in self.candidates if x['unit'] != unit]
            return {'ok': True}
        if t == 'impact':
            return {'ok': True, 'impact': self.impact(a.get('unit') or None)}
        # --- node
        if t == 'node.reboot':
            if self.pending:
                raise Err('reboot_pending', 'Перезагрузка уже ждёт своего часа')
            mode = a.get('mode') if a.get('mode') in ('now', 'warn', 'empty') else 'now'
            hours = max(1, min(24, int(a.get('hours') or 3)))
            self.cancel.clear()
            if mode == 'now':
                return {'ok': True, '_after': lambda: self.reboot_worker('now', 0, '', '')}
            threading.Thread(target=self.reboot_worker, args=(mode, hours, str(a.get('say5') or ''), str(a.get('say1') or '')), daemon=True).start()
            return {'ok': True, 'pending': mode}
        if t == 'node.reboot_cancel':
            if not self.pending:
                raise Err('nothing_pending', 'Перезагрузка не запланирована')
            self.cancel.set()
            self.pending = None
            return {'ok': True}
        if t == 'node.edit':
            with self.lock:
                if a.get('name'):
                    self.cfg['name'] = str(a['name']).strip()[:24]
                if 'place' in a:
                    self.cfg['place'] = str(a.get('place') or '').strip()[:24]
                self.save()
            return {'ok': True}
        if t == 'maint.check':
            self.maint = maint_check()
            return {'ok': True, 'maint': self.maint}
        if t == 'diag.probe':
            return {'ok': True, 'probe': probe(a.get('ip'), a.get('port'))}
        # --- game console (mc.* are 1.0 names)
        if t in ('game.setup', 'mc.setup'):
            old = self.cfg.get('game') or {}
            typ = a.get('type') if a.get('type') in GAME_TYPES else old.get('type', 'minecraft')
            cfg = {'type': typ, 'host': str(a.get('host') or '127.0.0.1').strip()[:100], 'port': int(a.get('port') or 25575),
                   'password': str(a.get('password') or '') or old.get('password', ''), 'quick': old.get('quick', [])}
            rcon_run(cfg, [])
            with self.lock:
                self.cfg['game'] = cfg
                if t == 'mc.setup' and a.get('backups'):
                    self._backup_add({'path': a['backups'], 'label': 'Minecraft'}, quiet=True)
                self.save()
            self.game = game_probe(cfg)
            return {'ok': True}
        if t in ('game.disable', 'mc.disable'):
            with self.lock:
                self.cfg.pop('game', None)
                self.save()
            self.game = None
            return {'ok': True}
        if t in ('game.cmd', 'mc.cmd'):
            cfg = self.cfg.get('game')
            if not cfg:
                raise Err('game_off', 'Игровой сервер не подключён к панели')
            cmd = str(a.get('command') or '').strip()[:256]
            if cfg.get('type') == 'minecraft':
                cmd = cmd.lstrip('/')
            if not cmd:
                raise Err('empty_cmd', 'Пустая команда')
            return {'ok': True, 'output': rcon_run(cfg, [cmd])[0][:8000]}
        if t == 'game.quick':
            with self.lock:
                cfg = self.cfg.get('game')
                if not cfg:
                    raise Err('game_off', 'Игровой сервер не подключён к панели')
                q = cfg.setdefault('quick', [])
                if a.get('op') == 'remove':
                    i = int(a.get('index', -1))
                    if 0 <= i < len(q):
                        q.pop(i)
                else:
                    label, cmd = str(a.get('label') or '').strip()[:30], str(a.get('command') or '').strip()[:256]
                    if not cmd:
                        raise Err('empty_cmd', 'Пустая команда')
                    if len(q) >= 16:
                        raise Err('quick_limit', 'Кнопок уже 16, больше нельзя')
                    q.append({'label': label or cmd[:30], 'command': cmd})
                self.save()
            return {'ok': True}
        # --- backups
        if t == 'backup.add':
            self._backup_add(a)
            return {'ok': True}
        if t == 'backup.remove':
            path = str(a.get('path') or '')
            with self.lock:
                self.cfg['backups'] = [b for b in self.cfg.get('backups', []) if b['path'] != path]
                self.save()
            self.bk_cache = (0, [])
            return {'ok': True}
        if t == 'backup.suggest':
            with self.lock:
                known = {b['path'] for b in self.cfg.get('backups', [])}
            return {'ok': True, **backup_suggest(known)}
        # --- node actions
        if t == 'action.run':
            act = next((x for x in load_actions() if x['id'] == a.get('id')), None)
            if not act:
                raise Err('action_missing', 'Такого действия в actions.ini больше нет')
            log(L('действие «%s»', 'action "%s"') % act['name'])
            rc, out = run_action(act)
            return {'ok': rc == 0, 'output': out, 'rc': rc, **({} if rc == 0 else {'error': 'Действие завершилось с кодом %d' % rc, 'code': 'action_failed', 'p': {'rc': rc}})}
        raise Err('unknown_cmd', 'Агент %s не знает команду %s' % (VERSION, t), cmd=t)

    def _backup_add(self, a, quiet=False):
        path = os.path.normpath(str(a.get('path') or '').strip())
        if not path.startswith('/') or not os.path.isdir(path):
            raise Err('folder_missing', 'Папки %s нет на узле' % path, path=path)
        with self.lock:
            bl = self.cfg.setdefault('backups', [])
            if any(b['path'] == path for b in bl):
                if quiet:
                    return
                raise Err('backup_exists', 'За этой папкой панель уже следит')
            if len(bl) >= 10:
                raise Err('backup_limit', 'Папок с бэкапами уже 10, больше нельзя')
            bl.append({'path': path, 'label': str(a.get('label') or '').strip()[:40] or os.path.basename(path)})
            self.save()
        self.bk_cache = (0, [])

# ---------------------------------------------------------------- watchman (hub)

CAUSE_RU = {'clean': 'узел выключили штатно', 'oom': 'кончилась память, ядро убивало процессы', 'panic': 'паника ядра',
            'lockup': 'система зависла', 'abrupt': 'обрыв без записей в журнале: похоже на пропажу питания или действия хостера',
            'unknown': 'журнал прошлой загрузки не сохранился'}


class Hub:
    def __init__(self, cfg, state_path=HUB_STATE):
        self.cfg = cfg
        self.state_path = state_path
        self.cv = threading.Condition(threading.RLock())
        self.started = time.time()
        st = jload(state_path, {}) or {}
        self.nodes = st.get('nodes', {})
        self.events = st.get('events', [])
        self.seq = st.get('seq', 0)
        self.hist = {k: collections.deque(v, maxlen=1440) for k, v in st.get('hist', {}).items()}
        self.acc = {}  # current minute's accumulator
        self.ver = int(time.time()) % 100000 * 1000
        self.cmds = {}  # node -> command queue
        self.results = {}   # cmd id -> {'ev', 'res'}
        self.expect = {}  # (node, unit) -> stop expected until
        self.cool = {}  # (node, unit|mount, type) -> last event time
        self.views = {}  # neighbour watchman id -> {'ts', 'ages'}
        self.bad = {}  # ip -> [count, window start]
        self.diag_busy = set()
        self.pollgen = {}  # node -> latest poll number
        for n in self.nodes.values():  # after a restart, give nodes time to report
            if n.get('status') in ('up', 'stale'):
                n['last_seen'] = self.started
            for k, v in (('disk_alarm', []), ('backup_alarm', []), ('ssh_known', []), ('login_seen', []), ('flags_seen', {})):
                n.setdefault(k, v)

    # --- events

    def emit(self, typ, sev, node, title, text='', key='node', p=None):
        # key identifies the alert subject on a node: the app replaces an alert with the all-clear of the same key.
        # p holds the params the app builds localized text from; title and text are a Russian fallback.
        self.seq += 1
        e = {'seq': self.seq, 'ts': int(time.time()), 'type': typ, 'sev': sev, 'node': node, 'key': key, 'title': title, 'text': text, 'p': p or {}}
        self.events.append(e)
        del self.events[:-300]
        self.bump()
        if SRV_LANG == 'en':
            log('event [%s] %s %s %s' % (sev, typ, node, json.dumps(p or {}, ensure_ascii=False)))
        else:
            log('событие [%s] %s. %s' % (sev, title, text))

    def bump(self):
        self.ver += 1
        self.cv.notify_all()

    def cooled(self, key, sec):
        now = time.time()
        if now - self.cool.get(key, 0) < sec:
            return False
        self.cool[key] = now
        return True

    def muted(self, n):
        return n.get('muted_until', 0) > time.time()

    # --- agent signal

    def on_beat(self, b, ip=''):
        nid = b.get('node')
        if not isinstance(nid, str) or not NODE_RE.match(nid):
            raise ValueError('плохой идентификатор узла')
        now = time.time()
        with self.cv:
            n = self.nodes.get(nid)
            name = str(b.get('name') or nid)[:24]
            if n is None:
                n = self.nodes[nid] = {'id': nid, 'order': now, 'status': 'new', 'since': now, 'services': [], 'disk_alarm': [], 'muted_until': 0,
                                       'backup_alarm': [], 'ssh_known': [], 'login_seen': [], 'flags_seen': {}}
                self.emit('node_new', 'info', nid, 'Новый узел %s' % name, 'Агент вышел на связь впервые.', key='new', p={'name': name})
            prev_status = n.get('status')
            prev_svcs = {s['unit']: s for s in n.get('services', [])}
            m = b.get('m') or {}
            try:
                local = ipaddress.ip_address(ip).is_loopback
            except ValueError:
                local = True
            addr = (self.cfg.get('host') if local else ip) or n.get('addr', '')
            n.update({'name': name, 'place': str(b.get('place') or '')[:24], 'hub': bool(b.get('hub')), 'agent': str(b.get('agent') or '')[:16],
                      'last_seen': now, 'm': m, 'addr': addr, 'game': b.get('game'), 'mc': b.get('mc'),
                      'actions': (b.get('actions') or [])[:40], 'backups': (b.get('backups') or [])[:10], 'maint': b.get('maint'),
                      'candidates': (b.get('candidates') or [])[:5], 'pending': b.get('pending')})
            gd = b.get('going_down') or 0
            if gd and gd != n.get('gd_seen'):  # the agent said it is going down for a reboot
                n['gd_seen'] = gd
                n['rebooting'] = int(now)
                n['muted_until'] = now + REBOOT_MUTE
                self.emit('reboot', 'info', nid, '%s перезагружается из панели' % name, 'Тревоги по узлу заглушены на 10 минут.', key='reboot', p={'name': name})
            elif not gd and n.get('rebooting'):  # agent is back and not going down: the reboot finished or failed
                n.pop('rebooting', None)
            pm = b.get('pm')
            if isinstance(pm, dict) and pm.get('boot_id') and pm['boot_id'] != n.get('pm_boot'):
                n['pm_boot'] = pm['boot_id']
                n['pm'] = {k: pm.get(k) for k in ('cause', 'oom', 'last_ts', 'lines', 'why')}
                n['pm'].update(down_at=int(n.get('since', now)) if prev_status == 'down' else None, up_at=int(now))
            if prev_status != 'up':
                if prev_status == 'down':
                    self.node_returned(n, b, now)
                n['status'] = 'up'
                n['since'] = now - (m.get('uptime') or 0) if prev_status in ('new', 'down') else n.get('since', now)
                n['confirmed'] = None
            svcs = [s for s in (b.get('services') or []) if isinstance(s, dict) and s.get('unit')][:40]
            if prev_status in ('up', 'stale'):
                self.diff_services(n, prev_svcs, svcs)
            n['services'] = svcs
            self.check_disks(n)
            self.check_backups(n)
            self.check_boot(n, b.get('boot_check'))
            self.check_maint(n)
            self.check_logins(n, b.get('logins') or [])
            self.check_flags(n, b.get('flags') or {})
            if b.get('hub') and isinstance(b.get('view'), dict):
                self.views[nid] = {'ts': now, 'ages': {k: v for k, v in b['view'].items() if isinstance(v, (int, float))}}
            if b.get('hub') and 'hub_ok' in b:
                bad = 0 if b['hub_ok'] else n.get('hub_bad', 0) + 1
                if bad == 3:  # three signals in a row, to stay quiet during restarts
                    self.emit('hub_down', 'warn', nid, 'Сторож %s не отвечает' % name,
                              'Сам узел жив, но сторож на нём не работает. Пока тревоги идут только через сторожа %s.' % self.cfg['name'], key='hub',
                              p={'name': name, 'hub': self.cfg['name']})
                elif bad == 0 and n.get('hub_bad', 0) >= 3:
                    self.emit('hub_up', 'ok', nid, 'Сторож %s снова работает' % name, '', key='hub', p={'name': name})
                n['hub_bad'] = bad
            mem = m.get('mem') or [0, 0]
            disk = max([100.0 * d['used'] / d['total'] for d in m.get('disks') or [] if d.get('total')] or [0.0])
            a = self.acc.setdefault(nid, [int(now // 60), 0, 0.0, 0.0, 0.0])
            if a[0] != int(now // 60):
                self.flush_minute(nid, a)
                a[:] = [int(now // 60), 0, 0.0, 0.0, 0.0]
            a[1] += 1
            a[2] += float(m.get('cpu') or 0)
            a[3] += 100.0 * mem[0] / mem[1] if mem[1] else 0.0
            a[4] = max(a[4], disk)
            self.bump()

    def node_returned(self, n, b, now):
        nid, name = n['id'], n['name']
        gone = now - n.get('since', now)
        uptime = (b.get('m') or {}).get('uptime') or 0
        rebooted = uptime < gone + 120
        pm = n.get('pm') if rebooted and n.get('pm_boot') == b.get('boot_id') else None
        cause = (pm or {}).get('cause', '') if rebooted else ''
        if n.pop('quiet', False):
            why, text = 'panel', 'Вернулся после перезагрузки из панели, она заняла %s.' % dur_text(gone)
            title = '%s снова на связи' % name
        elif n.get('confirmed') is False:
            why, text = 'link', 'Сторож %s не видел его %s.' % (self.cfg['name'], dur_text(gone))
            title = 'Связь с %s восстановилась' % name
        elif not rebooted:
            why, text = 'net', 'Молчал %s, но не перезагружался: пропадала связь, а не сервер.' % dur_text(gone)
            title = '%s снова на связи' % name
        else:
            why = 'boot'
            text = 'Молчал %s и перезагрузился.%s' % (dur_text(gone), (' Причина: %s.' % CAUSE_RU.get(cause, cause)) if cause else '')
            title = '%s снова на связи' % name
        if pm is not None:
            n['pm']['down_at'] = int(n.get('since', now))
        self.emit('node_up', 'ok', nid, title, text, p={'name': name, 'gone': int(gone), 'why': why, 'cause': cause})

    def flush_minute(self, nid, a):
        if a[1]:
            self.hist.setdefault(nid, collections.deque(maxlen=1440)).append([a[0] * 60, round(a[2] / a[1], 1), round(a[3] / a[1], 1), round(a[4], 1)])

    def diff_services(self, n, prev, cur):
        if n.get('rebooting'):  # the node stops services itself before a reboot: not an incident
            return
        nid, name = n['id'], n['name']
        now = time.time()
        loud = not self.muted(n)  # a muted node records events without notifications
        for s in cur:
            p = prev.get(s['unit'])
            if not p:
                continue
            was, st, label = p.get('state'), s.get('state'), s.get('label') or s['unit']
            key = (nid, s['unit'])
            where = '%s, %s.' % (name, s['unit'])
            par = {'name': name, 'label': label, 'unit': s['unit']}
            if st == 'failed' and was != 'failed':
                if self.cooled(key + ('failed',), 300):
                    r = s.get('restarts') or 0
                    extra = ' Перезапускалась %d %s.' % (r, plural(r, ('раз', 'раза', 'раз'))) if r else ''
                    self.emit('svc_failed', 'crit' if loud else 'info', nid, 'Упала служба %s' % label, where + extra, key='svc:' + s['unit'], p=dict(par, restarts=r))
                n.setdefault('svc_bad', {})[s['unit']] = 1
            elif st == 'inactive' and was in ('active', 'activating', 'deactivating'):
                if self.expect.get(key, 0) < now and self.cooled(key + ('stopped',), 300):
                    self.emit('svc_stopped', 'warn' if loud else 'info', nid, 'Служба %s остановлена' % label, where + ' Её остановили не из панели.', key='svc:' + s['unit'], p=par)
                    n.setdefault('svc_bad', {})[s['unit']] = 1
            elif st == 'active' and was != 'active':
                if n.get('svc_bad', {}).pop(s['unit'], None):
                    self.emit('svc_ok', 'ok', nid, 'Служба %s снова работает' % label, where, key='svc:' + s['unit'], p=par)
            if st == 'active' and (s.get('restarts') or 0) - (p.get('restarts') or 0) > 0:
                dq = n.setdefault('svc_rs', {}).setdefault(s['unit'], [])
                dq.append(now)
                dq[:] = [t for t in dq if now - t < 600]
                if len(dq) >= 3 and self.cooled(key + ('flap',), 1800):
                    self.emit('svc_flap', 'warn' if loud else 'info', nid, 'Служба %s постоянно перезапускается' % label,
                              where + ' Уже %d %s за 10 минут.' % (len(dq), plural(len(dq), ('раз', 'раза', 'раз'))), key='svc:' + s['unit'], p=dict(par, n=len(dq)))
                    n.setdefault('svc_bad', {})[s['unit']] = 1

    def check_disks(self, n):
        alarm = set(n.get('disk_alarm') or [])
        for d in (n.get('m') or {}).get('disks') or []:
            if not d.get('total'):
                continue
            p = 100.0 * d['used'] / d['total']
            mnt = d.get('mount', '/')
            par = {'name': n['name'], 'mount': mnt, 'pct': int(round(p))}
            if p >= DISK_HIGH and mnt not in alarm:
                alarm.add(mnt)
                self.emit('disk_high', 'warn', n['id'], 'На %s кончается диск' % n['name'], 'Раздел %s занят на %d %%.' % (mnt, round(p)), key='disk:' + mnt, p=par)
            elif p < DISK_CLEAR and mnt in alarm:
                alarm.discard(mnt)
                self.emit('disk_ok', 'ok', n['id'], 'На %s снова есть место' % n['name'], 'Раздел %s занят на %d %%.' % (mnt, round(p)), key='disk:' + mnt, p=par)
        n['disk_alarm'] = sorted(alarm)

    @staticmethod
    def backup_late(b, now):
        every = b.get('every')
        return bool(every and b.get('ts') and now - b['ts'] > max(2 * every, every + 600 / _SPEED))

    def check_backups(self, n):
        now = time.time()
        alarm = set(n.get('backup_alarm') or [])
        present = set()
        for b in n.get('backups') or []:
            path = b.get('path')
            present.add(path)
            par = {'name': n['name'], 'label': b.get('label') or path, 'path': path, 'age': int(now - (b.get('ts') or now)), 'every': b.get('every')}
            if self.backup_late(b, now) and path not in alarm:
                alarm.add(path)
                self.emit('backup_late', 'warn', n['id'], 'Бэкапы %s на %s опаздывают' % (par['label'], n['name']),
                          'Последний %s назад, обычно раз в %s.' % (dur_text(par['age']), dur_text(b['every'])), key='backup:' + path, p=par)
            elif path in alarm and not self.backup_late(b, now):
                alarm.discard(path)
                self.emit('backup_ok', 'ok', n['id'], 'Бэкапы %s на %s снова идут' % (par['label'], n['name']), '', key='backup:' + path, p=par)
        n['backup_alarm'] = sorted(alarm & present)

    def check_boot(self, n, bc):
        if not isinstance(bc, dict) or not bc.get('boot_id') or bc['boot_id'] == n.get('bc_boot'):
            return
        n['bc_boot'] = bc['boot_id']
        missing = [x for x in bc.get('missing') or [] if isinstance(x, dict)][:20]
        n['boot_missing'] = [x.get('unit') for x in missing]
        if missing:
            labels = [x.get('label') or x.get('unit') for x in missing]
            self.emit('boot_missing', 'crit', n['id'], 'После загрузки %s не поднялось: %s' % (n['name'], ', '.join(labels)),
                      'До перезагрузки эти службы работали.', key='boot', p={'name': n['name'], 'labels': labels})

    def check_maint(self, n):
        mt = n.get('maint') or {}
        if mt.get('reboot') and not n.get('maint_flag'):
            n['maint_flag'] = True
            self.emit('maint_reboot', 'info', n['id'], '%s просит перезагрузку' % n['name'], 'После обновлений.', key='maint', p={'name': n['name'], 'why': mt.get('why', '')})
        elif not mt.get('reboot') and mt:
            n['maint_flag'] = False

    def check_logins(self, n, logins):
        seen = n.setdefault('login_seen', [])
        known = n.setdefault('ssh_known', [])
        for l in logins[-20:]:
            if not isinstance(l, dict) or not l.get('ip'):
                continue
            k = '%s|%s|%s' % (l.get('ts'), l.get('ip'), l.get('user'))
            if k in seen:
                continue
            seen.append(k)
            new = l['ip'] not in known
            par = {'name': n['name'], 'user': l.get('user', ''), 'ip': l['ip'], 'method': l.get('method', ''), 'new': new}
            title = ('Вход на %s с нового адреса' if new else 'Вход на %s') % n['name']
            self.emit('ssh_login', 'warn' if new else 'info', n['id'], title, '%s, %s, %s.' % (l.get('user', ''), l['ip'], l.get('method', '')),
                      key='ssh:' + l['ip'], p=par)
            if new:
                known.append(l['ip'])
        del seen[:-200]
        del known[:-200]

    def check_flags(self, n, flags):
        fs = n.setdefault('flags_seen', {})
        g = flags.get('reboot_gaveup')
        if isinstance(g, dict) and g.get('ts') != fs.get('reboot_gaveup'):
            fs['reboot_gaveup'] = g.get('ts')
            self.emit('reboot_gaveup', 'warn', n['id'], '%s так и не опустел' % n['name'],
                      'Перезагрузку отменил: за %s ч кто-то всё время был на узле.' % g.get('hours'), key='reboot', p={'name': n['name'], 'hours': g.get('hours')})

    # --- who went silent

    def peer_sees(self, nid):
        """True: a neighbour sees the node. False: neighbours exist and don't. None: nobody to ask."""
        now = time.time()
        answers = []
        for pid, v in self.views.items():
            peer = self.nodes.get(pid)
            if pid == nid or not peer or peer.get('status') != 'up' or now - v['ts'] > PEER_FRESH:
                continue
            age = v['ages'].get(nid)
            # signals reach both watchmen almost at once: if the node died, the neighbour has not heard it for a while either
            answers.append(age is not None and age + (now - v['ts']) < STALE_AFTER)
        if not answers:
            return None
        return any(answers)

    def went_down(self, n, both):
        name, place = n['name'], n.get('place') or ''
        mates = [x for x in self.nodes.values() if x is not n and place and x.get('place') == place and x.get('status') == 'down'
                 and x.get('confirmed') is not False and not self.muted(x) and abs(n['since'] - x.get('since', 0)) <= GROUP_WINDOW]
        tail = ' Оба сторожа его не видят.' if both else ''
        n['down_ts'] = time.time()
        if mates:
            group = sorted(mates + [n], key=lambda x: x.get('since', 0))
            names = [x['name'] for x in group]
            lead = min(mates, key=lambda x: x.get('down_ts', 0))  # it already has a loud alert; the group alert replaces it
            self.emit('node_down', 'info', n['id'], '%s молчит' % name, 'Сигнала нет больше минуты.' + tail, p={'name': name, 'both': both, 'group': place})
            self.emit('group_down', 'crit', lead['id'], 'Замолчали %s' % ', '.join(names),
                      'Все с места «%s» почти одновременно. Похоже, там пропал свет или интернет.' % place, key='node', p={'names': names, 'place': place})
        else:
            self.emit('node_down', 'crit', n['id'], '%s молчит' % name, 'Сигнала нет больше минуты.' + tail, p={'name': name, 'both': both})
        self.start_diag(n['id'])

    def tick(self):
        now = time.time()
        with self.cv:
            grace = now - self.started < 20
            for n in list(self.nodes.values()):
                st = n.get('status')
                if st == 'new' or 'last_seen' not in n:
                    continue
                age = now - n['last_seen']
                if st == 'up' and age >= STALE_AFTER:
                    n['status'] = 'stale'
                    self.bump()
                elif st == 'stale' and age >= DOWN_AFTER and not grace:
                    sees = self.peer_sees(n['id'])
                    n['status'] = 'down'
                    n['since'] = n['last_seen']
                    n['confirmed'] = None if sees is None else (not sees)
                    n.pop('diag', None)
                    if self.muted(n):
                        n['quiet'] = True
                        self.bump()
                    elif sees:
                        self.emit('link_lost', 'warn', n['id'], 'Сторож %s потерял %s' % (self.cfg['name'], n['name']),
                                  'Второй сторож узел видит. Похоже на обрыв связи между ними, сам узел жив.', p={'name': n['name'], 'hub': self.cfg['name']})
                    else:
                        self.went_down(n, sees is False)
                elif st == 'down' and n.get('confirmed') is False and not n.get('quiet'):
                    if self.peer_sees(n['id']) is False:  # the other watchman lost the node too
                        n['confirmed'] = True
                        self.went_down(n, True)
            for k in [k for k, v in self.expect.items() if v < now]:
                del self.expect[k]
            for nid, a in list(self.acc.items()):
                if a[0] != int(now // 60):
                    self.flush_minute(nid, a)
                    del self.acc[nid]

    def loop(self):
        last_save = time.time()
        while True:
            time.sleep(2)
            try:
                self.tick()
                if time.time() - last_save > 60:
                    self.save()
                    last_save = time.time()
            except Exception as e:
                log(L('сторож, обход: %r', 'hub, sweep: %r') % (e,))

    def save(self):
        with self.cv:
            st = {'nodes': self.nodes, 'events': self.events, 'seq': self.seq, 'hist': {k: list(v) for k, v in self.hist.items()}}
            try:
                jsave(self.state_path, st)
            except OSError as e:
                log(L('не удалось сохранить состояние:', 'could not save state:'), e)

    # --- checking a silent node from neighbours

    def start_diag(self, nid):
        if nid in self.diag_busy:
            return False
        self.diag_busy.add(nid)
        threading.Thread(target=self.diag_run, args=(nid,), daemon=True).start()
        return True

    def diag_run(self, nid):
        try:
            with self.cv:
                n = self.nodes.get(nid)
                if not n or not n.get('addr'):
                    return
                target, port, place = n['addr'], (n.get('m') or {}).get('ssh_port') or 22, n.get('place')
                ups = [x for x in self.nodes.values() if x['id'] != nid and x.get('status') == 'up']
                ups.sort(key=lambda x: (x.get('place') == place, x.get('order', 0)))
                probers = [(x['id'], x['name']) for x in ups[:3]]
                n['diag'] = {'ts': int(time.time()), 'ip': target, 'port': port, 'results': [], 'running': True}
                self.bump()
            results = []
            for pid, pname in probers:
                r = self.dispatch(pid, 'diag.probe', {'ip': target, 'port': port}, 70)
                results.append({'from': pname, 'ok': r.get('ok'), 'error': r.get('error'), **(r.get('probe') or {})})
                with self.cv:
                    if nid in self.nodes:
                        self.nodes[nid]['diag'] = {'ts': int(time.time()), 'ip': target, 'port': port, 'results': list(results), 'running': True}
                        self.bump()
            with self.cv:
                if nid in self.nodes:
                    self.nodes[nid]['diag'] = {'ts': int(time.time()), 'ip': target, 'port': port, 'results': results, 'running': False}
                    self.bump()
        finally:
            self.diag_busy.discard(nid)

    # --- answers to the app

    PUBLIC = ('place', 'addr', 'agent', 'm', 'services', 'mc', 'game', 'actions', 'backups', 'maint', 'candidates', 'pending', 'pm', 'diag', 'confirmed')

    def public_node(self, n):
        out = {'id': n['id'], 'name': n.get('name', n['id']), 'hub': bool(n.get('hub')), 'status': n.get('status', 'new'),
               'last_seen': int(n.get('last_seen', 0)), 'since': int(n.get('since', 0)), 'muted_until': int(n.get('muted_until', 0)),
               'boot_missing': [u for u in n.get('boot_missing') or [] if any(s['unit'] == u and s.get('state') != 'active' for s in n.get('services') or [])],
               'backup_alarm': n.get('backup_alarm') or [],
               'rebooting': n.get('rebooting') if n.get('rebooting') and time.time() - n['rebooting'] < REBOOT_MUTE else None}
        for k in self.PUBLIC:
            out[k] = n.get(k)
        out['place'] = out['place'] or ''
        out['m'] = out['m'] or {}
        out['services'] = out['services'] or []
        return out

    def state(self):
        with self.cv:
            nodes = sorted(self.nodes.values(), key=lambda n: n.get('order', 0))
            return {'v': self.ver, 'ts': int(time.time()), 'hub': {'id': self.cfg['id'], 'name': self.cfg['name'], 'version': VERSION},
                    'nodes': [self.public_node(n) for n in nodes], 'events': self.events[-100:]}

    def wait_state(self, since, wait):
        end = time.time() + wait
        with self.cv:
            if since > self.ver:  # watchman restarted, versions start over
                return self.state()
            while self.ver <= since:
                left = end - time.time()
                if left <= 0:
                    return {'v': self.ver, 'ts': int(time.time()), 'same': True}
                self.cv.wait(left)
        time.sleep(0.25)  # batch close changes into one answer
        return self.state()

    def summary(self):
        now = time.time()
        nodes = list(self.nodes.values())
        down = [{'id': n['id'], 'name': n.get('name', n['id'])} for n in nodes if n.get('status') == 'down' and n.get('confirmed') is not False and not self.muted(n)]
        failed = sum(1 for n in nodes if n.get('status') == 'up' for s in n.get('services', []) if s.get('state') == 'failed')
        return {'total': len(nodes), 'up': sum(1 for n in nodes if n.get('status') in ('up', 'stale')), 'down': down, 'failed': failed, 'ts': int(now)}

    def problems(self):
        now = time.time()
        out = []
        for n in sorted(self.nodes.values(), key=lambda x: x.get('order', 0)):
            nid, name, st = n['id'], n.get('name', n['id']), n.get('status')
            if st == 'down':
                if n.get('confirmed') is not False and not self.muted(n):
                    out.append({'node': nid, 'key': 'node', 'sev': 'crit', 'type': 'node_down', 'p': {'name': name, 'gone': int(now - n.get('since', now))},
                                'title': '%s молчит' % name, 'text': 'Сигнала нет %s.' % dur_text(now - n.get('since', now))})
                continue
            if st not in ('up', 'stale') or self.muted(n):
                continue
            for sv in n.get('services', []):
                if sv.get('state') == 'failed':
                    lb = sv.get('label') or sv['unit']
                    out.append({'node': nid, 'key': 'svc:' + sv['unit'], 'sev': 'crit', 'type': 'svc_failed', 'p': {'name': name, 'label': lb, 'unit': sv['unit']},
                                'title': 'Упала служба %s' % lb, 'text': '%s, %s.' % (name, sv['unit'])})
            for mnt in n.get('disk_alarm') or []:
                d = next((x for x in (n.get('m') or {}).get('disks') or [] if x.get('mount') == mnt and x.get('total')), None)
                out.append({'node': nid, 'key': 'disk:' + mnt, 'sev': 'warn', 'type': 'disk_high', 'p': {'name': name, 'mount': mnt, 'pct': int(round(100.0 * d['used'] / d['total'])) if d else None},
                            'title': 'На %s кончается диск' % name, 'text': 'Раздел %s.' % mnt})
            for b in n.get('backups') or []:
                if b.get('path') in (n.get('backup_alarm') or []):
                    lb = b.get('label') or b['path']
                    out.append({'node': nid, 'key': 'backup:' + b['path'], 'sev': 'warn', 'type': 'backup_late',
                                'p': {'name': name, 'label': lb, 'path': b['path'], 'age': int(now - (b.get('ts') or now)), 'every': b.get('every')},
                                'title': 'Бэкапы %s на %s опаздывают' % (lb, name), 'text': ''})
            if n.get('hub_bad', 0) >= 3:
                out.append({'node': nid, 'key': 'hub', 'sev': 'warn', 'type': 'hub_down', 'p': {'name': name, 'hub': self.cfg['name']},
                            'title': 'Сторож %s не отвечает' % name, 'text': 'Сам узел жив, но сторож на нём не работает.'})
        return out[:40]

    def wait_events(self, after, wait):
        end = time.time() + wait
        with self.cv:
            while self.seq <= after:
                left = end - time.time()
                if left <= 0:
                    break
                self.cv.wait(left)
            if after > self.seq:  # watchman reinstalled, the count starts over
                after = 0
            return {'seq': self.seq, 'events': [e for e in self.events if e['seq'] > after][-30:], 'summary': self.summary(),
                    'problems': self.problems(), 'hub': {'id': self.cfg['id'], 'name': self.cfg['name']}}

    def history(self, nid, hours=24, frm=None, to=None):
        with self.cv:
            rows = list(self.hist.get(nid, []))
        if frm and to:
            frm, to = int(frm), int(to)
            step = 60 if to - frm <= 3 * 3600 else 300
            start = frm // step * step
            n = max(1, min(600, (to - start) // step + 1))
        else:
            step = 300
            end = int(time.time() // step) * step + step
            start = end - max(1, min(24, hours)) * 3600
            n = (end - start) // step
        sums = [[0.0, 0.0, 0.0, 0] for _ in range(n)]
        for r in rows:
            i = (r[0] - start) // step
            if 0 <= i < n:
                s = sums[i]
                s[0] += r[1]
                s[1] += r[2]
                s[2] = max(s[2], r[3] if len(r) > 3 else 0.0)
                s[3] += 1
        return {'step': step, 'from': start, 'cpu': [round(s[0] / s[3], 1) if s[3] else None for s in sums],
                'mem': [round(s[1] / s[3], 1) if s[3] else None for s in sums], 'disk': [round(s[2], 1) if s[3] else None for s in sums]}

    # --- commands

    def dispatch(self, nid, typ, args, timeout):
        """Send a command to the agent and wait for the reply."""
        with self.cv:
            n = self.nodes.get(nid)
            if not n:
                return {'ok': False, 'error': 'Сторож %s не знает такого узла' % self.cfg['name'], 'code': 'hub_no_node', 'p': {'hub': self.cfg['name']}}
            if n.get('status') == 'down':
                return {'ok': False, 'error': '%s молчит, команду передать некому' % n.get('name', nid), 'code': 'node_silent', 'p': {'name': n.get('name', nid)}}
            cid = secrets.token_hex(8)
            slot = self.results[cid] = {'ev': threading.Event(), 'res': None}
            self.cmds.setdefault(nid, collections.deque(maxlen=20)).append({'id': cid, 'type': typ, 'args': args, 'ts': time.time()})
            self.cv.notify_all()
        ok = slot['ev'].wait(timeout)
        with self.cv:
            self.results.pop(cid, None)
            if not ok:
                q = self.cmds.get(nid)
                if q:
                    self.cmds[nid] = collections.deque([c for c in q if c['id'] != cid], maxlen=20)
                return {'ok': False, 'error': '%s не ответил на команду вовремя' % n.get('name', nid), 'code': 'node_timeout', 'p': {'name': n.get('name', nid)}}
            return slot['res'] or {'ok': False, 'error': 'Пустой ответ агента', 'code': 'empty_result'}

    def command(self, body):
        nid, typ, args = body.get('node'), str(body.get('type') or ''), body.get('args') or {}
        with self.cv:
            n = self.nodes.get(nid)
            unit = args.get('unit') if isinstance(args, dict) else None
            if n and typ in ('svc.stop', 'svc.restart', 'svc.start') and unit:
                self.expect[(nid, unit)] = time.time() + 180
                if typ == 'svc.stop':  # stopped on purpose: no "running again" later
                    n.get('svc_bad', {}).pop(unit, None)
        slow = typ in ('svc.restart', 'svc.start', 'svc.stop', 'svc.suggest', 'backup.suggest', 'maint.check', 'impact', 'diag.probe')
        res = self.dispatch(nid, typ, args, 70 if slow else 28 if not typ.startswith('action.') else 620)
        return res

    def poll(self, nid, wait):
        end = time.time() + wait
        with self.cv:
            # a newer poll from the node supersedes the old one, which may belong to a killed agent and would swallow the command
            gen = self.pollgen[nid] = self.pollgen.get(nid, 0) + 1
            while True:
                if self.pollgen.get(nid) != gen:
                    return {'cmds': []}
                q = self.cmds.get(nid)
                if q:
                    fresh = [c for c in q if time.time() - c['ts'] < 60]
                    q.clear()
                    if fresh:
                        return {'cmds': [{'id': c['id'], 'type': c['type'], 'args': c['args']} for c in fresh]}
                left = end - time.time()
                if left <= 0:
                    return {'cmds': []}
                self.cv.wait(min(left, 5))

    def result(self, body):
        with self.cv:
            slot = self.results.get(body.get('id'))
            if slot:
                slot['res'] = {k: v for k, v in body.items() if k != 'id'}
                slot['ev'].set()

    def mute(self, nid, minutes):
        with self.cv:
            n = self.nodes.get(nid)
            if not n:
                return {'ok': False, 'error': 'Нет такого узла', 'code': 'hub_no_node', 'p': {'hub': self.cfg['name']}}
            n['muted_until'] = time.time() + max(0, min(24 * 60, int(minutes))) * 60 if minutes else 0
            self.bump()
            return {'ok': True}

    def forget(self, nid):
        with self.cv:
            if self.nodes.pop(nid, None) is None:
                return {'ok': False, 'error': 'Нет такого узла', 'code': 'hub_no_node', 'p': {'hub': self.cfg['name']}}
            self.hist.pop(nid, None)
            self.views.pop(nid, None)
            self.cmds.pop(nid, None)
            self.bump()
            return {'ok': True}

    def diag(self, nid):
        with self.cv:
            if nid not in self.nodes:
                return {'ok': False, 'error': 'Нет такого узла', 'code': 'hub_no_node', 'p': {'hub': self.cfg['name']}}
        return {'ok': True, 'started': self.start_diag(nid)}

    def view(self):
        now = time.time()
        with self.cv:
            return {'ages': {n['id']: int(now - n['last_seen']) for n in self.nodes.values() if 'last_seen' in n and n.get('status') != 'new'}}

    # --- key brute-force protection

    def blocked(self, ip):
        b = self.bad.get(ip)
        return bool(b and b[0] >= 20 and time.time() - b[1] < 600)

    def strike(self, ip):
        now = time.time()
        b = self.bad.get(ip)
        if not b or now - b[1] > 600:
            if len(self.bad) > 5000:
                self.bad.clear()
            self.bad[ip] = [1, now]
        else:
            b[0] += 1

def token_ok(given, expected):
    return bool(expected) and hmac.compare_digest(given.encode('utf-8'), expected.encode('utf-8'))


def make_handler(hub):
    cfg = hub.cfg

    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        server_version = 'laputa'
        sys_version = ''

        def setup(self):
            self.request.settimeout(20)
            super().setup()

        def log_message(self, *a):
            pass

        def send(self, code, obj=None, raw=None, ctype='application/json'):
            data = raw if raw is not None else json.dumps(obj if obj is not None else {}, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Connection', 'close')
            self.send_header('Cache-Control', 'no-store')
            if cfg.get('cors'):
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Access-Control-Allow-Headers', 'Authorization, Content-Type')
            self.end_headers()
            self.wfile.write(data)
            self.close_connection = True

        def deny(self):
            hub.strike(self.client_address[0])
            time.sleep(0.4)
            self.send(404, {'error': 'not found'})

        def body(self):
            n = int(self.headers.get('Content-Length') or 0)
            if n <= 0 or n > 512 * 1024:
                return {}
            return json.loads(self.rfile.read(n).decode('utf-8'))

        def do_OPTIONS(self):
            self.send(200 if cfg.get('cors') else 404, {})

        def do_GET(self):
            self.route('GET')

        def do_POST(self):
            self.route('POST')

        def route(self, method):
            try:
                self.handle_route(method)
            except (BrokenPipeError, ConnectionError, socket.timeout, ssl.SSLError):
                self.close_connection = True
            except (ValueError, KeyError, TypeError) as e:
                try:
                    self.send(400, {'ok': False, 'error': L('Плохой запрос: %s', 'Bad request: %s') % e})
                except OSError:
                    pass
            except Exception as e:
                log(L('сторож, запрос %s: %r', 'hub, request %s: %r') % (self.path.split('?')[0][:40], e))
                try:
                    self.send(500, {'ok': False, 'error': L('Сбой сторожа', 'Hub failure')})
                except OSError:
                    pass

        def handle_route(self, method):
            ip = self.client_address[0]
            if hub.blocked(ip):
                self.close_connection = True
                return
            u = urlsplit(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            path = u.path
            if path.startswith('/i/') and method == 'GET':  # installer download for nodes
                parts = path.split('/')
                if len(parts) == 4 and token_ok(parts[2], cfg['agent_token']) and parts[3] == 'laputa.py':
                    with open(os.path.abspath(__file__), 'rb') as f:
                        return self.send(200, raw=f.read(), ctype='text/x-python; charset=utf-8')
                return self.deny()
            auth = self.headers.get('Authorization') or ''
            tok = auth[7:] if auth.startswith('Bearer ') else ''
            if path.startswith('/a/'):
                if not token_ok(tok, cfg['agent_token']):
                    return self.deny()
                self.request.settimeout(60)
                if path == '/a/beat' and method == 'POST':
                    hub.on_beat(self.body(), ip)
                    return self.send(200, {'ok': True})
                if path == '/a/poll' and method == 'GET':
                    return self.send(200, hub.poll(q.get('node', ''), min(30, float(q.get('wait', 25)))))
                if path == '/a/result' and method == 'POST':
                    hub.result(self.body())
                    return self.send(200, {'ok': True})
                if path == '/a/view' and method == 'GET':
                    return self.send(200, hub.view())
                return self.send(404, {})
            if path.startswith('/v1/'):
                if not token_ok(tok, cfg['app_token']):
                    return self.deny()
                self.request.settimeout(100)
                if path == '/v1/cmd':
                    self.request.settimeout(660)  # node actions may take up to 10 minutes
                if path == '/v1/ping':
                    return self.send(200, {'ok': True, 'id': cfg['id'], 'name': cfg['name'], 'version': VERSION, 'ts': int(time.time())})
                if path == '/v1/state':
                    since, wait = int(q.get('since', 0)), min(25, float(q.get('wait', 0)))
                    return self.send(200, hub.wait_state(since, wait) if wait > 0 and since else hub.state())
                if path == '/v1/events':
                    wait = min(300, float(q.get('wait', 0)))
                    self.request.settimeout(wait + 40)
                    return self.send(200, hub.wait_events(int(q.get('after', 0)), wait))
                if path == '/v1/history':
                    return self.send(200, hub.history(q.get('node', ''), int(q.get('hours', 24)), q.get('from'), q.get('to')))
                if method == 'POST':
                    b = self.body()
                    if path == '/v1/cmd':
                        return self.send(200, hub.command(b))
                    if path == '/v1/mute':
                        return self.send(200, hub.mute(b.get('node'), b.get('minutes', 0)))
                    if path == '/v1/forget':
                        return self.send(200, hub.forget(b.get('node')))
                    if path == '/v1/diag':
                        return self.send(200, hub.diag(b.get('node')))
                return self.send(404, {})
            return self.deny()

    return H


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64
    limit = threading.BoundedSemaphore(96)

    def process_request(self, request, client_address):
        if not self.limit.acquire(blocking=False):
            try:
                request.close()
            except OSError:
                pass
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.limit.release()

    def handle_error(self, request, client_address):
        pass


def run_hub():
    cfg = jload(HUB_CFG)
    if not cfg:
        raise SystemExit(L('Нет %s. Запустите install-hub.', 'No %s. Run install-hub.') % HUB_CFG)
    hub = Hub(cfg)
    srv = Server((cfg.get('bind', '0.0.0.0'), int(cfg['port'])), make_handler(hub))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(TLS_CRT, TLS_KEY)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True, do_handshake_on_connect=False)
    threading.Thread(target=hub.loop, daemon=True).start()

    def stop(*a):
        hub.save()
        os._exit(0)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log(L('сторож %s (%s) слушает порт %s', 'hub %s (%s) listening on port %s') % (cfg['name'], VERSION, cfg['port']))
    srv.serve_forever(poll_interval=1)


# ---------------------------------------------------------------- install

HUB_UNIT = """[Unit]
Description={desc}
After=network-online.target
Wants=network-online.target

[Service]
User=laputa
Group=laputa
ExecStart={py} {opt}/laputa.py hub
Restart=always
RestartSec=3
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectControlGroups=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
ReadWritePaths={var}
MemoryMax=200M

[Install]
WantedBy=multi-user.target
"""

AGENT_UNIT = """[Unit]
Description={desc}
After=network-online.target
Wants=network-online.target

[Service]
ExecStart={py} {opt}/laputa.py agent
Restart=always
RestartSec=5
MemoryMax=150M

[Install]
WantedBy=multi-user.target
"""


def need_root():
    if os.geteuid() != 0:
        raise SystemExit(L('Нужны права root: запустите через sudo.', 'Root is required: run with sudo.'))


def python_path():
    return os.path.realpath(sys.executable or shutil.which('python3') or '/usr/bin/python3')


def make_user():
    """System user without login. useradd is almost everywhere; BusyBox (Alpine) has adduser instead."""
    if sh(['id', 'laputa'])[0] == 0:
        return
    nologin = shutil.which('nologin') or next((p for p in ('/usr/sbin/nologin', '/sbin/nologin', '/usr/bin/nologin') if os.path.exists(p)), '/bin/false')
    if shutil.which('useradd'):
        rc, out = sh(['useradd', '--system', '--no-create-home', '--home-dir', VAR, '--shell', nologin, 'laputa'])
    elif shutil.which('adduser'):
        rc, out = sh(['adduser', '-S', '-D', '-H', '-h', VAR, '-s', nologin, 'laputa'])
        if rc == 0 and shutil.which('addgroup') and sh(['getent', 'group', 'laputa'])[0] != 0:
            sh(['addgroup', '-S', 'laputa'])
            sh(['addgroup', 'laputa', 'laputa'])
    else:
        rc, out = 1, L('нет ни useradd, ни adduser', 'neither useradd nor adduser found')
    if rc:
        raise SystemExit(L('Не удалось создать пользователя laputa: ', 'Could not create user laputa: ') + out)


def own(path):
    try:
        shutil.chown(path, 'laputa', 'laputa')
    except LookupError:
        shutil.chown(path, 'laputa')  # no laputa group: the owner is enough


def start_unit(unit, text):
    """Enable and start a service. Without systemd, explain how to run it."""
    if INIT != 'systemd':
        return False
    sh(['systemctl', 'daemon-reload'])
    sh(['systemctl', 'enable', unit])
    rc, out = sh(['systemctl', 'restart', unit], timeout=30)
    if rc:
        raise SystemExit(L('%s не запустился: %s', '%s did not start: %s') % (text, out))
    return True


def open_port(port):
    if shutil.which('ufw') and 'Status: active' in sh(['ufw', 'status'])[1]:
        print('ufw:', sh(['ufw', 'allow', '%d/tcp' % port, 'comment', 'laputa'])[1].strip())
    elif shutil.which('firewall-cmd') and sh(['firewall-cmd', '--state'])[0] == 0:
        sh(['firewall-cmd', '--permanent', '--add-port=%d/tcp' % port])
        print('firewalld:', sh(['firewall-cmd', '--reload'])[1].strip())
    else:
        print(L('Ни ufw, ни firewalld не работают. Если у вас свои правила nftables или iptables, откройте порт %d/tcp сами.',
                'Neither ufw nor firewalld is active. If you use your own nftables or iptables rules, open port %d/tcp yourself.') % port)


def put_self():
    os.makedirs(OPT, exist_ok=True)
    os.makedirs(ETC, mode=0o755, exist_ok=True)
    src = os.path.abspath(__file__)
    dst = os.path.join(OPT, 'laputa.py')
    if src != dst:
        shutil.copyfile(src, dst)
    os.chmod(dst, 0o755)


def der_item(buf, pos):
    """Parse one DER element: returns (content start, element end)."""
    ln = buf[pos + 1]
    pos += 2
    if ln & 0x80:
        n = ln & 0x7f
        ln = int.from_bytes(buf[pos:pos + n], 'big')
        pos += n
    return pos, pos + ln


def spki_pin(der):
    """base64 sha256 of SubjectPublicKeyInfo, the format curl --pinnedpubkey expects."""
    body, _ = der_item(der, 0)            # Certificate
    pos, _ = der_item(der, body)          # TBSCertificate
    if der[pos] == 0xa0:                  # [0] version
        pos = der_item(der, pos)[1]
    for _ in range(5):                    # serial, signature, issuer, validity, subject
        pos = der_item(der, pos)[1]
    end = der_item(der, pos)[1]  # whole subjectPublicKeyInfo
    return base64.b64encode(hashlib.sha256(der[pos:end]).digest()).decode('ascii')


def cert_pins():
    with open(TLS_CRT, 'rb') as f:
        der = ssl.PEM_cert_to_DER_cert(f.read().decode('ascii'))
    return hashlib.sha256(der).hexdigest(), spki_pin(der)


def install_agent_cfg(hubs, name, place, is_hub=False):
    cfg = jload(AGENT_CFG, {}) or {}
    host = socket.gethostname()
    cfg.setdefault('id', slug(name or host))
    if name:
        cfg['name'] = name
    cfg.setdefault('name', host)
    if place is not None:
        cfg['place'] = place
    cfg.setdefault('place', '')
    cfg.setdefault('services', [])
    if is_hub:
        cfg['hub'] = True
    cfg['lang'] = SRV_LANG
    old = {h['url']: h for h in cfg.get('hubs', [])}
    for h in hubs:
        old[h['url']] = {**old.get(h['url'], {}), **h}
    cfg['hubs'] = list(old.values())
    jsave(AGENT_CFG, cfg)
    if not os.path.exists(ACTIONS_FILE):
        with open(ACTIONS_FILE, 'w', encoding='utf-8') as f:
            f.write(L(ACTIONS_EXAMPLE_RU, ACTIONS_EXAMPLE_EN))
        os.chmod(ACTIONS_FILE, 0o600)
    if INIT == 'systemd':
        with open(os.path.join(SYSTEMD_DIR, 'laputa-agent.service'), 'w') as f:
            f.write(AGENT_UNIT.format(opt=OPT, py=python_path(), desc=L('Laputa: агент', 'Laputa agent')))
        start_unit('laputa-agent.service', L('Агент', 'Agent'))
    else:
        print()
        print(L('На этом сервере нет systemd, поэтому службу я не создал. Добавьте в свою систему запуска команду (от root):',
                'There is no systemd here, so no service was created. Add this command to your init system (as root):'))
        print('  %s %s/laputa.py agent' % (python_path(), OPT))
    return cfg


def cmd_install_hub(a):
    need_root()
    if not shutil.which('openssl') and not (os.path.exists(TLS_CRT) and os.path.exists(TLS_KEY)):
        raise SystemExit(L('Нужна программа openssl, чтобы выпустить сертификат сторожа. Поставьте её пакетным менеджером: ',
                           'openssl is needed to issue the hub certificate. Install it with your package manager: ') +
                         'apt install openssl, dnf install openssl, pacman -S openssl, zypper install openssl, apk add openssl.')
    put_self()
    os.makedirs(VAR, exist_ok=True)
    make_user()
    old = jload(HUB_CFG, {}) or {}
    ag = jload(AGENT_CFG, {}) or {}  # reuse the agent's name if it is already installed
    host = socket.gethostname()
    name = a.name or old.get('name') or ag.get('name') or host
    port = a.port or old.get('port') or 8743
    cfg = {'id': old.get('id') or ag.get('id') or slug(a.name or host), 'name': name, 'port': port, 'host': a.host or old.get('host'),
           'app_token': old.get('app_token') or secrets.token_urlsafe(32), 'agent_token': old.get('agent_token') or secrets.token_urlsafe(32),
           'lang': SRV_LANG}
    if not cfg['host']:
        raise SystemExit(L('Укажите внешний адрес сервера: --host 203.0.113.5', 'Give the server\'s public address: --host 203.0.113.5'))
    if not (os.path.exists(TLS_CRT) and os.path.exists(TLS_KEY)):
        rc, out = sh(['openssl', 'req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:prime256v1', '-nodes', '-days', '3650',
                      '-subj', '/CN=laputa-' + cfg['id'], '-keyout', TLS_KEY, '-out', TLS_CRT], timeout=60)
        if rc:
            raise SystemExit(L('Не удалось создать сертификат: ', 'Could not create the certificate: ') + out)
    jsave(HUB_CFG, cfg)
    for p in (HUB_CFG, TLS_KEY, TLS_CRT):
        own(p)
        os.chmod(p, 0o600)
    own(VAR)
    if os.path.exists(HUB_STATE):
        own(HUB_STATE)
    os.chmod(ETC, 0o755)
    if INIT == 'systemd':
        with open(os.path.join(SYSTEMD_DIR, 'laputa-hub.service'), 'w') as f:
            f.write(HUB_UNIT.format(opt=OPT, var=VAR, py=python_path(), desc=L('Laputa: сторож', 'Laputa hub')))
        start_unit('laputa-hub.service', L('Сторож', 'Hub'))
    else:
        print(L('На этом сервере нет systemd, поэтому службу я не создал. Добавьте в свою систему запуска команду от пользователя laputa:',
                'There is no systemd here, so no service was created. Add this command to your init system, run as user laputa:'))
        print('  %s %s/laputa.py hub' % (python_path(), OPT))
    pin, _ = cert_pins()
    install_agent_cfg([{'url': 'https://127.0.0.1:%d' % port, 'pin': pin, 'token': cfg['agent_token'], 'local': True}], a.name, a.place, is_hub=True)
    if a.open_port:
        open_port(port)
    print()
    print(L('Сторож %s установлен%s.', 'Hub %s installed%s.') % (name, L(' и запущен', ' and started') if INIT == 'systemd' else ''))
    cmd_keys(a)


def cmd_keys(a):
    need_root()
    cfg = jload(HUB_CFG)
    if not cfg:
        raise SystemExit(L('На этом сервере сторож не установлен.', 'No hub is installed on this server.'))
    pin, spki = cert_pins()
    url = 'https://%s:%d' % (cfg['host'], cfg['port'])
    app = make_key('laputa1', {'url': url, 'pin': pin, 'token': cfg['app_token'], 'name': cfg['name']})
    agent = make_key('laputa1a', {'url': url, 'pin': pin, 'token': cfg['agent_token']})
    print()
    print(L('1. Ключ для приложения (вставить в Laputa на телефоне):', '1. Key for the app (paste it into Laputa on your phone):'))
    print()
    print(app)
    print()
    print(L('2. Команда для каждого остального узла (подставьте имя и место):', '2. Command for every other node (fill in the name and place):'))
    print()
    print("curl -fsSk --pinnedpubkey 'sha256//%s' %s/i/%s/laputa.py -o /tmp/laputa.py && sudo python3 /tmp/laputa.py install-agent --hub '%s' --lang %s --name %s --place %s"
          % (spki, url, cfg['agent_token'], agent, SRV_LANG, L('ИМЯ', 'NAME'), L('МЕСТО', 'PLACE')))
    print()
    print(L('   Нужны curl и python3 (3.7+). Если работаете от root, уберите sudo.', '   Needs curl and python3 (3.7+). If you are root, drop sudo.'))
    print()
    print(L('   Команду можно повторять на узле, где агент уже стоит: он добавит этого сторожа к прежним и сохранит свои службы.',
            '   You can repeat it on a node that already has the agent: it adds this hub to the others and keeps its services.'))
    print()
    print(L('Порт %d/tcp должен быть открыт снаружи.', 'Port %d/tcp must be reachable from outside.') % cfg['port'])


def cmd_rekey(a):
    """New app key, in case the phone is lost. Nodes keep their own key."""
    need_root()
    cfg = jload(HUB_CFG)
    if not cfg:
        raise SystemExit(L('На этом сервере сторож не установлен.', 'No hub is installed on this server.'))
    cfg['app_token'] = secrets.token_urlsafe(32)
    jsave(HUB_CFG, cfg)
    own(HUB_CFG)
    if INIT == 'systemd':
        sh(['systemctl', 'restart', 'laputa-hub.service'], timeout=30)
    else:
        print(L('Перезапустите сторожа сами, чтобы новый ключ вступил в силу.', 'Restart the hub yourself for the new key to take effect.'))
    print(L('Старый ключ приложения больше не действует. Удалите этого сторожа в приложении и добавьте заново новым ключом.',
            'The old app key no longer works. Remove this hub in the app and add it again with the new key.'))
    cmd_keys(a)


def cmd_install_agent(a):
    need_root()
    if not a.hub:
        raise SystemExit(L('Нужен хотя бы один ключ сторожа: --hub laputa1a:…', 'At least one hub key is needed: --hub laputa1a:…'))
    hubs = []
    for k in a.hub:
        try:
            h = read_key('laputa1a', k)
            hubs.append({'url': h['url'].rstrip('/'), 'pin': h['pin'], 'token': h['token']})
        except (ValueError, KeyError) as e:
            raise SystemExit(L('Ключ сторожа не читается: %s', 'Cannot read the hub key: %s') % e)
    put_self()
    for h in hubs:
        try:
            hub_call(h, 'GET', '/a/view', timeout=10)
            print(L('Сторож %s отвечает.', 'Hub %s is answering.') % h['url'])
        except Exception as e:
            print(L('Внимание: сторож %s сейчас не отвечает (%s). Агент будет пробовать сам.', 'Warning: hub %s is not answering now (%s). The agent will keep trying.') % (h['url'], e))
    cfg = install_agent_cfg(hubs, a.name, a.place)
    if INIT == 'systemd':
        print(L('Агент установлен. Узел «%s» появится в приложении через несколько секунд.', 'Agent installed. Node "%s" will show up in the app in a few seconds.') % cfg['name'])
    else:
        print(L('Агент настроен. Узел «%s» появится в приложении, когда вы запустите команду выше.', 'Agent configured. Node "%s" will show up in the app once you start the command above.') % cfg['name'])


def cmd_status(a):
    cfg = jload(HUB_CFG)
    if not cfg:
        ag = jload(AGENT_CFG)
        if not ag:
            raise SystemExit(L('Laputa на этом сервере не установлена (или нет прав прочитать настройки: попробуйте sudo).',
                               'Laputa is not installed here (or you cannot read its settings: try sudo).'))
        print(L('Агент узла «%s». Сторожа:', 'Agent of node "%s". Hubs:') % ag.get('name'))
        for h in ag.get('hubs', []):
            try:
                hub_call(h, 'GET', '/a/view', timeout=8)
                print(L('  %s  отвечает', '  %s  answering') % h['url'])
            except Exception as e:
                print(L('  %s  не отвечает: %s', '  %s  not answering: %s') % (h['url'], e))
        return
    pin, _ = cert_pins()
    st = hub_call({'url': 'https://127.0.0.1:%d' % cfg['port'], 'pin': pin, 'token': cfg['app_token']}, 'GET', '/v1/state')
    print(L('Сторож %s видит узлов: %d', 'Hub %s sees nodes: %d') % (cfg['name'], len(st['nodes'])))
    names = {'up': L('в строю', 'up'), 'stale': L('запаздывает', 'late'), 'down': L('молчит', 'silent'), 'new': L('не выходил на связь', 'never reported')}
    for n in st['nodes']:
        print(L('  %-14s %-20s служб: %d', '  %-14s %-20s services: %d') % (n['name'], names.get(n['status'], n['status']), len(n['services'])))


def cmd_uninstall(a):
    need_root()
    for u in ('laputa-agent.service', 'laputa-hub.service'):
        if INIT == 'systemd':
            sh(['systemctl', 'disable', '--now', u])
        try:
            os.remove(os.path.join(SYSTEMD_DIR, u))
        except OSError:
            pass
    if INIT == 'systemd':
        sh(['systemctl', 'daemon-reload'])
    for d in (ETC, VAR, OPT):
        shutil.rmtree(d, ignore_errors=True)
    print(L('Laputa убрана с этого сервера. Остались пользователь laputa и открытый порт в межсетевом экране, если вы его открывали: уберите их сами.',
            'Laputa is removed from this server. The user laputa and the firewall port (if you opened it) are left: remove them yourself.'))


def main():
    p = argparse.ArgumentParser(description=L('Laputa: агент и сторож', 'Laputa: agent and hub'))
    sub = p.add_subparsers(dest='cmd')
    sub.add_parser('hub')
    sub.add_parser('agent')
    h = sub.add_parser('install-hub')
    h.add_argument('--host')
    h.add_argument('--port', type=int)
    h.add_argument('--name')
    h.add_argument('--place')
    h.add_argument('--open-port', '--open-ufw', dest='open_port', action='store_true')
    h.add_argument('--lang', choices=('ru', 'en'))
    g = sub.add_parser('install-agent')
    g.add_argument('--hub', action='append')
    g.add_argument('--name')
    g.add_argument('--place')
    g.add_argument('--lang', choices=('ru', 'en'))
    sub.add_parser('keys')
    sub.add_parser('rekey')
    sub.add_parser('status')
    sub.add_parser('uninstall')
    a = p.parse_args()
    if getattr(a, 'lang', None):  # the install-time language is saved in the settings
        global SRV_LANG
        SRV_LANG = a.lang
    if a.cmd == 'hub':
        run_hub()
    elif a.cmd == 'agent':
        Agent().run()
    elif a.cmd == 'install-hub':
        cmd_install_hub(a)
    elif a.cmd == 'install-agent':
        cmd_install_agent(a)
    elif a.cmd == 'keys':
        cmd_keys(a)
    elif a.cmd == 'rekey':
        cmd_rekey(a)
    elif a.cmd == 'status':
        cmd_status(a)
    elif a.cmd == 'uninstall':
        cmd_uninstall(a)
    else:
        p.print_help()


if __name__ == '__main__':
    main()
