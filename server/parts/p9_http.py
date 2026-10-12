def token_ok(given, expected):
    return bool(expected) and hmac.compare_digest(given.encode('utf-8'), expected.encode('utf-8'))


def make_handler(hub):
    cfg = hub.cfg

    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        server_version = 'laputa'
        sys_version = ''

        def setup(self):
            self.request.settimeout(20)
            super().setup()

        def log_message(self, *a):
            pass

        def send(self, code, obj=None, raw=None, ctype='application/json'):
            data = raw if raw is not None else json.dumps(obj if obj is not None else {}, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Connection', 'close')
            self.send_header('Cache-Control', 'no-store')
            if cfg.get('cors'):
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Access-Control-Allow-Headers', 'Authorization, Content-Type')
            self.end_headers()
            self.wfile.write(data)
            self.close_connection = True

        def deny(self):
            hub.strike(self.client_address[0])
            time.sleep(0.4)
            self.send(404, {'error': 'not found'})

        def body(self):
            n = int(self.headers.get('Content-Length') or 0)
            if n <= 0 or n > 512 * 1024:
                return {}
            return json.loads(self.rfile.read(n).decode('utf-8'))

        def do_OPTIONS(self):
            self.send(200 if cfg.get('cors') else 404, {})

        def do_GET(self):
            self.route('GET')

        def do_POST(self):
            self.route('POST')

        def route(self, method):
            try:
                self.handle_route(method)
            except (BrokenPipeError, ConnectionError, socket.timeout, ssl.SSLError):
                self.close_connection = True
            except (ValueError, KeyError, TypeError) as e:
                try:
                    self.send(400, {'ok': False, 'error': L('Плохой запрос: %s', 'Bad request: %s') % e})
                except OSError:
                    pass
            except Exception as e:
                log(L('сторож, запрос %s: %r', 'hub, request %s: %r') % (self.path.split('?')[0][:40], e))
                try:
                    self.send(500, {'ok': False, 'error': L('Сбой сторожа', 'Hub failure')})
                except OSError:
                    pass

        def handle_route(self, method):
            ip = self.client_address[0]
            if hub.blocked(ip):
                self.close_connection = True
                return
            u = urlsplit(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            path = u.path
            if path.startswith('/i/') and method == 'GET':  # installer download for nodes
                parts = path.split('/')
                if len(parts) == 4 and token_ok(parts[2], cfg['agent_token']) and parts[3] == 'laputa.py':
                    with open(os.path.abspath(__file__), 'rb') as f:
                        return self.send(200, raw=f.read(), ctype='text/x-python; charset=utf-8')
                return self.deny()
            auth = self.headers.get('Authorization') or ''
            tok = auth[7:] if auth.startswith('Bearer ') else ''
            if path.startswith('/a/'):
                if not token_ok(tok, cfg['agent_token']):
                    return self.deny()
                self.request.settimeout(60)
                if path == '/a/beat' and method == 'POST':
                    hub.on_beat(self.body(), ip)
                    return self.send(200, {'ok': True})
                if path == '/a/poll' and method == 'GET':
                    return self.send(200, hub.poll(q.get('node', ''), min(30, float(q.get('wait', 25)))))
                if path == '/a/result' and method == 'POST':
                    hub.result(self.body())
                    return self.send(200, {'ok': True})
                if path == '/a/view' and method == 'GET':
                    return self.send(200, hub.view())
                return self.send(404, {})
            if path.startswith('/v1/'):
                if not token_ok(tok, cfg['app_token']):
                    return self.deny()
                self.request.settimeout(100)
                if path == '/v1/cmd':
                    self.request.settimeout(660)  # node actions may take up to 10 minutes
                if path == '/v1/ping':
                    return self.send(200, {'ok': True, 'id': cfg['id'], 'name': cfg['name'], 'version': VERSION, 'ts': int(time.time())})
                if path == '/v1/state':
                    since, wait = int(q.get('since', 0)), min(25, float(q.get('wait', 0)))
                    return self.send(200, hub.wait_state(since, wait) if wait > 0 and since else hub.state())
                if path == '/v1/events':
                    wait = min(300, float(q.get('wait', 0)))
                    self.request.settimeout(wait + 40)
                    return self.send(200, hub.wait_events(int(q.get('after', 0)), wait))
                if path == '/v1/history':
                    return self.send(200, hub.history(q.get('node', ''), int(q.get('hours', 24)), q.get('from'), q.get('to')))
                if method == 'POST':
                    b = self.body()
                    if path == '/v1/cmd':
                        return self.send(200, hub.command(b))
                    if path == '/v1/mute':
                        return self.send(200, hub.mute(b.get('node'), b.get('minutes', 0)))
                    if path == '/v1/forget':
                        return self.send(200, hub.forget(b.get('node')))
                    if path == '/v1/diag':
                        return self.send(200, hub.diag(b.get('node')))
                return self.send(404, {})
            return self.deny()

    return H


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64
    limit = threading.BoundedSemaphore(96)

    def process_request(self, request, client_address):
        if not self.limit.acquire(blocking=False):
            try:
                request.close()
            except OSError:
                pass
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.limit.release()

    def handle_error(self, request, client_address):
        pass


def run_hub():
    cfg = jload(HUB_CFG)
    if not cfg:
        raise SystemExit(L('Нет %s. Запустите install-hub.', 'No %s. Run install-hub.') % HUB_CFG)
    hub = Hub(cfg)
    srv = Server((cfg.get('bind', '0.0.0.0'), int(cfg['port'])), make_handler(hub))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(TLS_CRT, TLS_KEY)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True, do_handshake_on_connect=False)
    threading.Thread(target=hub.loop, daemon=True).start()

    def stop(*a):
        hub.save()
        os._exit(0)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log(L('сторож %s (%s) слушает порт %s', 'hub %s (%s) listening on port %s') % (cfg['name'], VERSION, cfg['port']))
    srv.serve_forever(poll_interval=1)


