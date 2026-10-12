package app.laputa;

import android.app.Activity;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.res.Configuration;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.PowerManager;
import android.provider.Settings;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.webkit.JavascriptInterface;
import android.webkit.ValueCallback;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import org.json.JSONObject;

import java.io.InputStream;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** The only screen: a WebView with the UI from assets/ui and a bridge to the native side. */
public final class MainActivity extends Activity {
    private static final String ORIGIN = "https://appassets.laputa/";
    private WebView web;
    private Store store;
    private Notifier notifier;
    private final ExecutorService pool = Executors.newCachedThreadPool();
    private volatile boolean ready;
    private String pendingNode;

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        L.init(this);
        store = new Store(this);
        notifier = new Notifier(this);

        web = new WebView(this);
        web.setLayoutParams(new ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        web.setBackgroundColor(0xFF0D1430);
        // Last run's colours: window and bars start in the chosen theme, no dark flash.
        try {
            String last = store.get("chrome");
            if (last != null && last.length() > 0) {
                JSONObject c = new JSONObject(last);
                paintChrome(c);
                web.setBackgroundColor(Color.parseColor(c.optString("top", "#0d1430")));
            }
        } catch (Throwable ignored) {
        }
        web.setOverScrollMode(View.OVER_SCROLL_NEVER);
        web.setVerticalScrollBarEnabled(false);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);
        s.setTextZoom(100);
        s.setSupportZoom(false);
        s.setMediaPlaybackRequiresUserGesture(true);
        web.addJavascriptInterface(new Bridge(), "LaputaNative");
        web.setWebViewClient(new Client());
        setContentView(web);

        pendingNode = getIntent() == null ? null : getIntent().getStringExtra("node");
        web.loadUrl(ORIGIN + "index.html");

        WatchJob.schedule(this);
        LiveService.apply(this);
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        String node = intent == null ? null : intent.getStringExtra("node");
        if (node != null) {
            if (ready) emit("{\"type\":\"open\",\"node\":" + JSONObject.quote(node) + "}");
            else pendingNode = node;
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        web.onResume();
        LiveService.apply(this);
        if (ready) emit("{\"type\":\"resume\"}");
    }

    @Override
    protected void onPause() {
        if (ready) emit("{\"type\":\"pause\"}");
        web.onPause();
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        pool.shutdownNow();
        web.destroy();
        super.onDestroy();
    }

    @Override
    public void onConfigurationChanged(Configuration cfg) {
        super.onConfigurationChanged(cfg);
        if (ready) emit("{\"type\":\"theme\",\"night\":" + night() + "}");
    }

