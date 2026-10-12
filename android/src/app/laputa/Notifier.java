package app.laputa;

import android.app.Notification;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.net.ConnectivityManager;
import android.net.NetworkInfo;
import android.os.Build;

import org.json.JSONObject;

/**
 * Уведомления. Собирается с SDK уровня 23, поэтому каналы (Android 8+) создаются через рефлексию:
 * самих классов в старом SDK нет, а на устройстве они есть.
 */
public final class Notifier implements Checker.Alerts {
    public static final String CH_STATUS = "status";
    public static final int ID_STATUS = 1;
    public static final int ID_TEST = 9;
    static final int COLOR = 0xFF5D7FD6;
    static final int COLOR_BAD = 0xFFE5486B;

    private final Context ctx;
    private final NotificationManager nm;

    public Notifier(Context c) {
        ctx = c.getApplicationContext();
        nm = (NotificationManager) ctx.getSystemService(Context.NOTIFICATION_SERVICE);
        T tr = tr();
        channel(Checker.CH_ALERT, tr.t("j.ch.alert"), tr.t("j.ch.alert.d"), 4);
        channel(Checker.CH_WARN, tr.t("j.ch.warn"), tr.t("j.ch.warn.d"), 3);
        channel(Checker.CH_INFO, tr.t("j.ch.info"), tr.t("j.ch.info.d"), 2);
        channel(CH_STATUS, tr.t("j.ch.status"), tr.t("j.ch.status.d"), 1);
    }

    private static final java.util.HashMap<String, T> DICTS = new java.util.HashMap<String, T>();

    private static JSONObject asset(Context c, String lang) {
        try {
            java.io.InputStream in = c.getAssets().open("ui/i18n/" + lang + ".json");
            java.io.ByteArrayOutputStream buf = new java.io.ByteArrayOutputStream();
            byte[] chunk = new byte[16384];
            int n;
            while ((n = in.read(chunk)) > 0) buf.write(chunk, 0, n);
            in.close();
            return new JSONObject(buf.toString("UTF-8"));
        } catch (Exception e) {
            L.i("словарь " + lang + ": " + e);
            return new JSONObject();
        }
    }

    /** Словарь на текущем языке приложения. */
    public static T tr(Context c) {
        String lang = new Store(c).lang();
        synchronized (DICTS) {
            T t = DICTS.get(lang);
            if (t == null) {
                Context a = c.getApplicationContext();
                t = new T(lang, asset(a, lang), asset(a, "en"), asset(a, "ru"));
                DICTS.put(lang, t);
            }
            return t;
        }
    }

    @Override
    public T tr() {
        return tr(ctx);
    }

    private void channel(String id, String name, String desc, int importance) {
        if (Build.VERSION.SDK_INT < 26) return;
        try {
            Class<?> cls = Class.forName("android.app.NotificationChannel");
            Object ch = cls.getConstructor(String.class, CharSequence.class, int.class).newInstance(id, name, importance);
            cls.getMethod("setDescription", String.class).invoke(ch, desc);
            if (importance <= 2) cls.getMethod("setShowBadge", boolean.class).invoke(ch, false);
            if (importance >= 4) cls.getMethod("enableVibration", boolean.class).invoke(ch, true);
            NotificationManager.class.getMethod("createNotificationChannel", cls).invoke(nm, ch);
        } catch (Throwable t) {
            L.i("канал уведомлений " + id + ": " + t);
        }
    }

    private Notification.Builder builder(String channel) {
        if (Build.VERSION.SDK_INT >= 26) {
            try {
                return Notification.Builder.class.getConstructor(Context.class, String.class).newInstance(ctx, channel);
            } catch (Throwable t) {
                L.i("конструктор уведомления: " + t);
            }
        }
        return new Notification.Builder(ctx);
    }

