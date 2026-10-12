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
