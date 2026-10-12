<div align="center">

# Laputa

**A personal server panel: an Android app, plus a watchman and an agent in one dependency-free Python file.**

[![License: GPL-3.0-or-later](https://img.shields.io/badge/license-GPL--3.0--or--later-blue.svg)](LICENSE)
![Android 8.0+](https://img.shields.io/badge/Android-8.0%2B-3DDC84?logo=android&logoColor=white)
![Python 3.8+](https://img.shields.io/badge/Python-3.8%2B-3776AB?logo=python&logoColor=white)

English · [Русский](README.ru.md)

</div>

Laputa tells you when a server or a service dies, and why: a reboot from the panel, an out-of-memory kill, a lost network link, a power cut at one location. From the alert you can restart the service or mute the node. Everything else you need day to day is on the node's screen: services, Docker containers, backups, updates, a game console and your own action buttons.

## Contents

- [How it works](#how-it-works)
- [Installation](#installation)
- [Node screen](#node-screen)
- [App](#app)
- [Alerts](#alerts)
- [Maintenance](#maintenance)
- [Security](#security)
- [What is tested](#what-is-tested)
- [Building](#building)
- [License](#license)

## Repository layout

| Path | What it is |
|---|---|
| `server/laputa.py` | Watchman and agent, one file, no dependencies. Built from `server/parts/` |
| `android/` | App shell: WebView, notifications, widget, background check |
| `ui/` | App interface and dictionaries for six languages |
| `test/`, `tools/` | Tests and the test bench |

The APK is built from source (see [Building](#building)).

## How it works

- **The agent** runs on every node as root. Every 10 seconds it pushes metrics and service states to the watchmen itself, so the node needs no open inbound ports: a server behind NAT works too.
- **The watchman** (hub) runs on a server with an open port, as a separate unprivileged user. It decides who went silent, keeps the event log and answers the app.
- **The app** only knows the watchmen. It holds no node addresses; everything comes through the key.

Two watchmen on different servers are better. Then they can tell a dead node from a broken link, and report each other's death.

A node is considered late after 35 seconds without a signal and silent after 75.

## Installation

You need Linux, Python 3.8 or newer, and `openssl` on the watchman's server. Services are visible where systemd exists, containers where Docker exists. Tested on Debian, AlmaLinux and Alpine; pending updates are counted for apt, dnf/yum, pacman, zypper and apk.

Server messages (installer output, `status`, `keys`, service logs) are in English or Russian. The language is taken from the system locale at install time, or set with `--lang en` / `--lang ru` on `install-hub` and `install-agent`, and is remembered. The command printed by `keys` passes the watchman's language on to new nodes.

### 1. First watchman

Copy `server/laputa.py` to the server as `/tmp/laputa.py` (for example with `scp`).

**On the watchman's server:**

```
sudo python3 /tmp/laputa.py install-hub --host PUBLIC_ADDRESS --name NAME --place PLACE --open-port
```

`--open-port` opens port 8743/tcp in ufw or firewalld. Without it, open the port yourself.

The command prints the app key (`laputa1:…`) and a ready-made command for the other nodes.

### 2. Other nodes

**On every node**, run the printed command with the name and place filled in:

```
curl -fsSk --pinnedpubkey 'sha256//…' https://ADDRESS:8743/i/…/laputa.py -o /tmp/laputa.py && sudo python3 /tmp/laputa.py install-agent --hub 'laputa1a:…' --lang en --name NAME --place PLACE
```

### 3. Second watchman

**On the second server** (its agent is already there after step 2, the file is in `/opt/laputa/`):

```
sudo python3 /opt/laputa/laputa.py install-hub --host PUBLIC_ADDRESS --open-port
```

Run its node command **on every node, including the first watchman**. The agent adds the second watchman to the first and keeps its services. Without this, each watchman only watches itself, and nobody notices when it dies.

### 4. Phone

1. Install the APK.
2. Paste the app key of the first watchman.
3. On the main screen, allow notifications and background work.
4. Add the second watchman's key under **Settings**, **Watchmen**, **Add**.

### 5. Services

Open a node, under **Services** tap **Add**. The agent picks what is worth watching and says why: listens on a port, has its own unit in `/etc/systemd/system`, has crashed before, a known program, a Docker container. Recommended ones are already checked; just tap **Add**. Name, type and link can be changed later on the service card, **Edit label**.

Services that appear later and run for more than an hour are quietly suggested on the node screen: **Watch** or **Don't suggest**.

## Node screen

- **Actions.** Custom buttons are defined only on the server itself, in `/etc/laputa/actions.ini`. The app shows and runs them but cannot create them. The file contains an example:

  ```
  [Update bot]
  command = cd /opt/mybot && git pull && systemctl restart mybot
  service = mybot.service   ; the button also appears on this service's card
  confirm = yes             ; ask before running
  timeout = 120             ; seconds, at most 600
  user = mybot              ; who to run as, root by default
  ```

  The file is re-read automatically; no agent restart needed.

- **Game console.** Appears when the node has a service of type **Game**. For Minecraft you see the players; for any other game with RCON, just the console. Your own command buttons (**+** under the console) are stored on the node.
- **Backups.** **Watch backups**: the agent finds backup-like folders, timers and cron jobs. The panel learns how often copies appear; if the next one is twice as late, you get a warning.
- **Maintenance.** How many updates are pending, how many of them are security updates, and whether the node asks for a reboot.
- **Reboot.** Before it you see who it affects: players, connections to services, active AmneziaWG and WireGuard peers. Options: now; with a warning to players 5 minutes and 1 minute ahead; when nobody is left on the node (waits up to 24 hours, then gives up and says so). Restarting a service that someone is connected to also asks first.
- **If a node rebooted on its own**, once it is back the screen shows the cause from the previous boot's journal (out of memory, kernel panic, hang, cut-off with no records), the last log lines and graphs for the 30 minutes before the crash. If the node came back without rebooting, the event says the network was lost, not the server. Services that ran before the reboot and did not come up after it raise an alert.
- **If a node is silent**, neighbouring nodes check it with ping, its SSH port and traceroute. **Text for the hoster** puts the results into a ready-made message. If every node at one place goes silent, you get one alert guessing that power or internet is out there.

## App

- Languages: English, Russian, Ukrainian, German, Spanish, Chinese. **Settings**, **Language**; follows the system by default. Notifications and server errors use the chosen language too.
- Home screen widget: one line about the state of the nodes, updated after every check.
- An alert has **Restart** (for a failed service; on Android 12 and newer only from an unlocked phone) and **Mute for an hour** buttons.
- An SSH login from an address the node has not seen before raises a warning. Logins from known addresses are recorded in **Events** silently.
- Themes: System, Night, Day, Dusk, Ocean, Black (AMOLED), Dawn, Mint. On Android 12 and newer also Monet dark and Monet light, coloured from the wallpaper.
- Services on the node screen and in the **Services** tab are folded by type when there are more than six. A group with something broken is always open.
- **Events** tab: filter by event kind and by node, plus search. The filter is remembered.
- **Settings**, **What to notify about**: for each kind of event (node link, services, disk, backups, SSH logins, reboots) — with sound, quiet, or don't send (only recorded in **Events**). On the node screen, **Notifications for this node** overrides this for one node. These settings live on the phone; every app user has their own.
- After **Reboot node** the node gets a blinking blue lamp and the label **Rebooting** until it is back. Services that stop during the reboot raise no alerts.
- With more than 8 nodes the sky is drawn in two tiers; with more than 16, one island per place with a node count. Nodes in trouble stay as separate islands.

## Alerts

| Event | Channel |
|---|---|
| Node silent, service failed | Alerts, with sound |
| Service did not come up after a reboot, every node at one place went silent | Alerts, with sound |
| Disk almost full (90 %), service stopped outside the panel, service keeps restarting, a watchman is down, watchmen unreachable for 3 minutes, backups late, SSH login from a new address, node never emptied for a reboot | Warnings |
| Node or service is back | All clear, silent, replaces the earlier alert |

In **Instantly** mode the app keeps a connection open to the watchman and learns about events within seconds. In **Every 15 minutes** mode it wakes on a schedule. The scheduled check runs in both modes as a safety net.

Rebooting a node from the panel mutes its alerts for 10 minutes. Stopping a service from the panel is not an alarm.

## Maintenance

**On the server:**

```
sudo python3 /opt/laputa/laputa.py status      # what the watchman sees, or which watchmen an agent reports to
sudo python3 /opt/laputa/laputa.py keys        # print the keys again
sudo python3 /opt/laputa/laputa.py rekey       # new app key, if the phone is lost
sudo python3 /opt/laputa/laputa.py uninstall   # remove Laputa from the server
journalctl -u laputa-hub -u laputa-agent -n 50
```

Updating: put the new `laputa.py` into `/tmp` and either repeat the same `install-hub` or `install-agent` command, or replace the file and restart:

```
sudo install -m 755 /tmp/laputa.py /opt/laputa/laputa.py && sudo systemctl restart 'laputa-*'
```

Keys, services and history are kept. Update every server, the watchmen first: the node command from `keys` downloads the file from the watchman.

If the app misbehaves: **Settings**, **App log**.

## Security

- All connections use TLS. The watchman's certificate is self-signed; its fingerprint is built into the key, and the app and agents check exactly that.
- The app key gives full control: restarting and stopping services from the panel's list, rebooting nodes, game console commands, running actions from `actions.ini`. There are no arbitrary shell commands in the protocol: an action is a command the owner wrote on the server.
- The neighbour check only probes the silent node's address as the watchman saw it; local addresses are refused.
- Agents have their own key. They can only send a signal and fetch commands.
- After 20 wrong keys from one address, the watchman ignores it for 10 minutes.
- The RCON password is stored only on the node, in `/etc/laputa/agent.json`, readable by root. `actions.ini` is created readable by root only.

## What is tested

Automatically:

- Server: 68 checks of the 1.0 features and 108 checks of the 1.1–1.2 features (Docker, service discovery, actions, console, backups, "who is affected", every kind of reboot, crash analysis, post-boot reconciliation, neighbour checks, place-wide alert, updates, SSH logins, error codes, server message language) on a bench of watchmen and agents, under Python 3.8 and 3.13. systemd, Docker, ss, awg, journalctl, ping, traceroute and apt are stubbed on the bench.
- Installer: in the official Debian 12, Debian 13, AlmaLinux 9 and Alpine 3.22 images, including apt, dnf and apk update counts.
- Interface: 34 checks in Chromium against the live bench, and 15 through a stubbed Android bridge, including language switching.
- App alert logic: 38 checks, the same Java code on a regular JVM against the bench, including plural forms in different languages.
- The texts of 31 event kinds match between the interface and notifications in all six languages. All dictionaries are complete.

Not tested:

- Real Docker, AmneziaWG and previous-boot journals; crash analysis is tested on sample records.
- A real Minecraft server and other games with RCON.
- Counting updates with pacman and zypper, the ufw and firewalld branches.
- Translations other than Russian were not made by native speakers.

## Building

Built without Gradle, using tools from the Ubuntu 24.04 repositories (`aapt android-sdk-platform-23 android-sdk-build-tools dalvik-exchange apksigner zipalign`) and Node.js:

```
cd ui && npm install && cd .. && android/build.sh
```

Only built-in Android APIs are used; compilation targets SDK 23. Notification channels and starting the background service go through reflection.

The signing key is not in the repository. The build looks for it in `keystore/` (or in the folder named by `LAPUTA_KEYSTORE`: `laputa.jks` and `pass.txt`), and creates a new one if there is none. An APK signed with a different key will not install over an existing one, so keep your key.

The server is assembled from parts:

```
cd server/parts && cat p0_common.py p1_modules.py p2_agent.py p3_hub.py p9_http.py p9_install.py > ../laputa.py
```

## License

Laputa is free software: you can redistribute it and/or modify it under the terms of the
[GNU General Public License](LICENSE) as published by the Free Software Foundation, either version 3
of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the
implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License
for more details.

Bundled third-party components keep their own licenses: [Preact](https://github.com/preactjs/preact) (MIT),
[htm](https://github.com/developit/htm) (Apache 2.0), the [Onest](https://github.com/simpals/onest) and
[Unbounded](https://github.com/googlefonts/unbounded) fonts (SIL Open Font License 1.1).