    private PendingIntent open(String node, int req) {
        Intent i = new Intent(ctx, MainActivity.class);
        i.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        if (node != null && node.length() > 0) i.putExtra("node", node);
        i.setAction("open-" + req); // разные действия, чтобы система не склеила намерения разных уведомлений
        return PendingIntent.getActivity(ctx, req, i, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private PendingIntent action(String what, int nid, JSONObject act) {
        Intent i = new Intent(ctx, ActionReceiver.class);
        i.setAction("app.laputa." + what + "-" + nid);
        i.putExtra("what", what);
        i.putExtra("nid", nid);
        i.putExtra("act", act.toString());
        return PendingIntent.getBroadcast(ctx, nid * 2 + (what.equals("mute") ? 0 : 1), i, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private void addActions(Notification.Builder b, int id, JSONObject act) {
        if (act == null || act.optString("node").length() == 0) return;
        String sev = act.optString("sev"), type = act.optString("type");
        if (!"crit".equals(sev) && !"warn".equals(sev)) return;
        T tr = tr();
        String unit = act.optString("unit");
        if (unit.length() > 0 && ("svc_failed".equals(type) || "svc_stopped".equals(type) || "svc_flap".equals(type) || "boot_missing".equals(type))) {
            Notification.Action.Builder ab = new Notification.Action.Builder(R.drawable.ic_stat, tr.t("j.act.restart"), action("restart", id, act));
            try {   // Android 12+: перезапуск службы только с разблокированного телефона
                Notification.Action.Builder.class.getMethod("setAuthenticationRequired", boolean.class).invoke(ab, true);
            } catch (Throwable ignored) {
            }
            b.addAction(ab.build());
        }
        b.addAction(new Notification.Action.Builder(R.drawable.ic_stat, tr.t("j.act.mute"), action("mute", id, act)).build());
    }

    public void show(int id, String channel, String title, String text, String node, boolean quiet) {
        show(id, channel, title, text, node, quiet, null);
    }

    @Override
    public void show(int id, String channel, String title, String text, String node, boolean quiet, JSONObject act) {
        try {
            boolean alert = Checker.CH_ALERT.equals(channel);
            Notification.Builder b = builder(channel)
                    .setSmallIcon(R.drawable.ic_stat)
                    .setContentTitle(title)
                    .setContentText(text)
                    .setStyle(new Notification.BigTextStyle().bigText(text))
                    .setContentIntent(open(node, id))
                    .setAutoCancel(true)
                    .setShowWhen(true)
                    .setWhen(System.currentTimeMillis())
                    .setColor(alert ? COLOR_BAD : COLOR)
                    .setOnlyAlertOnce(quiet)
                    .setCategory(alert ? Notification.CATEGORY_ALARM : Notification.CATEGORY_STATUS)
                    .setVisibility(Notification.VISIBILITY_PRIVATE);
            addActions(b, id, act);   // кнопки только у тревог и предупреждений: у отбоя их нет
            if (Build.VERSION.SDK_INT < 26) {
                b.setPriority(alert ? Notification.PRIORITY_HIGH : quiet ? Notification.PRIORITY_LOW : Notification.PRIORITY_DEFAULT);
                if (alert) b.setDefaults(Notification.DEFAULT_ALL);
            }
            nm.notify(id, b.build());
            L.i("уведомление [" + channel + "] " + title + ". " + text);
        } catch (Throwable t) {
            L.i("не удалось показать уведомление: " + t);
        }
    }

    public Notification statusNotification(String text, boolean bad) {
        Notification.Builder b = builder(CH_STATUS)
                .setSmallIcon(R.drawable.ic_stat)
                .setContentTitle(text)
                .setContentText(tr().t("j.live"))
                .setContentIntent(open(null, ID_STATUS))
                .setOngoing(true)
                .setShowWhen(false)
                .setOnlyAlertOnce(true)
                .setColor(bad ? COLOR_BAD : COLOR)
                .setCategory(Notification.CATEGORY_SERVICE)
                .setVisibility(Notification.VISIBILITY_PUBLIC);
        if (Build.VERSION.SDK_INT < 26) b.setPriority(Notification.PRIORITY_MIN);
        return b.build();
    }

    private String lastStatus = "";

    @Override
    public void status(String text, boolean bad) {
        Widget.update(ctx);
        if (!LiveService.running) return; // строка состояния есть только у постоянной службы
        String k = text + bad;
        if (k.equals(lastStatus)) return;
        lastStatus = k;
        try {
            nm.notify(ID_STATUS, statusNotification(text, bad));
        } catch (Throwable ignored) {
        }
    }

    @Override
    public boolean online() {
        try {
            ConnectivityManager cm = (ConnectivityManager) ctx.getSystemService(Context.CONNECTIVITY_SERVICE);
            NetworkInfo ni = cm.getActiveNetworkInfo();
            return ni != null && ni.isConnected();
        } catch (Throwable t) {
            return true;
        }
    }

    public boolean enabled() {
        try {
            if (Build.VERSION.SDK_INT >= 33 && ctx.checkSelfPermission("android.permission.POST_NOTIFICATIONS") != 0) return false;
            if (Build.VERSION.SDK_INT >= 24) {
                return (Boolean) NotificationManager.class.getMethod("areNotificationsEnabled").invoke(nm);
            }
        } catch (Throwable ignored) {
        }
        return true;
    }

    /** HTTP для проверок: запросы к сторожу с его отпечатком и ключом. */
    public static final Checker.Http HTTP = new Checker.Http() {
        @Override
        public Net.Resp get(JSONObject hub, String path, int timeoutMs) throws Exception {
            String url = hub.optString("url").replaceAll("/+$", "");
            return Net.request(url + path, hub.optString("pin"), hub.optString("token"), "GET", null, timeoutMs);
        }
    };
}
