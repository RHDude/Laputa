package app.laputa;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONArray;
import org.json.JSONObject;

/** App settings: watchmen with keys, alert mode, check state. */
public final class Store implements Checker.KV {
    private final SharedPreferences sp;

    public Store(Context c) {
        sp = c.getApplicationContext().getSharedPreferences("laputa", Context.MODE_PRIVATE);
    }

    @Override
    public String get(String key) {
        return sp.getString(key, "");
    }

    @Override
    public void put(String key, String value) {
        sp.edit().putString(key, value).apply();
    }

    public JSONObject cfg() {
        try {
            String s = sp.getString("cfg", "");
            if (s.length() > 0) return new JSONObject(s);
        } catch (Exception ignored) {
        }
        JSONObject o = new JSONObject();
        try {
            o.put("hubs", new JSONArray());
            o.put("settings", new JSONObject());
        } catch (Exception ignored) {
        }
        return o;
    }

    public void setCfg(JSONObject o) {
        sp.edit().putString("cfg", o.toString()).apply();
    }

    public JSONArray hubs() {
        JSONArray a = cfg().optJSONArray("hubs");
        return a == null ? new JSONArray() : a;
    }

    public String lang() {
        JSONObject s = cfg().optJSONObject("settings");
        return T.pick(s == null ? "" : s.optString("lang", ""), java.util.Locale.getDefault().getLanguage());
    }

    /** true: persistent connection to the watchman; false: check every 15 minutes. */
    public boolean live() {
        JSONObject s = cfg().optJSONObject("settings");
        return s == null || !"eco".equals(s.optString("mode", "live"));
    }
}
