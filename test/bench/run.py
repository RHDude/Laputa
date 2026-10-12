#!/usr/bin/env python3
"""Laputa test bench: two watchmen and five agents on one machine, stubbed systemd and RCON.

  run.py test    run the scenario with checks
  run.py serve   start the bench and keep it running (for UI tests)
"""
import json, os, shutil, socket, struct, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, 'server'))
os.environ.setdefault('LAPUTA_SPEED', '5')
os.environ.setdefault('LAPUTA_INIT', 'systemd')
import laputa  # noqa: E402

BASE = os.environ.get('BENCH_DIR', '/tmp/laputa-bench')
PY = sys.executable
SCRIPT = os.path.join(ROOT, 'server', 'laputa.py')
PROCS = {}
RESULTS = []


def check(name, cond, extra=''):
    RESULTS.append((name, bool(cond)))
    print(('  PASS  ' if cond else '  FAIL  ') + name + (('   ' + str(extra)) if extra and not cond else ''), flush=True)


def mk_hub(key, name, port):
    d = os.path.join(BASE, key)
    os.makedirs(d + '/etc', exist_ok=True)
    os.makedirs(d + '/var', exist_ok=True)
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:prime256v1', '-nodes', '-days', '30', '-subj', '/CN=' + key,
                    '-keyout', d + '/etc/tls.key', '-out', d + '/etc/tls.crt'], check=True, capture_output=True)
    cfg = {'id': key, 'name': name, 'port': port, 'host': '127.0.0.1', 'app_token': 'app-' + key + '-secret', 'agent_token': 'agent-' + key + '-secret', 'cors': True, 'bind': '127.0.0.1'}
    json.dump(cfg, open(d + '/etc/hub.json', 'w'))
    import hashlib, ssl
    pin = hashlib.sha256(ssl.PEM_cert_to_DER_cert(open(d + '/etc/tls.crt').read())).hexdigest()
    return {'dir': d, 'cfg': cfg, 'pin': pin, 'url': 'https://127.0.0.1:%d' % port,
            'app': {'url': 'https://127.0.0.1:%d' % port, 'pin': pin, 'token': cfg['app_token']},
            'agent': {'url': 'https://127.0.0.1:%d' % port, 'pin': pin, 'token': cfg['agent_token']}}


def start_hub(h):
    env = dict(os.environ, LAPUTA_ETC=h['dir'] + '/etc', LAPUTA_VAR=h['dir'] + '/var')
    PROCS['hub-' + h['cfg']['id']] = subprocess.Popen([PY, SCRIPT, 'hub'], env=env, stdout=open(h['dir'] + '/log', 'a'), stderr=subprocess.STDOUT)


def mk_agent(key, name, place, hubs, units, is_hub=False, local=None):
    d = os.path.join(BASE, 'agent-' + key)
    os.makedirs(d, exist_ok=True)
    hl = []
    for h in hubs:
        e = dict(h['agent'])
        if local is h:
            e['local'] = True
        hl.append(e)
    cfg = {'id': key, 'name': name, 'place': place, 'hubs': hl, 'services': []}
    if is_hub:
        cfg['hub'] = True
    json.dump(cfg, open(d + '/agent.json', 'w'))
    json.dump(units, open(d + '/units.json', 'w'))
    return d


def start_agent(key):
    d = os.path.join(BASE, 'agent-' + key)
    env = dict(os.environ, LAPUTA_ETC=d, PATH=os.path.join(HERE, 'bin') + ':' + os.environ['PATH'], FAKE_UNITS=d + '/units.json')
    PROCS['agent-' + key] = subprocess.Popen([PY, SCRIPT, 'agent'], env=env, stdout=open(d + '/log', 'a'), stderr=subprocess.STDOUT)


def stop(name):
    p = PROCS.pop(name, None)
    if p:
        p.terminate()
        try:
            p.wait(5)
        except subprocess.TimeoutExpired:
            p.kill()


def stop_all():
    for n in list(PROCS):
        stop(n)


def api(h, method, path, body=None, timeout=40):
    return laputa.hub_call(h['app'], method, path, body, timeout=timeout)


def cmd(h, node, typ, **args):
    return api(h, 'POST', '/v1/cmd', {'node': node, 'type': typ, 'args': args}, timeout=90)


