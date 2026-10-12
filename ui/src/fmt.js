// Language-independent formatting and friendly names of known services.
import { t } from './i18n.js';

export function pct(used, total) {
  if (!total) return 0;
  return Math.max(0, Math.min(100, (used / total) * 100));
}

export const round = Math.round;

// Known services: a proper name or a description in the user's language.
const KNOWN = [
  [/^awg-quick@(.+)$/, (m) => t('unit.awg') + (m[1] === 'awg0' ? '' : ' ' + m[1])],
  [/^wg-quick@(.+)$/, (m) => t('unit.wg') + (m[1] === 'wg0' ? '' : ' ' + m[1])],
  [/^sshd?$/, 'SSH'], [/^(3x-ui|x-ui)$/, '3X-UI'], [/^xray$/, 'Xray'], [/^sing-box$/, 'sing-box'], [/^hysteria(-server)?(@.*)?$/, 'Hysteria'],
  [/^openvpn(-server)?(@.*)?$/, 'OpenVPN'], [/^tailscaled$/, 'Tailscale'], [/^cloudflared$/, 'Cloudflare Tunnel'],
  [/^nginx$/, 'Nginx'], [/^caddy$/, 'Caddy'], [/^(apache2|httpd)$/, 'Apache'], [/^traefik$/, 'Traefik'], [/^haproxy$/, 'HAProxy'],
  [/^docker$/, 'Docker'], [/^containerd$/, 'containerd'], [/^postgresql(@.*)?$/, 'PostgreSQL'], [/^mariadb$/, 'MariaDB'], [/^mysql$/, 'MySQL'],
  [/^redis(-server)?$/, 'Redis'], [/^mongod$/, 'MongoDB'], [/^jellyfin$/, 'Jellyfin'], [/^plexmediaserver$/, 'Plex'], [/^navidrome$/, 'Navidrome'],
  [/^emby-server$/, 'Emby'], [/^minecraft(-server)?$/, 'Minecraft'], [/^beszel(-agent)?$/, 'Beszel'], [/^netdata$/, 'Netdata'], [/^grafana(-server)?$/, 'Grafana'],
  [/^prometheus$/, 'Prometheus'], [/^uptime-kuma$/, 'Uptime Kuma'], [/^gitea$/, 'Gitea'], [/^forgejo$/, 'Forgejo'], [/^vaultwarden$/, 'Vaultwarden'],
  [/^fail2ban$/, 'Fail2ban'], [/^cron$|^crond$|^cronie$/, () => t('unit.cron')], [/^ufw$/, () => t('unit.ufw')], [/^firewalld$/, () => t('unit.firewalld')],
];

export function unitName(unit) {
  if (unit.startsWith('docker:')) return unit.slice(7);
  const base = unit.replace(/\.service$/, '');
  for (const [re, v] of KNOWN) {
    const m = re.exec(base);
    if (m) return typeof v === 'function' ? v(m) : v;
  }
  const s = base.replace(/[-_@.]+/g, ' ').trim();
  return s ? s[0].toUpperCase() + s.slice(1) : unit;
}

export function guessKind(unit, desc) {
  const u = (unit + ' ' + (desc || '')).toLowerCase();
  if (/bot/.test(u)) return 'bot';
  if (/minecraft|paper|spigot|palworld|valheim|factorio|terraria|ark|rust|cs2|steamcmd/.test(u)) return 'game';
  if (/x-ui|3x-ui|panel|cockpit|webmin|portainer/.test(u)) return 'panel';
  if (/xray|sing-box|awg|wg-quick|wireguard|hysteria|tuic|openvpn|tailscale|cloudflared/.test(u)) return 'vpn';
  if (/jellyfin|plex|navidrome|emby|immich|photoprism/.test(u)) return 'media';
  if (/beszel|netdata|prometheus|grafana|zabbix|uptime-kuma/.test(u)) return 'monitor';
  if (/nginx|caddy|apache|httpd|traefik|haproxy/.test(u)) return 'web';
  if (/docker|containerd|postgres|mysql|maria|redis|mongo/.test(u)) return 'base';
  return 'other';
}

export const KINDS = ['bot', 'panel', 'game', 'vpn', 'media', 'web', 'monitor', 'base', 'other'];

export function kindName(kind, many) {
  return t('kind.' + (KINDS.includes(kind) ? kind : 'other') + (many ? '.many' : ''));
}

// Event kinds for the filter and notification settings. Same list in android/.../Checker.java.
export const EV_KINDS = ['down', 'svc', 'disk', 'backup', 'ssh', 'reboot'];
const EV_KIND = {
  node_new: 'down', node_down: 'down', group_down: 'down', node_up: 'down', link_lost: 'down', hub_down: 'down', hub_up: 'down',
  svc_failed: 'svc', svc_stopped: 'svc', svc_ok: 'svc', svc_flap: 'svc', boot_missing: 'svc',
  disk_high: 'disk', disk_ok: 'disk', backup_late: 'backup', backup_ok: 'backup', ssh_login: 'ssh',
  reboot: 'reboot', reboot_gaveup: 'reboot', maint_reboot: 'reboot',
};
export function evKind(type) { return EV_KIND[type] || 'other'; }
