
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
