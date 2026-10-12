import app.laputa.Checker;
import app.laputa.Net;
import app.laputa.T;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.nio.file.Files;

/** One pass of the real alert logic on a plain JVM. Notifications are printed as lines. */
public class CheckerCli {
    public static void main(String[] a) throws Exception {
        final File kvFile = new File(a[0]);
        JSONArray hubs = new JSONArray(new String(Files.readAllBytes(new File(a[1]).toPath()), "UTF-8"));
        int wait = Integer.parseInt(a[2]);
        final boolean offline = a.length > 3 && a[3].equals("offline");
        Checker.KV kv = new Checker.KV() {
            public String get(String k) {
                try { return new JSONObject(new String(Files.readAllBytes(kvFile.toPath()), "UTF-8")).optString(k, ""); } catch (Exception e) { return ""; }
            }
            public void put(String k, String v) {
                try {
                    JSONObject o;
                    try { o = new JSONObject(new String(Files.readAllBytes(kvFile.toPath()), "UTF-8")); } catch (Exception e) { o = new JSONObject(); }
                    o.put(k, v);
                    Files.write(kvFile.toPath(), o.toString().getBytes("UTF-8"));
                } catch (Exception e) { throw new RuntimeException(e); }
            }
        };
        String dir = System.getenv("LAPUTA_I18N");
        String lang = System.getenv("LAPUTA_LANG") == null ? "ru" : System.getenv("LAPUTA_LANG");
        final T tr = new T(lang, dict(dir, lang), dict(dir, "en"), dict(dir, "ru"));
        Checker.Alerts alerts = new Checker.Alerts() {
            public void show(int id, String ch, String title, String text, String node, boolean quiet, JSONObject act) {
                System.out.println("ALERT|" + id + "|" + ch + "|" + title + "|" + text + "|" + node + "|" + quiet + "|" + (act == null ? "" : act.toString()));
            }
            public T tr() { return tr; }
            public void status(String text, boolean bad) { System.out.println("STATUS|" + text + "|" + bad); }
            public boolean online() { return !offline; }
        };
        Checker.Http http = new Checker.Http() {
            public Net.Resp get(JSONObject hub, String path, int timeoutMs) throws Exception {
                return Net.request(hub.optString("url") + path, hub.optString("pin"), hub.optString("token"), "GET", null, timeoutMs);
            }
        };
        if (System.getenv("LAPUTA_EV") != null) {   // compare event texts with the UI
            JSONArray evs = new JSONArray(new String(Files.readAllBytes(new File(System.getenv("LAPUTA_EV")).toPath()), "UTF-8"));
            for (int i = 0; i < evs.length(); i++) {
                String[] x = tr.ev(evs.getJSONObject(i));
                System.out.println(x[0] + "\t" + x[1]);
            }
            return;
        }
        if (System.getenv("LAPUTA_STATUS_ONLY") != null) {   // status line text check without network
            System.out.println("SUMMARY|" + Checker.summaryText(tr, new JSONObject(System.getenv("LAPUTA_STATUS_ONLY"))));
            return;
        }
        long t0 = System.currentTimeMillis();
        boolean ok = new Checker(http, alerts, kv).pass(hubs, wait);
        System.out.println("RESULT|" + ok + "|" + (System.currentTimeMillis() - t0));
    }

    static JSONObject dict(String dir, String lang) {
        try { return new JSONObject(new String(Files.readAllBytes(new File(dir, lang + ".json").toPath()), "UTF-8")); } catch (Exception e) { return new JSONObject(); }
    }
}