def node(h, nid):
    return next((n for n in api(h, 'GET', '/v1/state')['nodes'] if n['id'] == nid), None)


def events(h, typ=None):
    ev = api(h, 'GET', '/v1/state')['events']
    return [e for e in ev if typ is None or e['type'] == typ]


def wait_for(fn, timeout=30, step=0.5):
    end = time.time() + timeout
    while time.time() < end:
        try:
            v = fn()
            if v:
                return v
        except Exception:
            pass
        time.sleep(step)
    return None


def set_unit(key, unit, **kw):
    p = os.path.join(BASE, 'agent-' + key, 'units.json')
    u = json.load(open(p))
    u[unit].update(kw)
    u[unit]['since'] = time.time()
    json.dump(u, open(p, 'w'))


def fake_rcon(port, password):
    """Minimal Minecraft RCON server."""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('127.0.0.1', port))
    srv.listen(5)

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
                elif body == 'minecraft:list':
                    c.sendall(pkt(rid, 0, 'There are 2 of a max of 20 players online: Alpha, Beta'))
                else:
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


UNITS_A = {'x-ui.service': {'state': 'active', 'desc': '3x-ui Service', 'mem': 96000000}, 'milo-bot.service': {'state': 'active', 'desc': 'Milo Bot bot', 'mem': 312000000},
           'mike-bot.service': {'state': 'active', 'desc': 'MikeModBot'}, 'ssh.service': {'state': 'active', 'desc': 'OpenBSD Secure Shell server'}}
UNITS_M = {'minecraft.service': {'state': 'active', 'desc': 'Minecraft Server', 'mem': 4600000000}, 'jellyfin.service': {'state': 'active', 'desc': 'Jellyfin Media Server'},
           'awg-quick@awg0.service': {'state': 'active', 'desc': 'AmneziaWG tunnel'}, 'cron.service': {'state': 'active', 'desc': 'cron'}}
UNITS_X = {'docker.service': {'state': 'active', 'desc': 'Docker'}, 'ssh.service': {'state': 'active', 'desc': 'ssh'}}


def build():
    stop_all()
    shutil.rmtree(BASE, ignore_errors=True)
    os.makedirs(BASE)
    A = mk_hub('summit', 'Summit', 18743)
    B = mk_hub('aurora', 'Aurora', 18744)
    mk_agent('summit', 'Summit', 'Казань', [A, B], dict(UNITS_X), is_hub=True, local=A)
    mk_agent('aurora', 'Aurora', 'Польша', [B, A], dict(UNITS_A), is_hub=True, local=B)
    mk_agent('meadow', 'Meadow', 'дом', [A, B], dict(UNITS_M))
    mk_agent('ridge', 'Ridge', 'дом', [A, B], dict(UNITS_X))
    mk_agent('cobalt', 'Cobalt', 'Чехия', [A, B], dict(UNITS_A))
    start_hub(A)
    start_hub(B)
    time.sleep(1.2)
    for k in ('summit', 'aurora', 'meadow', 'ridge', 'cobalt'):
        start_agent(k)
    return A, B


