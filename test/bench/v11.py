#!/usr/bin/env python3
"""Стенд для возможностей 1.1: Docker, автовыбор служб, действия, игровая консоль, бэкапы,
«кого заденет», перезагрузка по условию, сверка после загрузки, разбор падения, проверка с соседей,
общая беда, обслуживание, входы по SSH, коды ошибок."""
import json, os, socket, struct, subprocess, sys, threading, time

os.environ.setdefault('BENCH_DIR', '/tmp/laputa-v11')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run as B  # noqa: E402
from run import check, api, cmd, node, events, wait_for  # noqa: E402

TARGET = '192.0.2.10'   # адрес сторожа для узлов с той же машины: проверка с соседей стучится сюда
RCON_PORT = 25611
RCON = {'players': ['Alpha', 'Beta'], 'log': []}
EXTRA = {}


def fake_rcon(port, password):
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('127.0.0.1', port))
    srv.listen(8)

    def pkt(rid, typ, body):
        d = struct.pack('<ii', rid, typ) + body.encode() + b'\x00\x00'
        return struct.pack('<i', len(d)) + d

    def serve(c):
        try:
            while True:
                h = c.recv(4)
                if len(h) < 4:
                    return
                (ln,) = struct.unpack('<i', h)
                d = b''
                while len(d) < ln:
                    d += c.recv(ln - len(d))
                rid, typ = struct.unpack('<ii', d[:8])
                body = d[8:-2].decode()
                if typ == 3:
                    c.sendall(pkt(rid if body == password else -1, 2, ''))
                elif body in ('minecraft:list', 'list'):
                    p = RCON['players']
                    c.sendall(pkt(rid, 0, 'There are %d of a max of 20 players online: %s' % (len(p), ', '.join(p))))
                else:
                    RCON['log'].append(body)
                    c.sendall(pkt(rid, 0, '§aDone: ' + body))
        except OSError:
            pass
        finally:
            c.close()

    def loop():
        while True:
            c, _ = srv.accept()
            threading.Thread(target=serve, args=(c,), daemon=True).start()
    threading.Thread(target=loop, daemon=True).start()


def adir(key):
    return os.path.join(B.BASE, 'agent-' + key)


def wj(path, obj):
    json.dump(obj, open(path, 'w'))


def wt(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, 'w').write(text)


def start_agent(key):
    d = adir(key)
    env = dict(os.environ, LAPUTA_ETC=d, PATH=os.path.join(B.HERE, 'bin') + ':' + os.environ['PATH'], FAKE_UNITS=d + '/units.json',
               FAKE_DOCKER=d + '/docker.json', FAKE_SS=d + '/ss.json', FAKE_AWG=d + '/awg.json', FAKE_DEAD=TARGET,
               FAKE_PREV_BOOT=d + '/prev-boot.log', FAKE_SSH=d + '/ssh.log', FAKE_APT=d + '/apt.txt',
               LAPUTA_PROC=d + '/proc', LAPUTA_BOOT_ID=d + '/boot_id', LAPUTA_UPTIME=d + '/uptime', LAPUTA_REBOOT_FILE=d + '/reboot-required',
               LAPUTA_BACKUP_ROOTS=d + '/srv', LAPUTA_CAND_AGE='0', LAPUTA_SUGGEST_EVERY='3', LAPUTA_WARN_LEAD='4', LAPUTA_BOOT_CHECK='3',
               LAPUTA_MAINT_FIRST='1', LAPUTA_REBOOT_HOUR='4', LAPUTA_REBOOT_POLL='1', **EXTRA.get(key, {}))
    B.PROCS['agent-' + key] = subprocess.Popen([B.PY, B.SCRIPT, 'agent'], env=env, stdout=open(d + '/log', 'a'), stderr=subprocess.STDOUT)


def ev(h, typ, nid=None, after=0):
    return [e for e in events(h, typ) if (nid is None or e['node'] == nid) and e['seq'] > after]


def topseq(h):
    return api(h, 'GET', '/v1/events?after=0')['seq']


UNITS_MH = {
    'minecraft.service': {'state': 'active', 'desc': 'Minecraft Server', 'path': '/etc/systemd/system/minecraft.service'},
    'mybot.service': {'state': 'active', 'desc': 'My bot', 'path': '/etc/systemd/system/mybot.service', 'enabled': 'disabled'},
    'cron.service': {'state': 'active', 'desc': 'cron'},
    'awg-quick@awg0.service': {'state': 'active', 'desc': 'AmneziaWG tunnel'},
    'ssh.service': {'state': 'active', 'desc': 'OpenBSD Secure Shell server'},
    '__timers': ['restic-backup.timer', 'apt-daily.timer'],
}
UNITS_PM = {'app.service': {'state': 'active', 'desc': 'App'}, 'db.service': {'state': 'active', 'desc': 'DB'}}
UNITS_PLAIN = {'ssh.service': {'state': 'active', 'desc': 'ssh'}}


