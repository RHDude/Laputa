
# ---------------------------------------------------------------- Docker

def parse_iso(s):
    """'2026-10-08T12:00:00.123456789Z' -> unix-время; нулевая дата Docker -> None."""
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
    """Все контейнеры: {имя: {state, sub, since, restarts, ports, image}}. Без Docker пусто."""
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
    """Состояние юнитов systemd и контейнеров Docker (имена вида docker:NAME) в одном словаре."""
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


# ---------------------------------------------------------------- сеть: кто слушает, кто подключён

def unit_of_pid(pid):
    """Юнит systemd, которому принадлежит процесс: по его cgroup."""
    try:
        with open('%s/%d/cgroup' % (PROC, pid)) as f:
            text = f.read()
    except OSError:
        return None
    m = re.findall(r'/([^/\n]+\.service)', text)
    return m[-1] if m else None


def listen_map():
    """{юнит: [порты]} для сокетов, которые слушают процессы служб. Нужен ss (iproute2)."""
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
    """{локальный порт: число установленных TCP-соединений с внешних адресов}."""
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
    """Активные пиры AmneziaWG и WireGuard: свежее рукопожатие за последние 3 минуты."""
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


# ---------------------------------------------------------------- игровая консоль (RCON)

GAME_TYPES = ('minecraft', 'rcon')
LIST_RE = re.compile(r'There are (\d+) of a max(?:imum)? of (\d+) players online:?\s*(.*)', re.S)


def rcon_run(cfg, commands):
    """Выполнить несколько команд за одно подключение. Возвращает ответы по порядку."""
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


# ---------------------------------------------------------------- бэкапы

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
    """Папки, похожие на бэкапы, и подсказки из таймеров и cron."""
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


# ---------------------------------------------------------------- действия узла

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


# ---------------------------------------------------------------- обслуживание: обновления и перезагрузка

def _ver_key(v):
    # 6.1.0-25-amd64 -> [6, 1, 0, 25, 64]: номер сборки ядра Debian и Ubuntu тоже идёт в сравнение
    return [int(x) for x in re.findall(r'\d+', v)]


def kernel_reboot_needed():
    """Установлено ядро новее работающего: нужна перезагрузка. Работает на любом дистрибутиве."""
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


# ---------------------------------------------------------------- входы по SSH

ACCEPT_RE = re.compile(r'Accepted (\S+) for (\S+) from (\S+) port (\d+)')


class LoginWatch:
    """Следит за успешными входами по SSH: журнал systemd или файлы журналов там, где его нет."""

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
            ino, off = self.files.get(path, (st.st_ino, st.st_size))   # при первом взгляде историю не читаем
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


# ---------------------------------------------------------------- проверка соседа снаружи

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


# ---------------------------------------------------------------- разбор прошлой загрузки

def read_boot_id():
    try:
        with open(BOOT_ID_FILE) as f:
            return f.read().strip()
    except OSError:
        return ''


def postmortem():
    """Чем закончилась прошлая загрузка: по её журналу, если он сохраняется между загрузками."""
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
        rest = re.sub(r'^\S+ ', '', rest, count=1)   # имя хоста
        tail.append([int(float(ts)), rest[:240]])
    return {'cause': cause, 'oom': bool(oom), 'last_ts': int(last_ts), 'lines': tail}