    @Override
    public void onBackPressed() {
        if (!ready) {
            super.onBackPressed();
            return;
        }
        web.evaluateJavascript("(window.__laputa&&window.__laputa.back())===true", new ValueCallback<String>() {
            @Override
            public void onReceiveValue(String v) {
                if (!"true".equals(v)) moveTaskToBack(true);
            }
        });
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] perms, int[] results) {
        if (code == 33 && results.length > 0 && results[0] != 0 && !shouldShowRequestPermissionRationale(perms[0])) {
            openNotificationSettings(); // the system no longer asks: open the app settings
        }
        emit("{\"type\":\"env\",\"env\":" + env() + "}");
    }

    private boolean night() {
        return (getResources().getConfiguration().uiMode & Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES;
    }

    private String env() {
        JSONObject o = new JSONObject();
        try {
            o.put("version", getPackageManager().getPackageInfo(getPackageName(), 0).versionName);
            o.put("night", night());
            o.put("notif", notifier.enabled());
            o.put("battery", ((PowerManager) getSystemService(POWER_SERVICE)).isIgnoringBatteryOptimizations(getPackageName()));
            o.put("sdk", Build.VERSION.SDK_INT);
            o.put("lang", java.util.Locale.getDefault().getLanguage());
            JSONObject monet = monet();
            if (monet != null) o.put("monet", monet);
        } catch (Exception ignored) {
        }
        return o.toString();
    }

    /** Material You palette from the wallpaper (Android 12+): system_accent1_500 and friends. Not available earlier. */
    private JSONObject monet() {
        if (Build.VERSION.SDK_INT < 31) return null;
        try {
            JSONObject m = new JSONObject();
            int[] tones = {0, 10, 50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 1000};
            for (String pal : new String[]{"accent1", "accent2", "accent3", "neutral1", "neutral2"}) {
                JSONObject t = new JSONObject();
                for (int tone : tones) {
                    int id = getResources().getIdentifier("system_" + pal + "_" + tone, "color", "android");
                    if (id != 0) t.put(String.valueOf(tone), String.format("#%06X", 0xFFFFFF & getColor(id)));
                }
                m.put(pal, t);
            }
            return m.optJSONObject("accent1").length() > 0 ? m : null;
        } catch (Throwable t) {
            L.i("Monet palette: " + t);
            return null;
        }
    }

    private void emit(final String json) {
        web.post(new Runnable() {
            @Override
            public void run() {
                web.evaluateJavascript("window.__laputa&&window.__laputa.emit(" + json + ")", null);
            }
        });
    }

    private void reply(final int id, final boolean ok, final String jsonValue) {
        web.post(new Runnable() {
            @Override
            public void run() {
                web.evaluateJavascript("window.__laputa&&window.__laputa.resolve(" + id + "," + ok + "," + jsonValue + ")", null);
            }
        });
    }

    private void chrome(final JSONObject a) {
        store.put("chrome", a.toString());
        runOnUiThread(new Runnable() {
            @Override
            public void run() {
                paintChrome(a);
            }
        });
    }

    private void paintChrome(JSONObject a) {
        try {
            Window w = getWindow();
            int top = Color.parseColor(a.optString("top", "#0d1430"));
            w.setStatusBarColor(top);
            w.setNavigationBarColor(Color.parseColor(a.optString("bottom", "#151e38")));
            w.getDecorView().setBackgroundColor(top);
            int flags = 0;
            if (a.optBoolean("lightTop")) flags |= View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR;
            if (a.optBoolean("lightBottom")) flags |= 0x10; // SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR, Android 8+
            w.getDecorView().setSystemUiVisibility(flags);
        } catch (Throwable ignored) {
        }
    }

    private void openNotificationSettings() {
        try {
            Intent i = new Intent("android.settings.APP_NOTIFICATION_SETTINGS");
            i.putExtra("android.provider.extra.APP_PACKAGE", getPackageName());
            startActivity(i);
        } catch (Throwable t) {
            try {
                startActivity(new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:" + getPackageName())));
            } catch (Throwable ignored) {
            }
        }
    }

    private String call(String method, JSONObject a) throws Exception {
        if ("http".equals(method)) {
            Net.Resp r = Net.request(a.optString("url"), a.optString("pin"), a.optString("token"), a.optString("method", "GET"),
                    a.optString("body", ""), a.optInt("timeout", 15000));
            JSONObject o = new JSONObject();
            o.put("status", r.status);
            o.put("body", r.body);
            return o.toString();
        }
        if ("log.put".equals(method)) {
            String t = a.optString("text");
            L.i(t.length() > 600 ? t.substring(0, 600) : t);
            return "true";
        }
        if ("env".equals(method)) return env();
        if ("store.get".equals(method)) return store.cfg().toString();
        if ("store.set".equals(method)) {
            String was = store.lang();
            store.setCfg(a);
            if (!was.equals(store.lang())) {   // language changed: rebuild notification channels and the widget
                new Notifier(this);
                Widget.update(this);
            }
            return "true";
        }
        if ("ready".equals(method)) {
            ready = true;
            if (pendingNode != null) {
                emit("{\"type\":\"open\",\"node\":" + JSONObject.quote(pendingNode) + "}");
                pendingNode = null;
            }
            return "true";
        }
        if ("chrome".equals(method)) {
            chrome(a);
            return "true";
        }
        if ("alerts.apply".equals(method)) {
            WatchJob.schedule(this);
            LiveService.apply(this);
            return "true";
        }
        if ("alerts.test".equals(method)) {
            T tr = notifier.tr();
            notifier.show(Notifier.ID_TEST, Checker.CH_ALERT, tr.t("j.test"), tr.t("j.test.x"), null, false);
            return "true";
        }
        if ("perm.notifications".equals(method)) {
            runOnUiThread(new Runnable() {
                @Override
                public void run() {
                    if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission("android.permission.POST_NOTIFICATIONS") != 0) {
                        requestPermissions(new String[]{"android.permission.POST_NOTIFICATIONS"}, 33);
                    } else {
                        openNotificationSettings();
                    }
                }
            });
            return "true";
        }
        if ("perm.battery".equals(method)) {
            runOnUiThread(new Runnable() {
                @Override
                public void run() {
                    try {
                        startActivity(new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:" + getPackageName())));
                    } catch (Throwable t) {
                        try {
                            startActivity(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS));
                        } catch (Throwable ignored) {
                        }
                    }
                }
            });
            return "true";
        }
        if ("clipboard.read".equals(method)) {
            ClipboardManager cm = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
            ClipData d = cm.getPrimaryClip();
            String t = d != null && d.getItemCount() > 0 ? String.valueOf(d.getItemAt(0).coerceToText(this)) : "";
            return JSONObject.quote(t);
        }
        if ("clipboard.write".equals(method)) {
            ClipboardManager cm = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
            cm.setPrimaryClip(ClipData.newPlainText("Laputa", a.optString("text")));
            return "true";
        }
        if ("open".equals(method)) {
            String url = a.optString("url");
            if (url.startsWith("https://") || url.startsWith("http://") || url.startsWith("tg://")) {
                Intent i = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
                i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                startActivity(i);
            }
            return "true";
        }
        if ("log.get".equals(method)) {
            String chk = store.get("chk");
            String head = "mode: " + (store.live() ? "live" : "eco") + ", live service: " + (LiveService.running ? "on" : "off")
                    + ", lang: " + store.lang() + "\nchecks: " + chk + "\n\n";
            return JSONObject.quote(head + L.read());
        }
        throw new Exception("unknown call " + method);
    }

    public final class Bridge {
        @JavascriptInterface
        public void post(final String msg) {
            pool.execute(new Runnable() {
                @Override
                public void run() {
                    int id = 0;
                    try {
                        JSONObject m = new JSONObject(msg);
                        id = m.optInt("id");
                        JSONObject args = m.optJSONObject("args");
                        String res = call(m.optString("method"), args == null ? new JSONObject() : args);
                        reply(id, true, res);
                    } catch (Throwable t) {
                        reply(id, false, JSONObject.quote(notifier.tr().exc(t)));
                    }
                }
            });
        }
    }

    private final class Client extends WebViewClient {
        private String mime(String path) {
            if (path.endsWith(".html")) return "text/html";
            if (path.endsWith(".js")) return "application/javascript";
            if (path.endsWith(".css")) return "text/css";
            if (path.endsWith(".woff2")) return "font/woff2";
            if (path.endsWith(".svg")) return "image/svg+xml";
            if (path.endsWith(".png")) return "image/png";
            if (path.endsWith(".json")) return "application/json";
            return "application/octet-stream";
        }

        @Override
        public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
            String url = request.getUrl().toString();
            if (!url.startsWith(ORIGIN)) {
                // the UI lives inside the app: the WebView never goes out, network goes through the bridge
                return new WebResourceResponse("text/plain", "utf-8", new java.io.ByteArrayInputStream(new byte[0]));
            }
            String path = request.getUrl().getPath();
            if (path == null || path.equals("/") || path.contains("..")) path = "/index.html";
            try {
                InputStream in = getAssets().open("ui" + path);
                String m = mime(path);
                return new WebResourceResponse(m, m.startsWith("text/") || m.contains("javascript") ? "utf-8" : null, in);
            } catch (Exception e) {
                return new WebResourceResponse("text/plain", "utf-8", new java.io.ByteArrayInputStream(new byte[0]));
            }
        }

        @Override
        public boolean shouldOverrideUrlLoading(WebView view, String url) {
            return !url.startsWith(ORIGIN); // never navigate away from the UI
        }
    }
}