def build():
    B.stop_all()
    import shutil
    shutil.rmtree(B.BASE, ignore_errors=True)
    os.makedirs(B.BASE)
    A = B.mk_hub('hub1', 'Hub1', 18753)
    c = json.load(open(A['dir'] + '/etc/hub.json'))
    c['host'] = TARGET
    wj(A['dir'] + '/etc/hub.json', c)
    now = time.time()
    B.mk_agent('meadow', 'Meadow', 'дом', [A], dict(UNITS_MH))
    B.mk_agent('gw', 'Gateway', 'Польша', [A], dict(UNITS_PLAIN))
    B.mk_agent('r1', 'R1', 'дача', [A], dict(UNITS_PLAIN))
    B.mk_agent('r2', 'R2', 'дача', [A], dict(UNITS_PLAIN))
    B.mk_agent('pmnode', 'PMNode', 'Казань', [A], dict(UNITS_PM))
    for k in ('meadow', 'gw', 'r1', 'r2', 'pmnode'):
        d = adir(k)
        wt(d + '/boot_id', 'boot-1-' + k)
        wt(d + '/uptime', '100000.00 1.00')
    d = adir('meadow')
    wj(d + '/docker.json', {'jellyfin': {'status': 'running', 'image': 'jellyfin/jellyfin:latest', 'ports': '0.0.0.0:8096->8096/tcp, :::8096->8096/tcp', 'started': now - 7200},
                            'old': {'status': 'exited', 'image': 'busybox', 'code': 1, 'finished': now - 600}})
    wt(d + '/proc/101/cgroup', '0::/system.slice/minecraft.service\n')
    wt(d + '/proc/102/cgroup', '0::/system.slice/ssh.service\n')
    wj(d + '/ss.json', {'listen': [{'port': 25565, 'pid': 101}, {'port': 2222, 'pid': 102}],
                        'est': [{'lport': 25565, 'peer': '203.0.113.7'}, {'lport': 25565, 'peer': '127.0.0.1'}, {'lport': 8096, 'peer': '198.51.100.2'}]})
    wj(d + '/awg.json', [{'iface': 'awg0', 'age': 30}, {'iface': 'awg0', 'age': 4000}])
    wt(d + '/apt.txt', 'Inst libssl3 [3.0.11-1] (3.0.15-1~deb12u1 Debian-Security:12/stable-security [amd64])\n'
                       'Inst curl [7.88.1-10] (7.88.1-10+deb12u8 Debian:12.8/stable [amd64])\nConf curl (7.88.1-10+deb12u8 Debian:12.8/stable [amd64])\n')
    wt(d + '/reboot-required', '*** System restart required ***\n')
    wt(d + '/reboot-required.pkgs', 'linux-image-6.1.0-27-amd64\nlibssl3\n')
    wt(d + '/ssh.log', '')
    # бэкапы: раз в 10 минут, последний 35 минут назад — опаздывают
    bk = d + '/srv/game/backups'
    os.makedirs(bk)
    for i in range(6):
        p = '%s/world-%d.tar.gz' % (bk, i)
        wt(p, 'x' * (100 + i))
        t = now - 2100 - i * 600
        os.utime(p, (t, t))
    sb = d + '/srv/site/dumps'
    os.makedirs(sb)
    for i in range(4):
        p = '%s/db-%d.sql.gz' % (sb, i)
        wt(p, 'y')
        t = now - 3600 - i * 86400
        os.utime(p, (t, t))
    os.makedirs(d + '/srv/site/htdocs')
    wt(d + '/actions.ini', '[Обновить бота]\ncommand = echo updated; exit 0\nservice = mybot.service\nconfirm = no ; без вопросов\n\n'
                           '[Сломанное]\ncommand = echo fail >&2; exit 3\n\n[Пустое]\nservice = cron.service\n')
    # gw: настройки 1.0 с блоком mc
    g = json.load(open(adir('gw') + '/agent.json'))
    g['mc'] = {'host': '127.0.0.1', 'port': RCON_PORT, 'password': 'secret', 'backups': adir('gw') + '/mcb'}
    os.makedirs(adir('gw') + '/mcb')
    wj(adir('gw') + '/agent.json', g)
    B.start_hub(A)
    time.sleep(1.2)
    for k in ('meadow', 'gw', 'r1', 'r2', 'pmnode'):
        start_agent(k)
    return A


