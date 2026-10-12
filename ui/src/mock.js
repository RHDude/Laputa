// Mock data for trying the UI without a server. Not part of the production build.

const GB = 1024 * 1024 * 1024;
const MB = 1024 * 1024;

function wave(n, base, amp, seed) {
  const out = [];
  let x = seed;
  for (let i = 0; i < n; i++) {
    x = (x * 9301 + 49297) % 233280;
    const r = x / 233280;
    out.push(Math.max(1, Math.min(99, base + Math.sin(i / 9 + seed) * amp + (r - 0.5) * amp)));
  }
  return out;
}

export function mockState(variant) {
  const now = Math.floor(Date.now() / 1000);
  const bad = variant === 'bad';
  const nodes = [
    {
      id: 'meadow', name: 'Meadow', place: 'дом', hub: false, addr: '198.51.100.24',
      status: 'up', last_seen: now - 3, since: now - 86400 * 12 - 3600 * 4, confirmed: null, muted_until: 0, agent: '1.1.0',
      m: { cpu: 23.4, cores: 4, load: [0.92, 0.71, 0.66], mem: [9.1 * GB, 15.5 * GB], swap: [0, 0.9 * GB],
        disks: [{ mount: '/', used: 9.8 * GB, total: 15.6 * GB }, { mount: '/srv', used: 61 * GB, total: 194 * GB }],
        uptime: 86400 * 12 + 3600 * 4, temp: 47, net: [184000, 96000], os: 'Debian 13', kernel: '6.12.48', host: 'Meadow', ssh_port: 22 },
      services: [
        { unit: 'minecraft.service', label: 'Minecraft', kind: 'game', link: '', state: 'active', sub: 'running', since: now - 86400 * 3, mem: 4.6 * GB, restarts: 0 },
        { unit: 'docker:jellyfin', label: 'Jellyfin', kind: 'media', link: 'https://media.example.org', state: 'active', sub: 'running', since: now - 86400 * 12, mem: null, restarts: 0 },
        { unit: 'awg-quick@awg0.service', label: 'Туннель AmneziaWG', kind: 'vpn', link: '', state: 'active', sub: 'exited', since: now - 86400 * 12, mem: null, restarts: 0 },
      ],
      game: { type: 'minecraft', online: true, players: ['Steve', 'Alex', 'night_owl'], max: 20, cfg: { host: '127.0.0.1', port: 25575, type: 'minecraft' },
        quick: [{ label: 'Ночь', command: 'time set night' }, { label: 'Шторм', command: 'weather thunder' }] },
      mc: null,
      backups: [{ path: '/srv/minecraft/backups', label: 'Minecraft', ts: now - (bad ? 3600 * 9 : 60 * 41), size: 248 * MB, count: 24, every: 3600 * 3 }],
      backup_alarm: bad ? ['/srv/minecraft/backups'] : [],
      actions: [{ id: 'a1', name: 'Обновить моды', service: 'minecraft.service', confirm: true, command: 'cd /srv/minecraft && ./update-mods.sh' }],
      maint: { pm: 'apt', updates: 14, security: 3, reboot: true, why: 'kernel', pkgs: [], ts: now - 600 },
      candidates: [{ unit: 'nginx.service', reasons: ['port', 'known', 'autostart'], ports: [80, 443], desc: 'A high performance web server' }],
      pending: null, pm: null, diag: null, boot_missing: [],
    },
    {
      id: 'ridge', name: 'Ridge', place: 'дом', hub: false, addr: '198.51.100.25',
      status: 'up', last_seen: now - 6, since: now - 86400 * 2, confirmed: null, muted_until: 0, agent: '1.1.0',
      m: { cpu: 6.1, cores: 4, load: [0.12, 0.1, 0.09], mem: [2.3 * GB, 15.4 * GB], swap: [0, 0],
        disks: [{ mount: '/', used: 7.4 * GB, total: 28.7 * GB }, { mount: '/srv', used: 38 * GB, total: 405 * GB }],
        uptime: 3600 * 7, temp: 51, net: [22000, 31000], os: 'Debian 13', kernel: '6.12.48', host: 'Ridge' },
      services: [
        { unit: 'docker.service', label: 'Docker', kind: 'base', link: '', state: 'active', sub: 'running', since: now - 3600 * 7, mem: 210 * MB, restarts: 0 },
        { unit: 'beszel-agent.service', label: 'Beszel', kind: 'monitor', link: '', state: bad ? 'inactive' : 'active', sub: 'running', since: now - 3600 * 7, mem: 18 * MB, restarts: 0 },
      ],
      game: null, mc: null, backups: [], actions: [], maint: null, candidates: [], pending: null, diag: null,
      boot_missing: bad ? ['beszel-agent.service'] : [],
      pm: { cause: 'oom', oom: true, last_ts: now - 3600 * 7 - 200, down_at: now - 3600 * 7 - 150, up_at: now - 3600 * 7,
        lines: [[now - 3600 * 7 - 230, 'kernel: java invoked oom-killer: gfp_mask=0x140cca(GFP_HIGHUSER_MOVABLE)'], [now - 3600 * 7 - 210, 'kernel: Out of memory: Killed process 4121 (java) total-vm:9123456kB'], [now - 3600 * 7 - 200, 'systemd[1]: minecraft.service: A process of this unit has been killed by the OOM killer.']] },
    },
    {
      id: 'aurora', name: 'Aurora', place: 'Польша', hub: true, addr: '203.0.113.10',
      status: 'up', last_seen: now - 2, since: now - 86400 * 31, confirmed: null, muted_until: 0, agent: '1.1.0',
      m: { cpu: 11.8, cores: 2, load: [0.31, 0.28, 0.22], mem: [1.42 * GB, 1.9 * GB], swap: [0.21 * GB, 1 * GB],
        disks: [{ mount: '/', used: bad ? 27.6 * GB : 17.2 * GB, total: 30 * GB }],
        uptime: 86400 * 31 + 3600 * 2, temp: null, net: [1240000, 3900000], os: 'Debian 12', kernel: '6.1.0', host: 'aurora' },
      services: [
        { unit: 'x-ui.service', label: '3X-UI', kind: 'panel', link: 'https://panel.example.org:7443/', state: 'active', sub: 'running', since: now - 86400 * 9, mem: 96 * MB, restarts: 1 },
        { unit: 'milo-bot.service', label: 'Milo Bot', kind: 'bot', link: 'https://t.me/example_bot', state: bad ? 'failed' : 'active', sub: bad ? 'failed' : 'running', since: bad ? now - 60 * 17 : now - 86400 * 4, mem: bad ? null : 312 * MB, restarts: bad ? 5 : 0 },
        { unit: 'mike-bot.service', label: 'MikeModBot', kind: 'bot', link: '', state: 'active', sub: 'running', since: now - 86400 * 9, mem: 148 * MB, restarts: 0 },
      ],
      game: null, mc: null, backups: [], actions: [{ id: 'a2', name: 'Обновить бота', service: 'milo-bot.service', confirm: false, command: 'cd /opt/booru && git pull' }],
      maint: { pm: 'apt', updates: 0, security: 0, reboot: false, why: '', pkgs: [] }, candidates: [], pending: bad ? null : { mode: 'empty', since: now - 600, until: now + 3 * 3600, hours: 3, left: 2 }, pm: null, diag: null, boot_missing: [],
    },
    {
      id: 'summit', name: 'Summit', place: 'Казань', hub: true, addr: '203.0.113.20',
      status: 'up', last_seen: now - 1, since: now - 86400 * 18, confirmed: null, muted_until: 0, agent: '1.1.0',
      m: { cpu: 3.2, cores: 1, load: [0.05, 0.04, 0.01], mem: [0.38 * GB, 0.96 * GB], swap: [0, 0],
        disks: [{ mount: '/', used: 4.1 * GB, total: 25 * GB }],
        uptime: 86400 * 18 + 3600 * 11, temp: null, net: [410000, 388000], os: 'AlmaLinux 9.4', kernel: '5.14.0', host: 'summit' },
      services: [
        { unit: 'awg-quick@awg0.service', label: 'Туннель до дома', kind: 'vpn', link: '', state: 'active', sub: 'exited', since: now - 86400 * 18, mem: null, restarts: 0 },
      ],
      game: null, mc: null, backups: [], actions: [], maint: { pm: 'dnf', updates: 3, security: 1, reboot: false, why: '', pkgs: [] }, candidates: [], pending: null, pm: null, diag: null, boot_missing: [],
    },
    {
      id: 'cobalt', name: 'Cobalt', place: 'Чехия', hub: false, addr: '192.0.2.77',
      status: bad ? 'down' : 'up', last_seen: bad ? now - 60 * 4 - 12 : now - 4, since: bad ? now - 60 * 4 - 12 : now - 86400 * 6,
      confirmed: bad ? true : null, muted_until: 0, agent: '1.1.0',
      m: { cpu: 8.4, cores: 2, load: [0.2, 0.18, 0.15], mem: [0.9 * GB, 3.8 * GB], swap: [0, 0],
        disks: [{ mount: '/', used: 6.3 * GB, total: 40 * GB }],
        uptime: 86400 * 6, temp: null, net: [800000, 2100000], os: 'Alpine 3.20', kernel: '6.6.31', host: 'cobalt', ssh_port: 2222 },
      services: [
        { unit: 'x-ui.service', label: '3X-UI', kind: 'panel', link: '', state: 'active', sub: 'running', since: now - 86400 * 6, mem: 88 * MB, restarts: 0 },
      ],
      game: null, mc: null, backups: [], actions: [], maint: null, candidates: [], pending: null, pm: null, boot_missing: [],
      diag: bad ? { ts: now - 60 * 3, ip: '192.0.2.77', port: 2222, running: false, results: [
        { from: 'Aurora', ok: true, ip: '192.0.2.77', ping: { sent: 3, recv: 0, avg: null }, tcp: { port: 2222, ok: false, err: 'TimeoutError' },
          trace: { tool: 'tracepath', hops: [{ n: 1, ip: '10.0.0.1', ms: 0.4 }, { n: 2, ip: '203.0.113.77', ms: 4.1 }, { n: 3, ip: '*', ms: null }] },
          raw: 'PING 192.0.2.77 (192.0.2.77) 56(84) bytes of data.\n--- 192.0.2.77 ping statistics ---\n3 packets transmitted, 0 received, 100% packet loss, time 2040ms' },
        { from: 'Summit', ok: true, ip: '192.0.2.77', ping: { sent: 3, recv: 0, avg: null }, tcp: { port: 2222, ok: false, err: 'TimeoutError' }, trace: { tool: 'traceroute', hops: [{ n: 1, ip: '10.1.0.1', ms: 0.3 }, { n: 2, ip: '203.0.113.77', ms: 31 }] }, raw: '' },
      ] } : null,
    },
  ];
  const events = bad ? [
    { seq: 43, ts: now - 60 * 3 - 12, type: 'node_down', sev: 'crit', node: 'cobalt', key: 'node', title: 'Cobalt молчит', text: '', p: { name: 'Cobalt', both: true } },
    { seq: 42, ts: now - 60 * 17, type: 'svc_failed', sev: 'crit', node: 'aurora', key: 'svc:milo-bot.service', title: 'Упала служба Milo Bot', text: '', p: { name: 'Aurora', label: 'Milo Bot', unit: 'milo-bot.service', restarts: 5 } },
    { seq: 41, ts: now - 3600 * 2, type: 'disk_high', sev: 'warn', node: 'aurora', key: 'disk:/', title: '', text: '', p: { name: 'Aurora', mount: '/', pct: 92 } },
    { seq: 40, ts: now - 3600 * 3, type: 'backup_late', sev: 'warn', node: 'meadow', key: 'backup:/srv/minecraft/backups', title: '', text: '', p: { name: 'Meadow', label: 'Minecraft', path: '/srv/minecraft/backups', age: 3600 * 6, every: 3600 * 3 } },
    { seq: 39, ts: now - 3600 * 5, type: 'ssh_login', sev: 'warn', node: 'aurora', key: 'ssh:203.0.113.50', title: '', text: '', p: { name: 'Aurora', user: 'root', ip: '203.0.113.50', method: 'publickey', new: true } },
    { seq: 38, ts: now - 3600 * 7 + 180, type: 'boot_missing', sev: 'crit', node: 'ridge', key: 'boot', title: '', text: '', p: { name: 'Ridge', labels: ['Beszel'] } },
    { seq: 37, ts: now - 3600 * 7, type: 'node_up', sev: 'ok', node: 'ridge', key: 'node', title: '', text: '', p: { name: 'Ridge', gone: 150, why: 'boot', cause: 'oom' } },
    { seq: 36, ts: now - 3600 * 7 - 100, type: 'node_down', sev: 'crit', node: 'ridge', key: 'node', title: '', text: '', p: { name: 'Ridge', both: true } },
    { seq: 35, ts: now - 3600 * 26, type: 'group_down', sev: 'crit', node: 'meadow', key: 'node', title: '', text: '', p: { names: ['Meadow', 'Ridge'], place: 'дом' } },
  ] : [
    { seq: 38, ts: now - 3600 * 26, type: 'node_up', sev: 'ok', node: 'ridge', key: 'node', title: '', text: '', p: { name: 'Ridge', gone: 240, why: 'net', cause: '' } },
    { seq: 37, ts: now - 3600 * 26 - 240, type: 'node_down', sev: 'crit', node: 'ridge', key: 'node', title: '', text: '', p: { name: 'Ridge', both: true } },
    { seq: 36, ts: now - 3600 * 50, type: 'reboot', sev: 'info', node: 'meadow', key: 'reboot', title: '', text: '', p: { name: 'Meadow' } },
  ];
  if (new URLSearchParams(location.search).has('many')) {
    const extra = [['autossh-aurora.service', 'Autossh aurora', 'vpn'], ['fail2ban.service', 'Fail2ban', 'monitor'], ['containerd.service', 'containerd', 'base'],
      ['ssh.service', 'SSH', 'other'], ['ModemManager.service', 'ModemManager', 'other'], ['NetworkManager.service', 'NetworkManager', 'other'], ['bluetooth.service', 'Bluetooth', 'other'],
      ['ufw.service', 'Файрвол UFW', 'monitor']].map(([unit, label, kind]) => ({ unit, label, kind, link: '', state: 'active', sub: 'running', since: now - 3600 * 16, mem: null, restarts: 0 }));
    nodes[1].services.push(...extra);
    nodes[4].services.push(...extra);
  }
  if (new URLSearchParams(location.search).has('boot')) Object.assign(nodes[2], { rebooting: now - 25, muted_until: now + 575 });
  // ?n=12: multiply nodes to see how the sky copes with many islands
  const want = +new URLSearchParams(location.search).get('n') || 0;
  const places = ['Франкфурт', 'Амстердам', 'Хельсинки', 'дом'];
  for (let i = nodes.length; i < want; i++) {
    const b = nodes[i % 4 === 0 ? 3 : 1];
    nodes.push({ ...b, id: 'n' + i, name: ['Node', 'Edge', 'Relay', 'Vault', 'Mirror'][i % 5] + '-' + i, place: places[i % 4], pm: null, boot_missing: [],
      status: i === 9 ? 'down' : 'up', confirmed: i === 9 ? true : null, since: i === 9 ? now - 300 : b.since, diag: null });
  }
  return { v: 1, ts: now, hub: { id: 'summit', name: 'Summit', version: '1.1.0' }, nodes, events };
}