def scenario():
    A, B = build()
    fake_rcon(25599, 'secret')
    print('\n1. Запуск')
    st = wait_for(lambda: (lambda s: s if len(s['nodes']) == 5 and all(n['status'] == 'up' for n in s['nodes']) else None)(api(A, 'GET', '/v1/state')), 20)
    check('сторож A видит 5 узлов в строю', st)
    stb = wait_for(lambda: (lambda s: s if len(s['nodes']) == 5 and all(n['status'] == 'up' for n in s['nodes']) else None)(api(B, 'GET', '/v1/state')), 10)
    check('сторож B видит 5 узлов в строю', stb)
    m = node(A, 'meadow')['m']
    check('метрики: память, диски, ядра', m['mem'][1] > 0 and m['cores'] >= 1 and isinstance(m['disks'], list), m)
    check('ping отдаёт имя сторожа', api(A, 'GET', '/v1/ping')['name'] == 'Summit')

    print('\n2. Ключ и защита')
    bad = dict(A['app'], token='wrong')
    try:
        laputa.hub_call(bad, 'GET', '/v1/state')
        check('неверный ключ отвергнут', False)
    except laputa.HubError as e:
        check('неверный ключ отвергнут', '404' in str(e), e)
    try:
        laputa.hub_call(dict(A['app'], pin='00' * 32), 'GET', '/v1/ping')
        check('чужой сертификат отвергнут', False)
    except laputa.HubError as e:
        check('чужой сертификат отвергнут', ('отпечат' in str(e) or 'pin' in str(e)), e)
    try:
        laputa.hub_call(dict(A['agent']), 'GET', '/v1/state')
        check('ключ агента не даёт доступа к панели', False)
    except laputa.HubError:
        check('ключ агента не даёт доступа к панели', True)

    print('\n3. Службы')
    r = cmd(A, 'aurora', 'svc.discover')
    names = [u['unit'] for u in r.get('units', [])]
    check('узел перечисляет службы без системного шума', 'milo-bot.service' in names and 'systemd-journald.service' not in names and 'never-started.service' in names, names)
    check('добавление службы', cmd(A, 'aurora', 'svc.add', unit='milo-bot.service', label='Milo Bot', kind='bot', link='t.me/example_bot').get('ok'))
    cmd(A, 'aurora', 'svc.add', unit='x-ui.service', label='Панель 3X-UI', kind='panel', link='')
    cmd(A, 'meadow', 'svc.add', unit='minecraft.service', label='Minecraft', kind='game', link='')
    s = wait_for(lambda: next((x for x in node(A, 'aurora')['services'] if x['unit'] == 'milo-bot.service'), None), 10)
    check('служба видна в состоянии с ярлыком и ссылкой', s and s['label'] == 'Milo Bot' and s['kind'] == 'bot' and s['state'] == 'active' and s['link'] == 'https://t.me/example_bot' and s['mem'] == 312000000, s)
    check('второй сторож видит ту же службу', wait_for(lambda: any(x['unit'] == 'milo-bot.service' for x in node(B, 'aurora')['services']), 10))
    check('повторное добавление отклонено', not cmd(A, 'aurora', 'svc.add', unit='milo-bot.service').get('ok'))
    check('несуществующая служба отклонена', not cmd(A, 'aurora', 'svc.add', unit='nope.service').get('ok'))
    check('служба вне списка не перезапускается', not cmd(A, 'aurora', 'svc.restart', unit='ssh.service').get('ok'))
    check('опасное имя юнита отклонено', not cmd(A, 'aurora', 'svc.add', unit='a; rm -rf /.service').get('ok'))
    t0 = time.time()
    check('перезапуск службы', cmd(A, 'aurora', 'svc.restart', unit='milo-bot.service').get('ok'))
    check('перезапуск отвечает быстро', time.time() - t0 < 5, time.time() - t0)
    check('журнал службы', 'строка журнала' in cmd(A, 'aurora', 'svc.logs', unit='milo-bot.service', lines=50).get('output', ''))
    check('изменение ярлыка', cmd(A, 'aurora', 'svc.edit', unit='milo-bot.service', label='Мина', kind='bot', link='').get('ok')
          and wait_for(lambda: any(x['label'] == 'Мина' for x in node(A, 'aurora')['services']), 10))

    print('\n4. Падение службы')
    top = api(A, 'GET', '/v1/events?after=0')['seq']
    waiter = {}
    threading.Thread(target=lambda: waiter.update(r=api(A, 'GET', '/v1/events?after=%d&wait=40' % top, timeout=60)), daemon=True).start()
    time.sleep(0.5)
    set_unit('aurora', 'milo-bot.service', state='failed', restarts=5)
    e = wait_for(lambda: events(A, 'svc_failed'), 15)
    check('событие о падении службы', e and e[-1]['sev'] == 'crit' and 'Мина' in e[-1]['title'] and e[-1]['key'] == 'svc:milo-bot.service', e)
    time.sleep(1)
    check('долгий опрос событий отдал тревогу сразу', waiter.get('r') and any(x['type'] == 'svc_failed' for x in waiter['r']['events']), waiter)
    check('сводка считает упавшие службы', api(A, 'GET', '/v1/events?after=0')['summary']['failed'] == 1)
    set_unit('aurora', 'milo-bot.service', state='active')
    check('событие о восстановлении службы', wait_for(lambda: events(A, 'svc_ok'), 15))
    check('остановка из панели не считается бедой', cmd(A, 'aurora', 'svc.stop', unit='x-ui.service').get('ok') and not wait_for(lambda: events(A, 'svc_stopped'), 6))
    cmd(A, 'aurora', 'svc.start', unit='x-ui.service')
    time.sleep(3)
    set_unit('meadow', 'minecraft.service', state='inactive')
    e = wait_for(lambda: events(A, 'svc_stopped'), 15)
    check('остановка мимо панели даёт предупреждение', e and e[-1]['sev'] == 'warn', e)
    set_unit('meadow', 'minecraft.service', state='active')

    print('\n5. Minecraft')
    check('неверный пароль RCON отклонён', not cmd(A, 'meadow', 'mc.setup', host='127.0.0.1', port=25599, password='nope').get('ok'))
    check('подключение RCON', cmd(A, 'meadow', 'mc.setup', host='127.0.0.1', port=25599, password='secret', backups=BASE).get('ok'))
    mc = wait_for(lambda: node(A, 'meadow')['mc'], 10)
    check('игроки и бэкапы в состоянии', mc and mc['online'] and mc['players'] == ['Alpha', 'Beta'] and mc['max'] == 20 and 'password' not in json.dumps(mc), mc)
    r = cmd(A, 'meadow', 'mc.cmd', command='/say привет')
    check('команда в консоль, цветовые коды убраны', r.get('ok') and r.get('output') == 'Done: say привет', r)

    print('\n6. Узел замолчал')
    stop('agent-cobalt')
    t0 = time.time()
    e = wait_for(lambda: events(A, 'node_down'), 30)
    took = time.time() - t0
    check('событие «молчит» с подтверждением второго сторожа', e and e[-1]['sev'] == 'crit' and 'Оба сторожа' in e[-1]['text'], e)
    check('порог срабатывания около 75 с в пересчёте', 13 <= took <= 22, took)
    n = node(A, 'cobalt')
    check('узел помечен как молчащий и подтверждённый', n['status'] == 'down' and n['confirmed'] is True, n)
    check('команда молчащему узлу сразу отклоняется', not cmd(A, 'cobalt', 'svc.discover').get('ok'))
    check('сводка называет молчащий узел', api(A, 'GET', '/v1/events?after=0')['summary']['down'] == [{'id': 'cobalt', 'name': 'Cobalt'}])
    start_agent('cobalt')
    e = wait_for(lambda: events(A, 'node_up'), 20)
    check('событие «снова на связи» с длительностью', e and e[-1]['sev'] == 'ok' and 'Молчал' in e[-1]['text'], e)

    print('\n7. Обрыв связи между узлом и одним сторожем')
    stop('agent-ridge')
    d = os.path.join(BASE, 'agent-ridge', 'agent.json')
    c = json.load(open(d))
    full = c['hubs']
    c['hubs'] = [h for h in full if h['url'] == B['url']]
    json.dump(c, open(d, 'w'))
    start_agent('ridge')
    e = wait_for(lambda: events(A, 'link_lost'), 30)
    check('сторож A сообщает об обрыве, а не о смерти', e and e[-1]['sev'] == 'warn' and node(A, 'ridge')['confirmed'] is False, e)
    check('ложной тревоги «молчит» нет', not [x for x in events(A, 'node_down') if x['node'] == 'ridge'])
    check('сторож B считает узел живым', node(B, 'ridge')['status'] == 'up')
    stop('agent-ridge')
    e = wait_for(lambda: [x for x in events(A, 'node_down') if x['node'] == 'ridge'], 40)
    check('когда узел умер совсем, тревога повышается', e and e[-1]['sev'] == 'crit' and e[-1]['p'].get('both') is True, e)
    c['hubs'] = full
    json.dump(c, open(d, 'w'))
    start_agent('ridge')
    check('узел вернулся на обоих сторожах', wait_for(lambda: node(A, 'ridge')['status'] == 'up' and node(B, 'ridge')['status'] == 'up', 20))

    print('\n8. Перезагрузка из панели и заглушка')
    n_down = len(events(A, 'node_down'))
    check('команда перезагрузки принята', cmd(A, 'meadow', 'node.reboot').get('ok'))
    check('агент вызвал systemctl reboot', wait_for(lambda: os.path.exists(os.path.join(BASE, 'agent-meadow', 'units.json.reboot')), 8))
    stop('agent-meadow')
    wait_for(lambda: node(A, 'meadow')['status'] == 'down', 30)
    n = node(A, 'meadow')
    check('узел в перезагрузке заглушён', n['status'] == 'down' and n['muted_until'] > time.time(), n)
    check('тревоги «молчит» нет', len(events(A, 'node_down')) == n_down)
    check('заглушённый узел не попадает в сводку тревог', api(A, 'GET', '/v1/events?after=0')['summary']['down'] == [])
    start_agent('meadow')
    e = wait_for(lambda: [x for x in events(A, 'node_up') if x['node'] == 'meadow'], 20)
    check('после перезагрузки приходит «снова на связи»', e and 'перезагрузки' in e[-1]['text'], e)
    check('ручная заглушка', api(A, 'POST', '/v1/mute', {'node': 'cobalt', 'minutes': 30}).get('ok') and node(A, 'cobalt')['muted_until'] > time.time() + 1700)
    check('снятие заглушки', api(A, 'POST', '/v1/mute', {'node': 'cobalt', 'minutes': 0}).get('ok') and node(A, 'cobalt')['muted_until'] == 0)

    print('\n9. Перезапуск сторожа')
    seq = api(A, 'GET', '/v1/events?after=0')['seq']
    stop('hub-summit')
    time.sleep(1)
    start_hub(A)
    st = wait_for(lambda: api(A, 'GET', '/v1/state'), 15)
    check('после перезапуска сторож помнит узлы и события', st and len(st['nodes']) == 5 and api(A, 'GET', '/v1/events?after=0')['seq'] >= seq, st and len(st['nodes']))
    time.sleep(8)
    check('перезапуск сторожа не вызвал ложных тревог', api(A, 'GET', '/v1/events?after=%d' % seq)['events'] == [] and all(n['status'] == 'up' for n in api(A, 'GET', '/v1/state')['nodes']),
          api(A, 'GET', '/v1/events?after=%d' % seq)['events'])
    check('сторож B заметил и пережил перезапуск A', node(B, 'summit')['status'] == 'up')

    print('\n10. Забыть узел, долгий опрос состояния')
    stop('agent-cobalt')
    wait_for(lambda: node(A, 'cobalt')['status'] == 'down', 30)
    check('забыть узел', api(A, 'POST', '/v1/forget', {'node': 'cobalt'}).get('ok') and node(A, 'cobalt') is None)
    v = api(A, 'GET', '/v1/state')['v']
    t0 = time.time()
    r = api(A, 'GET', '/v1/state?since=%d&wait=20' % v)
    check('долгий опрос состояния отвечает при изменении', 'nodes' in r and time.time() - t0 < 6, time.time() - t0)
    r = api(A, 'GET', '/v1/state?since=%d&wait=20' % (v + 10 ** 9))
    check('слишком новая версия не подвешивает панель', 'nodes' in r)
    h = api(A, 'GET', '/v1/history?node=meadow&hours=24')
    check('история отдаёт 288 точек по 5 минут', len(h['cpu']) == 288 and h['step'] == 300, len(h['cpu']))

    print('\n11. Перебор ключа')
    blocked = False
    for i in range(24):
        try:
            laputa.hub_call(bad, 'GET', '/v1/ping', timeout=5)
        except Exception as ex:
            blocked = 'сторож ответил' not in str(ex)
    check('после 20 неверных ключей адрес блокируется', blocked)


