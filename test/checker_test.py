#!/usr/bin/env python3
"""The Java alert logic (same as in the APK) on a JVM against the live bench."""
import json, os, subprocess, sys, threading, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'bench'))
import run as B  # noqa: E402

JVM = os.path.join(B.BASE + '-jvm')
CP = JVM + ':/usr/share/java/com.android.json.jar'  # org.json: part of Android on the phone, the libandroid-json-java package on the JVM


def compile_java():
    os.makedirs(JVM, exist_ok=True)
    a = os.path.join(HERE, '..', 'android')
    r = subprocess.run(['javac', '--release', '8', '-Xlint:-options', '-nowarn', '-cp', '/usr/lib/android-sdk/platforms/android-23/android.jar', '-d', JVM,
                        a + '/src/app/laputa/Net.java', a + '/src/app/laputa/Checker.java', a + '/src/app/laputa/T.java', a + '/test/CheckerCli.java'], capture_output=True, text=True, env=dict(os.environ, JAVA_TOOL_OPTIONS=''))
    if r.returncode:
        sys.exit('javac: ' + r.stderr)
KV = os.path.join(B.BASE, 'kv.json')
HUBS = os.path.join(B.BASE, 'hubs.json')


I18N = os.path.join(HERE, '..', 'ui', 'i18n')


def java(wait=0, mode='', lang='ru'):
    env = dict(os.environ, JAVA_TOOL_OPTIONS='', LAPUTA_I18N=I18N, LAPUTA_LANG=lang)
    p = subprocess.run(['java', '-cp', CP, 'CheckerCli', KV, HUBS, str(wait)] + ([mode] if mode else []), capture_output=True, text=True, env=env, timeout=120)
    lines = [l.split('|') for l in p.stdout.splitlines() if '|' in l]
    if p.returncode:
        print(p.stderr[-800:])
    return {'alerts': [l for l in lines if l[0] == 'ALERT'], 'status': [l for l in lines if l[0] == 'STATUS'], 'ok': any(l[0] == 'RESULT' and l[1] == 'true' for l in lines),
            'ms': next((int(l[2]) for l in lines if l[0] == 'RESULT'), -1)}


def kv():
    return json.loads(json.load(open(KV))['chk'])


def kv_patch(**kw):
    o = json.load(open(KV))
    c = json.loads(o['chk'])
    c.update(kw)
    o['chk'] = json.dumps(c)
    json.dump(o, open(KV, 'w'))


