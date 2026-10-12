package app.laputa;

import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * Режим «Сразу»: служба держит открытое соединение со сторожем. Сторож отвечает в ту же секунду,
 * как появляется событие, а при тишине раз в четыре минуты, чтобы соединение не умерло молча.
 */
public final class LiveService extends Service {
    static volatile boolean running;
    static volatile long lastOk;
    private static final int WAIT_SEC = 240;
    private Thread thread;
    private volatile boolean stop;

    public static void apply(Context c) {
        Store store = new Store(c);
        boolean want = store.live() && store.hubs().length() > 0;
        Intent i = new Intent(c, LiveService.class);
        try {
            if (want) {
                if (Build.VERSION.SDK_INT >= 26) {
                    Context.class.getMethod("startForegroundService", Intent.class).invoke(c, i);
                } else {
                    c.startService(i);
                }
            } else {
                c.stopService(i);
            }
        } catch (Throwable t) {
            // из фона систему не всегда можно попросить запустить службу: тогда работает проверка по расписанию
            Throwable cause = t.getCause() != null ? t.getCause() : t;
            L.i("постоянная служба не запущена: " + cause);
        }
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public void onCreate() {
        super.onCreate();
        L.init(this);
        final Notifier notifier = new Notifier(this);
        final Store store = new Store(this);
        String text = notifier.tr().t("j.connecting");
        try {
            String s = store.get("chk");
            if (s.length() > 0) text = new JSONObject(s).optString("text", text);
        } catch (Exception ignored) {
        }
        startForeground(Notifier.ID_STATUS, notifier.statusNotification(text, false));
        running = true;
        L.i("постоянная служба запущена");
        final PowerManager.WakeLock wl = ((PowerManager) getSystemService(POWER_SERVICE)).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "laputa:event");
        wl.setReferenceCounted(false);
        final Checker.Http http = new Checker.Http() {
            @Override
            public Net.Resp get(JSONObject hub, String path, int timeoutMs) throws Exception {
                Net.Resp r = Notifier.HTTP.get(hub, path, timeoutMs);
                try {
                    wl.acquire(15000); // ответ пришёл: не даём телефону уснуть, пока показываем уведомление
                } catch (Throwable ignored) {
                }
                return r;
            }
        };
        final Checker checker = new Checker(http, notifier, store);
        thread = new Thread(new Runnable() {
            @Override
            public void run() {
                int fails = 0;
                while (!stop) {
                    JSONArray hubs = store.hubs();
                    if (hubs.length() == 0 || !store.live()) break;
                    boolean ok = checker.pass(hubs, WAIT_SEC);
                    if (ok) {
                        fails = 0;
                        lastOk = System.currentTimeMillis();
                    } else {
                        fails++;
                        if (fails == 1 || fails % 20 == 0) L.i("сторожа не отвечают, попытка " + fails);
                        try {
                            Thread.sleep(Math.min(60000L, 5000L * fails));
                        } catch (InterruptedException e) {
                            break;
                        }
                    }
                }
                running = false;
                stopSelf();
            }
        }, "laputa-live");
        thread.setDaemon(true);
        thread.start();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        stop = true;
        running = false;
        if (thread != null) thread.interrupt();
        L.i("постоянная служба остановлена");
        super.onDestroy();
    }
}