def scenario():
    fake_rcon(RCON_PORT, 'secret')
    A = build()
    print('\n1. Запуск')
    check('сторож видит 5 узлов', wait_for(lambda: len(api(A, 'GET', '/v1/state')['nodes']) == 5 and all(n['status'] == 'up' for n in api(A, 'GET', '/v1/state')['nodes']), 20))
    check('версия сторожа в состоянии', api(A, 'GET', '/v1/state')['hub'].get('version') == '1.2.0')
    n = node(A, 'meadow')
    check('адрес узла с той же машины берётся из настроек сторожа', n['addr'] == TARGET, n.get('addr'))
    check('порт SSH определён по тому, что слушает служба ssh', n['m'].get('ssh_port') == 2222, n['m'].get('ssh_port'))

    print('\n2. Перенос настроек 1.0')
    g = wait_for(lambda: node(A, 'gw')['game'], 10)
    check('блок mc стал игровой консолью Minecraft с игроками', g and g['type'] == 'minecraft' and g['players'] == ['Alpha', 'Beta'] and 'password' not in json.dumps(g), g)
    check('для старых приложений mc по-прежнему отдаётся', node(A, 'gw')['mc'] and node(A, 'gw')['mc']['players'] == ['Alpha', 'Beta'])
    bl = node(A, 'gw')['backups']
    check('папка бэкапов из mc перешла в общий список', bl and bl[0]['path'].endswith('/mcb') and bl[0]['label'] == 'Minecraft', bl)
    cfg = json.load(open(adir('gw') + '/agent.json'))
    check('в файле настроек блока mc больше нет', 'mc' not in cfg and cfg['game']['type'] == 'minecraft')
    check('переход на общий RCON', cmd(A, 'gw', 'game.setup', type='rcon', host='127.0.0.1', port=RCON_PORT).get('ok'))
    g = wait_for(lambda: (lambda x: x if x and x['type'] == 'rcon' else None)(node(A, 'gw')['game']), 10)
    check('общий RCON: в сети, без разбора игроков, старый пароль сохранён', g and g['online'] and g['players'] is None and node(A, 'gw')['mc'] is None, g)

    # три узла замолкают сейчас, проверим их позже
    pm_state = adir('pmnode') + '/agent-state.json'
    check('добавление служб на узле для сверки', cmd(A, 'pmnode', 'svc.add_many', items=[{'unit': 'app.service', 'label': 'App'}, {'unit': 'db.service', 'label': 'База'}]).get('added') == 2)
    check('агент помнит, какие службы работали', wait_for(lambda: json.load(open(pm_state))['active'] == ['app.service', 'db.service'], 15))
    seq_down = topseq(A)
    for k in ('r1', 'r2', 'pmnode'):
        B.stop('agent-' + k)
    t_down = time.time()

    print('\n3. Docker')
    r = cmd(A, 'meadow', 'svc.discover')
    names = {u['unit']: u for u in r.get('units', [])}
    check('контейнеры в списке служб', 'docker:jellyfin' in names and names['docker:jellyfin']['state'] == 'active' and names['docker:jellyfin']['ports'] == [8096]
          and names.get('docker:old', {}).get('state') == 'failed', list(names))
    check('контейнер добавляется как служба', cmd(A, 'meadow', 'svc.add', unit='docker:jellyfin', label='', kind='media').get('ok'))
    s = wait_for(lambda: next((x for x in node(A, 'meadow')['services'] if x['unit'] == 'docker:jellyfin'), None), 10)
    check('у контейнера состояние, время запуска и имя по умолчанию', s and s['state'] == 'active' and s['label'] == 'jellyfin' and abs(s['since'] - (time.time() - 7200)) < 30, s)
    check('перезапуск контейнера', cmd(A, 'meadow', 'svc.restart', unit='docker:jellyfin').get('ok'))
    check('журнал контейнера', 'контейнер jellyfin' in cmd(A, 'meadow', 'svc.logs', unit='docker:jellyfin').get('output', ''))
    seq = topseq(A)
    time.sleep(2)
    dj = json.load(open(adir('meadow') + '/docker.json'))
    dj['jellyfin'].update(status='exited', code=137, finished=time.time())
    wj(adir('meadow') + '/docker.json', dj)
    e = wait_for(lambda: ev(A, 'svc_failed', 'meadow', seq), 15)
    check('упавший контейнер даёт тревогу', e and e[-1]['key'] == 'svc:docker:jellyfin' and e[-1]['p']['label'] == 'jellyfin', e)
    dj['jellyfin'].update(status='running', code=0, started=time.time())
    wj(adir('meadow') + '/docker.json', dj)
    check('и отбой, когда он снова работает', wait_for(lambda: ev(A, 'svc_ok', 'meadow', seq), 15))

    print('\n4. Автовыбор служб')
    r = cmd(A, 'meadow', 'svc.suggest')
    u = {x['unit']: x for x in r.get('units', [])}
    mc = u.get('minecraft.service', {})
    check('у кандидата причины: порт, ручной юнит, известная, автозапуск', mc.get('recommended') and mc['reasons'][:1] == ['port'] and {'manual', 'known', 'autostart'} <= set(mc['reasons'])
          and mc['ports'] == [25565], mc)
    check('свой бот без автозапуска рекомендован за ручной юнит', u.get('mybot.service', {}).get('recommended') and 'autostart' not in u['mybot.service']['reasons'], u.get('mybot.service'))
    check('cron не рекомендован', u.get('cron.service') and not u['cron.service']['recommended'], u.get('cron.service'))
    check('контейнер уже в панели помечен', u.get('docker:jellyfin', {}).get('monitored') is True)
    r = cmd(A, 'meadow', 'svc.add_many', items=[{'unit': 'minecraft.service', 'label': 'Minecraft', 'kind': 'game'}, {'unit': 'mybot.service', 'label': 'Бот', 'kind': 'bot'},
                                                {'unit': 'nope.service'}, {'unit': 'bad name'}])
    check('пакетное добавление с ошибками по каждой', r.get('added') == 2 and [x['code'] for x in r['errors']] == ['svc_missing', 'svc_name'], r)
    c = wait_for(lambda: (lambda x: x if any(y['unit'] == 'awg-quick@awg0.service' for y in x) else None)(node(A, 'meadow')['candidates'] or []), 25)
    check('тихие подсказки: новая служба, которой нет в панели', c and not any(y['unit'] in ('minecraft.service', 'mybot.service', 'cron.service') for y in c), c)
    check('отказ от подсказки', cmd(A, 'meadow', 'svc.dismiss', unit='awg-quick@awg0.service').get('ok')
          and wait_for(lambda: not any(y['unit'] == 'awg-quick@awg0.service' for y in node(A, 'meadow')['candidates'] or []), 10))
    time.sleep(4)
    check('после отказа подсказка не возвращается', not any(y['unit'] == 'awg-quick@awg0.service' for y in node(A, 'meadow')['candidates'] or []))

    print('\n5. Действия узла')
    acts = wait_for(lambda: node(A, 'meadow')['actions'], 10) or []
    check('действия из actions.ini видны, пустое пропущено', [a['name'] for a in acts] == ['Обновить бота', 'Сломанное'] and acts[0]['service'] == 'mybot.service'
          and acts[0]['confirm'] is False and acts[1]['confirm'] is True and 'user' not in acts[0], acts)
    r = cmd(A, 'meadow', 'action.run', id=acts[0]['id']) if acts else {}
    check('действие выполняется', r.get('ok') and r.get('output', '').strip() == 'updated', r)
    r = cmd(A, 'meadow', 'action.run', id=acts[1]['id']) if len(acts) > 1 else {}
    check('ошибка действия: код и вывод', not r.get('ok') and r.get('code') == 'action_failed' and r['p']['rc'] == 3 and 'fail' in r.get('output', ''), r)
    check('чужое действие не выполняется', cmd(A, 'meadow', 'action.run', id='0000000000').get('code') == 'action_missing')
    with open(adir('meadow') + '/actions.ini', 'a') as f:
        f.write('\n[Новое]\ncommand = true\n')
    check('правка файла подхватывается без перезапуска', wait_for(lambda: len(node(A, 'meadow')['actions']) == 3, 10))

    print('\n6. Игровая консоль')
    check('подключение Minecraft', cmd(A, 'meadow', 'game.setup', type='minecraft', host='127.0.0.1', port=RCON_PORT, password='secret').get('ok'))
    check('быстрая кнопка', cmd(A, 'meadow', 'game.quick', op='add', label='Утро', command='/time set day').get('ok')
          and cmd(A, 'meadow', 'game.quick', op='add', label='', command='weather clear').get('ok'))
    g = wait_for(lambda: (lambda x: x if x and len(x.get('quick', [])) == 2 else None)(node(A, 'meadow')['game']), 10)
    check('кнопки хранятся на узле', g and g['quick'] == [{'label': 'Утро', 'command': '/time set day'}, {'label': 'weather clear', 'command': 'weather clear'}], g)
    check('удаление кнопки', cmd(A, 'meadow', 'game.quick', op='remove', index=1).get('ok') and len(json.load(open(adir('meadow') + '/agent.json'))['game']['quick']) == 1)
    r = cmd(A, 'meadow', 'game.cmd', command='/time set day')
    check('команда Minecraft без косой черты', r.get('ok') and r['output'] == 'Done: time set day' and 'time set day' in RCON['log'], r)
    check('пустая кнопка отклонена', cmd(A, 'meadow', 'game.quick', op='add', label='x', command=' ').get('code') == 'empty_cmd')

    print('\n7. Бэкапы')
    bk = adir('meadow') + '/srv/game/backups'
    check('добавление папки бэкапов', cmd(A, 'meadow', 'backup.add', path=bk, label='Мир').get('ok'))
    b = wait_for(lambda: next((x for x in node(A, 'meadow')['backups'] if x['path'] == bk), None), 10)
    check('интервал выучен по промежуткам', b and b['every'] == 600 and b['count'] == 6 and b['label'] == 'Мир', b)
    e = wait_for(lambda: ev(A, 'backup_late', 'meadow'), 10)
    check('опоздание бэкапа', e and e[-1]['p']['path'] == bk and e[-1]['p']['every'] == 600 and e[-1]['key'] == 'backup:' + bk, e)
    check('опоздание в списке тревог', any(p['type'] == 'backup_late' for p in api(A, 'GET', '/v1/events?after=0')['problems']))
    wt(bk + '/world-new.tar.gz', 'z')
    check('новый бэкап снимает тревогу', wait_for(lambda: ev(A, 'backup_ok', 'meadow'), 25))
    r = cmd(A, 'meadow', 'backup.suggest')
    check('поиск папок с бэкапами', r.get('ok') and [x['path'] for x in r['dirs']] == [adir('meadow') + '/srv/site/dumps'] and r['dirs'][0]['reason'] == 'name', r.get('dirs'))
    check('подсказка из таймеров', 'timer: restic-backup.timer' in r.get('hints', []) and not any('apt-daily' in h for h in r.get('hints', [])), r.get('hints'))
    check('несуществующая папка', cmd(A, 'meadow', 'backup.add', path='/nope/x').get('code') == 'folder_missing')
    check('повтор отклонён', cmd(A, 'meadow', 'backup.add', path=bk).get('code') == 'backup_exists')

    print('\n8. Обслуживание и входы')
    mt = wait_for(lambda: node(A, 'meadow')['maint'], 10)
    check('обновления apt и флаг перезагрузки', mt and mt['pm'] == 'apt' and mt['updates'] == 2 and mt['security'] == 1 and mt['reboot'] and mt['why'] == 'file'
          and mt['pkgs'] == ['libssl3', 'linux-image-6.1.0-27-amd64'], mt)
    check('событие «просит перезагрузку» тихое и одно', len(ev(A, 'maint_reboot', 'meadow')) == 1 and ev(A, 'maint_reboot', 'meadow')[0]['sev'] == 'info')
    os.remove(adir('meadow') + '/reboot-required')
    r = cmd(A, 'meadow', 'maint.check')
    check('ручная проверка', r.get('ok') and r['maint']['reboot'] in (False, True) and r['maint']['why'] != 'file', r)
    with open(adir('meadow') + '/ssh.log', 'a') as f:
        f.write('%.6f meadow sshd[812]: Accepted publickey for root from 203.0.113.50 port 51122 ssh2: ED25519 SHA256:abc\n' % time.time())
    e = wait_for(lambda: ev(A, 'ssh_login', 'meadow'), 15)
    check('вход с нового адреса — предупреждение', e and e[-1]['sev'] == 'warn' and e[-1]['p'] == {'name': 'Meadow', 'user': 'root', 'ip': '203.0.113.50', 'method': 'publickey', 'new': True}, e)
    time.sleep(1)
    with open(adir('meadow') + '/ssh.log', 'a') as f:
        f.write('%.6f meadow sshd[813]: Accepted password for root from 203.0.113.50 port 51123 ssh2\n' % time.time())
    e = wait_for(lambda: (lambda x: x if len(x) == 2 else None)(ev(A, 'ssh_login', 'meadow')), 15)
    check('повторный вход с того же адреса без звука', e and e[-1]['sev'] == 'info' and e[-1]['p']['new'] is False, e)

    print('\n9. Кого заденет')
    r = cmd(A, 'meadow', 'impact', unit='minecraft.service').get('impact') or {}
    check('для игровой службы: игроки и внешние соединения на её порт', r.get('players') == ['Alpha', 'Beta'] and r['conns'] == [{'unit': 'minecraft.service', 'label': 'Minecraft', 'ports': [25565], 'n': 1}]
          and r['tunnels'] == [] and r['total'] == 3, r)
    r = cmd(A, 'meadow', 'impact', unit='mybot.service').get('impact') or {}
    check('у бота без портов никого', r.get('total') == 0 and r.get('players') is None, r)
    r = cmd(A, 'meadow', 'impact').get('impact') or {}
    check('для всего узла: игроки, соединения, контейнер и туннель', r.get('total') == 5 and r['tunnels'] == [{'iface': 'awg0', 'n': 1}]
          and any(c['unit'] == 'docker:jellyfin' and c['n'] == 1 for c in r['conns']), r)

    print('\n10. Общая беда и проверка с соседей')
    e = wait_for(lambda: ev(A, 'group_down', after=seq_down), 30)
    check('два узла одного места: одна общая тревога', e and len(e) == 1 and sorted(e[0]['p']['names']) == ['R1', 'R2'] and e[0]['p']['place'] == 'дача' and e[0]['sev'] == 'crit', e)
    nd = [x for x in ev(A, 'node_down', after=seq_down) if x['node'] in ('r1', 'r2')]
    check('второй узел группы без отдельного звука', len(nd) == 2 and sorted(x['sev'] for x in nd) == ['crit', 'info'], nd)
    check('ключ общей тревоги совпадает с тревогой первого узла', e and any(x['node'] == e[0]['node'] and x['key'] == e[0]['key'] and x['sev'] == 'crit' for x in nd))
    dg = wait_for(lambda: (lambda d: d if d and not d['running'] and d['results'] else None)(node(A, 'r1').get('diag')), 60)
    check('соседи проверили молчащий узел', dg and dg['ip'] == TARGET and dg['port'] == 22 and {x['from'] for x in dg['results']} <= {'Meadow', 'Gateway', 'PMNode'}, dg)
    r0 = (dg or {'results': [{}]})['results'][0]
    check('пинг, порт SSH и трассировка в отчёте', r0.get('ok') and r0['ping'] == {'sent': 3, 'recv': 0, 'avg': None} and r0['tcp']['ok'] is False
          and r0['trace']['hops'][:2] == [{'n': 1, 'ip': '10.0.0.1', 'ms': 0.412}, {'n': 2, 'ip': '198.51.100.1', 'ms': 5.12}] and '100% packet loss' in r0['raw'], r0)
    check('проверку можно запустить вручную', api(A, 'POST', '/v1/diag', {'node': 'r2'}).get('ok'))
    check('локальный адрес проверять нельзя', cmd(A, 'gw', 'diag.probe', ip='127.0.0.1', port=22).get('code') == 'bad_ip')
    check('и мусор вместо адреса тоже', cmd(A, 'gw', 'diag.probe', ip='1.2.3.4; reboot', port=22).get('code') == 'bad_ip')
    check('команда молчащему узлу: код node_silent', cmd(A, 'r1', 'svc.discover').get('code') == 'node_silent')
    check('команда неизвестному узлу: код hub_no_node', cmd(A, 'zzz', 'svc.discover').get('code') == 'hub_no_node')

    print('\n11. Вернулся: связь или перезагрузка')
    check('PMNode замолчал', wait_for(lambda: ev(A, 'node_down', 'pmnode', seq_down), 20))
    start_agent('pmnode')
    e = wait_for(lambda: ev(A, 'node_up', 'pmnode'), 20)
    check('аптайм не сбросился: пропадала связь', e and e[-1]['p']['why'] == 'net' and e[-1]['p']['cause'] == '', e)
    B.stop('agent-pmnode')
    seq = topseq(A)
    check('PMNode снова замолчал', wait_for(lambda: ev(A, 'node_down', 'pmnode', seq), 30))
    d = adir('pmnode')
    t = int(time.time()) - 60
    wt(d + '/prev-boot.log', ''.join('%d.%06d pmnode %s\n' % (t + i, 0, m) for i, m in enumerate([
        'app[311]: tick', 'kernel: java invoked oom-killer: gfp_mask=0x140cca', 'kernel: Out of memory: Killed process 1234 (java) total-vm:4GB',
        'kernel: oom-kill:constraint=CONSTRAINT_NONE', 'app[311]: last words'])))
    wt(d + '/boot_id', 'boot-2-pmnode')
    wt(d + '/uptime', '25.00 1.00')
    u = json.load(open(d + '/units.json'))
    u['db.service']['state'] = 'inactive'
    wj(d + '/units.json', u)
    start_agent('pmnode')
    e = wait_for(lambda: ev(A, 'node_up', 'pmnode', seq), 20)
    check('перезагрузился сам: причина из журнала прошлой загрузки', e and e[-1]['p']['why'] == 'boot' and e[-1]['p']['cause'] == 'oom' and 'память' in e[-1]['text'], e)
    pm = node(A, 'pmnode').get('pm') or {}
    check('разбор падения: причина, время смерти, хвост журнала', pm.get('cause') == 'oom' and pm.get('oom') is True and pm.get('last_ts') == t + 4 and pm.get('down_at')
          and pm['lines'][-1] == [t + 4, 'app[311]: last words'] and len(pm['lines']) == 5, pm)
    e = wait_for(lambda: ev(A, 'boot_missing', 'pmnode'), 20)
    check('после загрузки не поднялась служба, что работала до неё', e and e[-1]['p']['labels'] == ['База'] and e[-1]['sev'] == 'crit', e)
    check('в карточке узла видно, что не поднялось', node(A, 'pmnode')['boot_missing'] == ['db.service'])
    u['db.service']['state'] = 'active'
    wj(d + '/units.json', u)
    check('когда служба поднялась, отметка уходит', wait_for(lambda: node(A, 'pmnode')['boot_missing'] == [], 15))
    now = time.time()
    h = api(A, 'GET', '/v1/history?node=meadow&from=%d&to=%d' % (now - 1800, now))
    check('история за 30 минут с шагом в минуту и диском', h['step'] == 60 and len(h['cpu']) == 31 and len(h['disk']) == 31 and any(x is not None for x in h['disk']), h)

    print('\n12. Перезагрузка по условию')
    seq = topseq(A)
    r = cmd(A, 'meadow', 'node.reboot', mode='empty', hours=3)
    check('«когда опустеет» принято', r.get('ok') and r.get('pending') == 'empty', r)
    p = wait_for(lambda: (lambda x: x if x and x.get('left') else None)(node(A, 'meadow')['pending']), 10)
    check('узел ждёт и считает, кто на нём', p and p['mode'] == 'empty' and p['left'] == 5 and p['hours'] == 3, p)
    check('вторую перезагрузку не поставить', cmd(A, 'meadow', 'node.reboot', mode='now').get('code') == 'reboot_pending')
    check('отмена', cmd(A, 'meadow', 'node.reboot_cancel').get('ok') and wait_for(lambda: node(A, 'meadow')['pending'] is None, 10))
    check('отменять нечего', cmd(A, 'meadow', 'node.reboot_cancel').get('code') == 'nothing_pending')
    check('без перезагрузки', not os.path.exists(adir('meadow') + '/units.json.reboot'))
    cmd(A, 'meadow', 'node.reboot', mode='empty', hours=1)
    e = wait_for(lambda: ev(A, 'reboot_gaveup', 'meadow', seq), 20)
    check('не опустел за отведённое время: отказ с предупреждением', e and e[-1]['p']['hours'] == 1 and e[-1]['sev'] == 'warn', e)
    check('ожидание снято', wait_for(lambda: node(A, 'meadow')['pending'] is None, 10))
    RCON['log'][:] = []
    r = cmd(A, 'meadow', 'node.reboot', mode='warn', say5='Через 5 минут перезагрузка', say1='Через минуту')
    check('«с предупреждением» принято', r.get('ok') and r.get('pending') == 'warn')
    p = wait_for(lambda: node(A, 'meadow')['pending'], 5)
    check('время перезагрузки известно заранее', p and p['mode'] == 'warn' and p['at'] > time.time(), p)
    check('агент перезагрузил узел', wait_for(lambda: os.path.exists(adir('meadow') + '/units.json.reboot'), 15))
    check('игроков предупредили дважды и сохранили мир', RCON['log'] == ['say Через 5 минут перезагрузка', 'say Через минуту', 'save-all flush'], RCON['log'])
    e = wait_for(lambda: ev(A, 'reboot', 'meadow', seq), 10)
    check('сторож узнал о перезагрузке от самого агента и заглушил узел', e and node(A, 'meadow')['muted_until'] > time.time(), e)
    check('узел помечен как перезагружающийся', node(A, 'meadow').get('rebooting'))
    uu = json.load(open(adir('meadow') + '/units.json'))
    uu['minecraft.service']['state'] = 'inactive'
    wj(adir('meadow') + '/units.json', uu)
    time.sleep(5)
    check('службы, погашенные перед перезагрузкой, не дают тревог', not ev(A, 'svc_stopped', 'meadow', seq) and not ev(A, 'svc_failed', 'meadow', seq))
    uu['minecraft.service']['state'] = 'active'
    wj(adir('meadow') + '/units.json', uu)
    B.stop('agent-meadow')
    os.remove(adir('meadow') + '/units.json.reboot')
    RCON['players'] = []
    dd = adir('meadow')
    wj(dd + '/ss.json', {'listen': [{'port': 25565, 'pid': 101}], 'est': []})
    wj(dd + '/awg.json', [])
    start_agent('meadow')
    check('после перезапуска агента узел в строю и уже не перезагружается', wait_for(lambda: node(A, 'meadow')['status'] == 'up' and node(A, 'meadow')['pending'] is None and not node(A, 'meadow')['rebooting'], 20))
    RCON['log'][:] = []
    check('пустой узел перезагружается сразу', cmd(A, 'meadow', 'node.reboot', mode='empty', hours=2).get('ok')
          and wait_for(lambda: os.path.exists(dd + '/units.json.reboot'), 15))
    check('перед перезагрузкой мир сохранён', RCON['log'] == ['save-all flush'], RCON['log'])

    print('\n13. Коды ошибок')
    for typ, args, code in (('svc.add', {'unit': 'a; rm -rf /'}, 'svc_name'), ('svc.restart', {'unit': 'cron.service'}, 'svc_not_listed'),
                            ('nope.cmd', {}, 'unknown_cmd'), ('svc.edit', {'unit': 'mybot.service', 'link': 'javascript:alert(1)'}, 'link_scheme')):
        r = cmd(A, 'gw' if typ != 'svc.edit' else 'meadow', typ, **args)
        check('код %s' % code, r.get('ok') is False and r.get('code') == code and r.get('error'), r)
    r = cmd(A, 'r2', 'svc.discover')
    check('у ошибки есть параметры для перевода', r.get('p') == {'name': 'R2'}, r)
    B.stop('agent-gw')
    g = json.load(open(adir('gw') + '/agent.json'))
    g.pop('game', None)
    wj(adir('gw') + '/agent.json', g)
    start_agent('gw')
    wait_for(lambda: node(A, 'gw')['status'] == 'up' and node(A, 'gw')['game'] is None, 20)
    r = cmd(A, 'gw', 'game.cmd', command='list')
    check('консоль без игры: game_off; команда не теряется после перезапуска агента', r.get('code') == 'game_off', r)
    check('RCON не отвечает: rcon_down', cmd(A, 'gw', 'game.setup', type='rcon', host='127.0.0.1', port=1, password='x').get('code') == 'rcon_down')
    check('неверный пароль: rcon_auth', cmd(A, 'gw', 'game.setup', type='rcon', host='127.0.0.1', port=RCON_PORT, password='bad').get('code') == 'rcon_auth')