def main():
    compile_java()
    A, Bh = B.build()
    json.dump([A['app'], Bh['app']], open(HUBS, 'w'))
    B.wait_for(lambda: len(B.api(A, 'GET', '/v1/state')['nodes']) == 5 and len(B.api(Bh, 'GET', '/v1/state')['nodes']) == 5, 20)
    B.cmd(A, 'aurora', 'svc.add', unit='milo-bot.service', label='Milo Bot', kind='bot')
    time.sleep(3)
    print('\n1. Первое знакомство со сторожем')
    r = java()
    B.check('первый проход: связь есть, тревог нет', r['ok'] and not r['alerts'], r)
    B.check('строка состояния «Все 5 в строю»', r['status'] and r['status'][-1][1] == 'Все 5 в строю' and r['status'][-1][2] == 'false', r['status'])
    r = java()
    B.check('повторный проход ничего не повторяет', r['ok'] and not r['alerts'])

    print('\n2. Мгновенная тревога через открытое соединение')
    out = {}
    t = threading.Thread(target=lambda: out.update(r=java(wait=40)))
    t.start()
    time.sleep(2)
    B.set_unit('aurora', 'milo-bot.service', state='failed', restarts=2)
    t.join(30)
    r = out.get('r') or {'alerts': [], 'ms': -1}
    B.check('тревога пришла, не дожидаясь конца ожидания', r['alerts'] and r['alerts'][0][2] == 'alerts' and 'Упала служба Milo Bot' in r['alerts'][0][3] and 0 < r['ms'] < 9000, r)
    act = json.loads(r['alerts'][0][7]) if r['alerts'] and len(r['alerts'][0]) > 7 and r['alerts'][0][7] else {}
    B.check('текст собран из параметров события, кнопки знают службу', r['alerts'] and r['alerts'][0][4] == 'Aurora, milo-bot.service. Перезапусков: 2.'
            and act.get('unit') == 'milo-bot.service' and act.get('node') == 'aurora' and act.get('label') == 'Milo Bot', r['alerts'])
    fail_id = r['alerts'][0][1] if r['alerts'] else None
    B.set_unit('aurora', 'milo-bot.service', state='active')
    r = java(wait=20)
    B.check('отбой заменяет тревогу тем же уведомлением и без звука', r['alerts'] and r['alerts'][0][1] == fail_id and r['alerts'][0][2] == 'info' and r['alerts'][0][6] == 'true', r)

    print('\n2б. Что присылать: настройки на телефоне')
    def cfg(**settings):
        o = json.load(open(KV))
        o['cfg'] = json.dumps({'hubs': [], 'settings': settings})
        json.dump(o, open(KV, 'w'))
    java()
    cfg(notify={'svc': 'off'})
    B.set_unit('aurora', 'milo-bot.service', state='failed', restarts=3)
    r = B.wait_for(lambda: (lambda x: x if x['ok'] else None)(java(wait=15)), 30)
    B.check('«не присылать»: о падении службы уведомления нет', r and not [a for a in r['alerts'] if 'Milo Bot' in a[3]], r)
    B.set_unit('aurora', 'milo-bot.service', state='active')
    r = java(wait=15)
    B.check('и об отбое тоже', not [a for a in r['alerts'] if 'Milo Bot' in a[3]], r['alerts'])
    # the watchman won't repeat an alert for the same service within 5 minutes, so use other services
    B.cmd(A, 'aurora', 'svc.add', unit='mike-bot.service', label='Mike', kind='bot')
    B.cmd(A, 'aurora', 'svc.add', unit='x-ui.service', label='Panel', kind='panel')
    time.sleep(3)
    java(wait=0)
    cfg(notify={'svc': 'quiet'}, notifyNode={'aurora': {'svc': 'sound'}})
    B.set_unit('aurora', 'mike-bot.service', state='failed')
    r = java(wait=15)
    B.check('своя настройка узла сильнее общей: со звуком', [a for a in r['alerts'] if 'Mike' in a[3] and a[2] == 'alerts' and a[6] == 'false'], r['alerts'])
    B.set_unit('aurora', 'mike-bot.service', state='active')
    java(wait=15)
    cfg(notify={'svc': 'quiet'})
    B.set_unit('aurora', 'x-ui.service', state='failed')
    r = java(wait=15)
    B.check('«тихо»: в тихом канале без звука', [a for a in r['alerts'] if 'Panel' in a[3] and a[2] == 'info' and a[6] == 'true'], r['alerts'])
    B.set_unit('aurora', 'x-ui.service', state='active')
    java(wait=15)
    cfg()

    print('\n2а. Языки')
    for lang, want in (('en', 'All 5 are up'), ('de', 'Alle 5 laufen'), ('uk', 'Усі 5 у строю'), ('zh', '全部 5 个节点运行正常')):
        kv_patch(hub='')
        r = java(lang=lang)
        B.check('строка состояния: ' + lang, r['status'] and r['status'][-1][1] == want, r['status'])
    for lang, n, want in (('ru', 3, 'Молчат 3 узла'), ('ru', 5, 'Молчат 5 узлов'), ('ru', 21, 'Молчит 21 узел'), ('uk', 3, 'Мовчать 3 вузли'), ('en', 2, '2 nodes are silent'), ('es', 2, '2 nodos no responden')):
        summ = {'down': [{'id': 'x%d' % i, 'name': 'X'} for i in range(n)], 'failed': 0, 'total': 30}
        env = dict(os.environ, JAVA_TOOL_OPTIONS='', LAPUTA_I18N=I18N, LAPUTA_LANG=lang, LAPUTA_STATUS_ONLY=json.dumps(summ))
        got = subprocess.run(['java', '-cp', CP, 'CheckerCli', KV, HUBS, '0'], capture_output=True, text=True, env=env).stdout.strip().split('|', 1)[-1]
        B.check('формы числа: %s %d' % (lang, n), got == want, got)
    kv_patch(hub='')
    java()

    print('\n3. Узел замолчал и вернулся')
    B.stop('agent-cobalt')
    r = java(wait=40)
    B.check('тревога «Cobalt молчит» в канале тревог', r['alerts'] and r['alerts'][0][2] == 'alerts' and r['alerts'][0][3] == 'Cobalt молчит' and r['alerts'][0][5] == 'cobalt', r)
    B.check('строка состояния называет узел', r['status'] and r['status'][-1][1] == 'Cobalt молчит' and r['status'][-1][2] == 'true', r['status'])
    down_id = r['alerts'][0][1] if r['alerts'] else None
    B.start_agent('cobalt')
    r = java(wait=30)
    B.check('«снова на связи» заменяет тревогу', r['alerts'] and r['alerts'][0][1] == down_id and r['alerts'][0][2] == 'info', r)

    print('\n4. Сторож умер: переход на второго')
    B.stop('agent-ridge')
    B.wait_for(lambda: B.node(Bh, 'ridge')['status'] == 'down' and B.node(A, 'ridge')['status'] == 'down', 30)
    r = java(wait=0)  # the app learned about Ridge from the first watchman
    B.check('первый сторож сообщил о Ridge сразу как о молчащем, без ложного «обрыва»', [a for a in r['alerts'] if a[3] == 'Ridge молчит'] and not [a for a in r['alerts'] if 'потерял' in a[3]], r['alerts'])
    B.set_unit('aurora', 'milo-bot.service', state='failed')  # trouble happens while the app is offline
    B.stop('hub-summit')  # only the watchman died, the node is alive
    B.wait_for(lambda: B.events(Bh, 'svc_failed') and B.events(Bh, 'hub_down'), 30)
    r = java(wait=30)
    B.check('второй сторож подхватил, связь есть', r['ok'] and kv()['hub'] == Bh['url'], r)
    B.check('про уже известный молчащий узел повторной тревоги нет', not [a for a in r['alerts'] if 'Ridge' in a[3]], r['alerts'])
    B.check('беда, случившаяся во время смены сторожа, не потерялась', [a for a in r['alerts'] if 'Упала служба Milo Bot' in a[3] and a[2] == 'alerts'], r['alerts'])
    B.check('сосед сообщил, что сторож не работает, хотя узел жив', [a for a in r['alerts'] if a[3] == 'Сторож Summit не отвечает' and a[2] == 'warn'], r['alerts'])
    B.stop('agent-summit')  # now the whole server died
    r2 = B.wait_for(lambda: (lambda x: x if [a for a in x['alerts'] if a[3] == 'Summit молчит'] else None)(java(wait=10)), 40, 0.2)
    B.check('о смерти сервера со сторожем сообщил второй сторож', r2 is not None, r2)
    B.set_unit('aurora', 'milo-bot.service', state='active')
    B.start_hub(A)
    B.start_agent('summit')
    B.start_agent('ridge')
    B.wait_for(lambda: B.node(Bh, 'summit')['status'] == 'up' and B.node(Bh, 'ridge')['status'] == 'up' and B.events(Bh, 'hub_up'), 40)
    r = java(wait=5)
    B.check('возвращения приходят тихими отбоями', len([a for a in r['alerts'] if a[2] == 'info' and a[6] == 'true']) >= 3 and not [a for a in r['alerts'] if a[2] != 'info'], r['alerts'])

    print('\n5. Недоступны все сторожа')
    B.stop('hub-summit')
    B.stop('hub-aurora')
    r = java()
    B.check('первая неудача: тревоги ещё нет', not r['ok'] and not r['alerts'], r)
    B.check('строка состояния «Нет связи со сторожами»', r['status'] and r['status'][-1][1] == 'Нет связи со сторожами')
    kv_patch(lostSince=int(time.time() * 1000) - 200000)
    r = java()
    B.check('через 3 минуты приходит «Сторожа недоступны»', r['alerts'] and r['alerts'][0][2] == 'warn' and r['alerts'][0][3] == 'Сторожа недоступны', r)
    r = java()
    B.check('повторно не тревожит', not r['alerts'], r)
    kv_patch(lostSince=int(time.time() * 1000) - 200000, lostNotified=False)
    r = java(mode='offline')
    B.check('телефон без сети: тревоги нет, строка «Телефон не в сети»', not r['alerts'] and r['status'][-1][1] == 'Телефон не в сети', r)
    kv_patch(lostSince=int(time.time() * 1000) - 200000, lostNotified=True)
    B.start_hub(A)
    B.start_hub(Bh)
    time.sleep(2)
    r = java()
    B.check('сторожа вернулись: отбой «снова на связи»', r['ok'] and [a for a in r['alerts'] if a[3] == 'Сторожа снова на связи'], r)

    print('\n6. Защита')
    hubs = json.load(open(HUBS))
    json.dump([dict(hubs[0], pin='00' * 32)], open(HUBS, 'w'))
    r = java()
    B.check('подменённый сертификат: связи нет', not r['ok'], r)
    json.dump([dict(hubs[0], token='wrong')], open(HUBS, 'w'))
    r = java()
    B.check('неверный ключ: связи нет', not r['ok'], r)


if __name__ == '__main__':
    try:
        main()
    finally:
        B.stop_all()
    bad = [n for n, ok in B.RESULTS if not ok]
    print('\nИтог: %d проверок, не прошло %d' % (len(B.RESULTS), len(bad)))
    sys.exit(1 if bad else 0)
