
# ---------------------------------------------------------------- сторож

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
        self.acc = {}       # накопление за текущую минуту
        self.ver = int(time.time()) % 100000 * 1000
        self.cmds = {}      # node -> очередь команд
        self.results = {}   # cmd id -> {'ev', 'res'}
        self.expect = {}    # (node, unit) -> до какого времени остановка ожидаема
        self.cool = {}      # (node, unit|mount, type) -> когда было событие
        self.views = {}     # id сторожа-соседа -> {'ts', 'ages'}
        self.bad = {}       # ip -> [счётчик, начало окна]
        self.diag_busy = set()
        self.pollgen = {}   # node -> номер последнего опроса
        for n in self.nodes.values():  # после перезапуска сторожа даём узлам время объявиться
            if n.get('status') in ('up', 'stale'):
                n['last_seen'] = self.started
            for k, v in (('disk_alarm', []), ('backup_alarm', []), ('ssh_known', []), ('login_seen', []), ('flags_seen', {})):
                n.setdefault(k, v)

    # --- события

    def emit(self, typ, sev, node, title, text='', key='node', p=None):
        # key отличает предметы тревог на одном узле: приложение заменяет тревогу отбоем с тем же ключом.
        # p — параметры, по которым приложение собирает текст на языке пользователя; title и text — русский запасной вариант.
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

    # --- сигнал агента

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
            if gd and gd != n.get('gd_seen'):  # агент сам сказал, что уходит на перезагрузку
                n['gd_seen'] = gd
                n['rebooting'] = int(now)
                n['muted_until'] = now + REBOOT_MUTE
                self.emit('reboot', 'info', nid, '%s перезагружается из панели' % name, 'Тревоги по узлу заглушены на 10 минут.', key='reboot', p={'name': name})
            elif not gd and n.get('rebooting'):   # агент снова на связи и не уходит: перезагрузка закончилась или не удалась
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
                if bad == 3:  # три сигнала подряд, чтобы не шуметь при перезапуске
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
        if n.get('rebooting'):   # узел гасит службы сам, перед перезагрузкой: это не беда
            return
        nid, name = n['id'], n['name']
        now = time.time()
        loud = not self.muted(n)   # заглушённый узел пишет события без уведомлений
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

    # --- кто замолчал

    def peer_sees(self, nid):
        """True: сосед видит узел. False: соседи есть и не видят. None: спросить некого."""
        now = time.time()
        answers = []
        for pid, v in self.views.items():
            peer = self.nodes.get(pid)
            if pid == nid or not peer or peer.get('status') != 'up' or now - v['ts'] > PEER_FRESH:
                continue
            age = v['ages'].get(nid)
            # сигналы приходят обоим сторожам почти одновременно: если узел умер, у соседа он тоже давно молчит
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
            lead = min(mates, key=lambda x: x.get('down_ts', 0))   # у него уже есть громкая тревога: общая заменит её
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
                    if self.peer_sees(n['id']) is False:  # второй сторож тоже потерял узел
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

    # --- проверка молчащего узла с соседей

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

    # --- ответы приложению

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
            if since > self.ver:  # сторожа перезапускали, счёт версий начался заново
                return self.state()
            while self.ver <= since:
                left = end - time.time()
                if left <= 0:
                    return {'v': self.ver, 'ts': int(time.time()), 'same': True}
                self.cv.wait(left)
        time.sleep(0.25)  # собрать близкие изменения в один ответ
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
            if after > self.seq:  # сторожа переустановили, счёт начался заново
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

    # --- команды

    def dispatch(self, nid, typ, args, timeout):
        """Отдать команду агенту и дождаться ответа."""
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
                if typ == 'svc.stop':  # остановили сами: «снова работает» потом не нужно
                    n.get('svc_bad', {}).pop(unit, None)
        slow = typ in ('svc.restart', 'svc.start', 'svc.stop', 'svc.suggest', 'backup.suggest', 'maint.check', 'impact', 'diag.probe')
        res = self.dispatch(nid, typ, args, 70 if slow else 28 if not typ.startswith('action.') else 620)
        return res

    def poll(self, nid, wait):
        end = time.time() + wait
        with self.cv:
            # новый опрос от узла вытесняет старый: тот мог остаться от убитого процесса агента и унёс бы команду в никуда
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

    # --- защита от перебора ключа

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