def unit_tests():
    print('\n14. Разбор без сети')
    import laputa as L
    check('порты Docker', L.docker_ports('0.0.0.0:8096->8096/tcp, :::8096->8096/tcp, 0.0.0.0:53->53/udp') == [8096, 53])
    check('время Docker', L.parse_iso('2026-10-08T12:00:00.123456789Z') == 1791460800 and L.parse_iso('0001-01-01T00:00:00Z') is None
          and L.parse_iso('2026-10-08T15:00:00+03:00') == 1791460800)
    check('сравнение версий ядра', L._ver_key('6.1.0-27-amd64') > L._ver_key('6.1.0-25-amd64') and L._ver_key('6.12.1') > L._ver_key('6.9.12'))


def lang_tests():
    print('\n15. Язык сообщений сервера')
    import tempfile
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'server', 'laputa.py')

    def say(etc_cfg, **env):
        d = tempfile.mkdtemp()
        if etc_cfg is not None:
            wj(d + '/agent.json', etc_cfg)
        e = {k: v for k, v in os.environ.items() if not k.startswith(('LANG', 'LC_', 'LAPUTA_'))}
        e.update(LAPUTA_ETC=d, **env)
        r = subprocess.run([sys.executable, src, 'status'], env=e, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return r.stdout.decode('utf-8')
    check('LAPUTA_LANG=en: по-английски', 'Hubs:' in say({'name': 'x', 'hubs': []}, LAPUTA_LANG='en'))
    check('новая установка, локаль ru_RU: по-русски', 'не установлена' in say(None, LANG='ru_RU.UTF-8'))
    check('новая установка, локаль en_US: по-английски', 'not installed' in say(None, LANG='en_US.UTF-8'))
    check('установка до 1.2 (без lang) остаётся русской при любой локали', 'Сторожа:' in say({'name': 'x', 'hubs': []}, LANG='en_US.UTF-8'))
    check('выбранный при установке английский держится при русской локали', 'Hubs:' in say({'name': 'x', 'hubs': [], 'lang': 'en'}, LANG='ru_RU.UTF-8'))


if __name__ == '__main__':
    try:
        scenario()
        unit_tests()
        lang_tests()
    finally:
        B.stop_all()
    bad = [n for n, ok in B.RESULTS if not ok]
    print('\nИтог: %d проверок, не прошло %d' % (len(B.RESULTS), len(bad)))
    for n in bad:
        print('  НЕ ПРОШЛО:', n)
    sys.exit(1 if bad else 0)