def unit_tests():
    print('\n12. Логика сторожа без сети')
    d = os.path.join(BASE, 'unit')
    os.makedirs(d, exist_ok=True)
    hub = laputa.Hub({'id': 'h', 'name': 'H', 'port': 1}, d + '/state.json')
    GB = 1 << 30

    def beat(used, svc_state='active', restarts=0, cpu=10.0):
        hub.on_beat({'node': 'n1', 'name': 'N1', 'm': {'cpu': cpu, 'mem': [1 * GB, 4 * GB], 'disks': [{'mount': '/', 'used': used * GB, 'total': 100 * GB}], 'uptime': 5},
                     'services': [{'unit': 'a.service', 'label': 'A', 'kind': 'bot', 'state': svc_state, 'restarts': restarts}]})
    beat(50)
    beat(91)
    check('диск 91 % даёт предупреждение', [e for e in hub.events if e['type'] == 'disk_high'])
    beat(92)
    check('повторного предупреждения о диске нет', len([e for e in hub.events if e['type'] == 'disk_high']) == 1)
    beat(88)
    check('между 85 и 90 % тревога не снимается', not [e for e in hub.events if e['type'] == 'disk_ok'])
    beat(70)
    check('ниже 85 % приходит отбой', [e for e in hub.events if e['type'] == 'disk_ok'])
    for r in (1, 2, 3):
        beat(70, restarts=r)
    check('три перезапуска за 10 минут дают «постоянно перезапускается»', [e for e in hub.events if e['type'] == 'svc_flap'])
    beat(70, 'failed', 3)
    beat(70, 'active', 4)
    beat(70, 'failed', 4)
    check('частые падения одной службы не засыпают тревогами', len([e for e in hub.events if e['type'] == 'svc_failed']) == 1)
    try:
        hub.on_beat({'node': '../../etc', 'name': 'x'})
        check('плохой идентификатор узла отклонён', False)
    except ValueError:
        check('плохой идентификатор узла отклонён', True)
    hub.save()
    hub2 = laputa.Hub({'id': 'h', 'name': 'H', 'port': 1}, d + '/state.json')
    check('состояние переживает перезапуск', 'n1' in hub2.nodes and hub2.seq == hub.seq and hub2.nodes['n1']['disk_alarm'] == [])
    import base64, hashlib, ssl, subprocess as sp
    crt = os.path.join(BASE, 'summit', 'etc', 'tls.crt')
    want = sp.run("openssl x509 -in '%s' -pubkey -noout | openssl pkey -pubin -outform der | openssl dgst -sha256 -binary | base64" % crt, shell=True, capture_output=True, text=True).stdout.strip()
    got = laputa.spki_pin(ssl.PEM_cert_to_DER_cert(open(crt).read()))
    check('отпечаток открытого ключа считается без openssl и совпадает с ним', want and got == want, (want, got))
    rsa = os.path.join(BASE, 'rsa')
    sp.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2', '-subj', '/CN=rsa' + ''.join('/OU=' + c * 60 for c in 'abcdef'), '-keyout', rsa + '.key', '-out', rsa + '.crt'], capture_output=True)
    want = sp.run("openssl x509 -in '%s.crt' -pubkey -noout | openssl pkey -pubin -outform der | openssl dgst -sha256 -binary | base64" % rsa, shell=True, capture_output=True, text=True).stdout.strip()
    check('то же для RSA-сертификата с длинными полями', want and laputa.spki_pin(ssl.PEM_cert_to_DER_cert(open(rsa + '.crt').read())) == want)
    key = laputa.make_key('laputa1', {'url': 'https://203.0.113.5:8743', 'pin': 'ab', 'token': 't', 'name': 'Сторож'})
    check('ключ кодируется и читается обратно, включая кириллицу', laputa.read_key('laputa1', 'текст ' + key + ' хвост')['name'] == 'Сторож')
    check('склонения', laputa.plural(1, 'abc') == 'a' and laputa.plural(3, 'abc') == 'b' and laputa.plural(11, 'abc') == 'c' and laputa.dur_text(3700) == '1 ч 1 мин')


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'test'
    try:
        if mode == 'serve':
            A, B = build()
            fake_rcon(25599, 'secret')
            print(json.dumps({'A': A['app'], 'B': B['app']}), flush=True)
            while True:
                time.sleep(3600)
        scenario()
        unit_tests()
    finally:
        stop_all()
    bad = [n for n, ok in RESULTS if not ok]
    print('\nИтог: %d проверок, не прошло %d' % (len(RESULTS), len(bad)))
    for n in bad:
        print('  НЕ ПРОШЛО:', n)
    sys.exit(1 if bad else 0)