export function mockHistory(node) {
  const seed = node.length * 7;
  return { step: 300, from: Math.floor(Date.now() / 1000) - 86400, cpu: wave(288, 18, 14, seed), mem: wave(288, 58, 6, seed + 3), disk: wave(288, 40, 1, seed) };
}



export function mockCmd(type, a) {
  const LOG = [
    'окт 08 01:41:07 aurora python3[2211]: aiogram.dispatcher: Polling stopped',
    'окт 08 01:41:07 aurora python3[2211]: Traceback (most recent call last):',
    'окт 08 01:41:07 aurora python3[2211]:   File "/opt/milobot/main.py", line 88, in <module>',
    'окт 08 01:41:07 aurora python3[2211]: sqlite3.OperationalError: database or disk is full',
    'окт 08 01:41:08 aurora systemd[1]: milo-bot.service: Main process exited, code=exited, status=1/FAILURE',
  ].join('\n');
  switch (type) {
    case 'svc.suggest': return { ok: true, units: [
      { unit: 'nginx.service', desc: 'A high performance web server', state: 'active', score: 6, reasons: ['port', 'known', 'autostart'], ports: [80, 443], monitored: false, recommended: true },
      { unit: 'docker:immich', desc: 'ghcr.io/immich-app/immich-server', state: 'active', score: 6, reasons: ['port', 'container'], ports: [2283], monitored: false, recommended: true },
      { unit: 'mybot.service', desc: 'My bot', state: 'failed', score: 4, reasons: ['manual', 'crashed'], ports: [], monitored: false, recommended: true },
      { unit: 'minecraft.service', desc: 'Minecraft', state: 'active', score: 9, reasons: ['port', 'manual', 'known', 'autostart'], ports: [25565], monitored: true, recommended: true },
      { unit: 'cron.service', desc: 'Regular background program processing daemon', state: 'active', score: 1, reasons: ['autostart'], ports: [], monitored: false, recommended: false },
      { unit: 'ssh.service', desc: 'OpenBSD Secure Shell server', state: 'active', score: 4, reasons: ['port', 'autostart'], ports: [22], monitored: false, recommended: true },
      { unit: 'ufw.service', desc: 'Uncomplicated firewall', state: 'active', score: 1, reasons: ['autostart'], ports: [], monitored: false, recommended: false },
    ] };
    case 'svc.add_many': return { ok: true, added: (a.items || []).length, errors: [] };
    case 'svc.logs': return { ok: true, output: LOG };
    case 'impact': return { ok: true, impact: a.unit && a.unit !== 'minecraft.service' ? { players: null, conns: [], tunnels: [], total: 0 }
      : { players: ['Steve', 'Alex', 'night_owl'], conns: [{ unit: 'minecraft.service', label: 'Minecraft', ports: [25565], n: 3 }], tunnels: a.unit ? [] : [{ iface: 'awg0', n: 2 }], total: a.unit ? 6 : 8 } };
    case 'backup.suggest': return { ok: true, dirs: [{ path: '/var/backups/site', label: 'site', ts: Date.now() / 1000 - 7200, size: 12 * MB, count: 14, every: 86400, reason: 'name' }],
      hints: ['timer: restic-backup.timer', 'cron crontab: 0 4 * * * root /usr/local/bin/backup.sh'] };
    case 'game.cmd': return { ok: true, output: a.command === 'list' ? 'There are 3 of a max of 20 players online: Steve, Alex, night_owl' : 'Done' };
    case 'action.run': return { ok: true, rc: 0, output: 'Already up to date.\nmods: 42 checked, 0 updated' };
    case 'node.reboot': return { ok: true, pending: a.mode };
    default: return { ok: true, output: '' };
  }
}
