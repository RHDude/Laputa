package app.laputa;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.Calendar;

/**
 * Тексты на языке пользователя. Словари общие с интерфейсом (assets/ui/i18n/*.json),
 * правила сборки событий повторяют ui/src/i18n.js. Без Android: годится и для проверок на JVM.
 */
public final class T {
    public static final String[] LANGS = {"ru", "en", "uk", "de", "es", "zh"};

    public final String lang;
    private final JSONObject d;
    private final JSONObject en;
    private final JSONObject ru;

    public T(String lang, JSONObject d, JSONObject en, JSONObject ru) {
        this.lang = lang;
        this.d = d == null ? new JSONObject() : d;
        this.en = en == null ? new JSONObject() : en;
        this.ru = ru == null ? new JSONObject() : ru;
    }

    /** Язык из настроек приложения или системы; неизвестный — английский. */
    public static String pick(String pref, String sys) {
        if (pref != null) for (String l : LANGS) if (l.equals(pref)) return l;
        String s = sys == null ? "" : sys.toLowerCase();
        if (s.length() > 2) s = s.substring(0, 2);
        for (String l : LANGS) if (l.equals(s)) return l;
        return "be".equals(s) || "kk".equals(s) ? "ru" : "en";
    }

    private Object raw(String key) {
        Object v = d.opt(key);
        if (v == null) v = en.opt(key);
        if (v == null) v = ru.opt(key);
        return v;
    }

    public boolean has(String key) {
        return raw(key) != null;
    }

    String plural(long n) {
        if ("ru".equals(lang) || "uk".equals(lang)) {
            long a = Math.abs(n) % 100, b = a % 10;
            if (b == 1 && a != 11) return "one";
            if (b >= 2 && b <= 4 && (a < 12 || a > 14)) return "few";
            return "many";
        }
        if ("zh".equals(lang)) return "other";
        return n == 1 ? "one" : "other";
    }

    static String str(Object v) {
        if (v == null || v == JSONObject.NULL) return "";
        if (v instanceof Double || v instanceof Float) {
            double x = ((Number) v).doubleValue();
            if (x == Math.rint(x)) return String.valueOf((long) x);
        }
        return String.valueOf(v);
    }

    public String t(String key, JSONObject p) {
        Object v = raw(key);
        if (v == null) return key;
        String s;
        if (v instanceof JSONObject) {
            JSONObject forms = (JSONObject) v;
            long n = p == null ? 0 : p.optLong("n", 0);
            s = forms.optString(plural(n), "");
            if (s.length() == 0) s = forms.optString("other", forms.optString("many", ""));
        } else {
            s = String.valueOf(v);
        }
        if (p == null) return s;
        StringBuilder out = new StringBuilder();
        int i = 0;
        while (i < s.length()) {
            char c = s.charAt(i);
            int close = c == '{' ? s.indexOf('}', i) : -1;
            if (close > i) {
                out.append(str(p.opt(s.substring(i + 1, close))));
                i = close + 1;
            } else {
                out.append(c);
                i++;
            }
        }
        return out.toString();
    }

    public String t(String key) {
        return t(key, null);
    }

    /** t("j.act.muted", "name", "Aurora") */
    public String t(String key, String k1, Object v1) {
        JSONObject p = new JSONObject();
        try {
            p.put(k1, v1);
        } catch (Exception ignored) {
        }
        return t(key, p);
    }

    private String u(String key, long n) {
        return t(key, "n", n);
    }

    public String dur(long sec, boolean shortForm) {
        sec = Math.max(0, sec);
        String j = t("u.join");
        if (sec < 60) return u("u.s", sec);
        long m = sec / 60;
        if (m < 60) return u("u.min", m);
        long h = m / 60;
        if (h < 24) {
            long mm = m % 60;
            return shortForm || mm == 0 ? u("u.h", h) : u("u.h", h) + j + u("u.min", mm);
        }
        long dd = h / 24, hh = h % 24;
        return shortForm || hh == 0 ? u("u.d", dd) : u("u.d", dd) + j + u("u.h", hh);
    }

    public static String clock(long ms) {
        Calendar c = Calendar.getInstance();
        c.setTimeInMillis(ms);
        int h = c.get(Calendar.HOUR_OF_DAY), m = c.get(Calendar.MINUTE);
        return (h < 10 ? "0" : "") + h + ":" + (m < 10 ? "0" : "") + m;
    }

    private String join(JSONArray a) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < a.length(); i++) {
            if (i > 0) sb.append(t("u.comma"));
            sb.append(a.optString(i));
        }
        return sb.toString();
    }

    static boolean truthy(JSONObject p, String k) {
        Object v = p.opt(k);
        if (v == null || v == JSONObject.NULL) return false;
        if (v instanceof Boolean) return (Boolean) v;
        if (v instanceof Number) return ((Number) v).doubleValue() != 0;
        return String.valueOf(v).length() > 0;
    }

    static String variant(String type, JSONObject p) {
        if ("node_down".equals(type)) {
            if (truthy(p, "group")) return "group";
            if (truthy(p, "both")) return "both";
            return p.has("gone") && !p.isNull("gone") ? "gone" : "";
        }
        if ("node_up".equals(type)) {
            String why = p.optString("why", "");
            return "boot".equals(why) && truthy(p, "cause") ? "bootc" : why;
        }
        if ("svc_failed".equals(type)) return truthy(p, "restarts") ? "r" : "";
        if ("ssh_login".equals(type)) return truthy(p, "new") ? "new" : "";
        return "";
    }

    JSONObject params(JSONObject p) {
        JSONObject q = new JSONObject();
        try {
            JSONArray names = p.names();
            for (int i = 0; names != null && i < names.length(); i++) q.put(names.getString(i), p.get(names.getString(i)));
            for (String k : new String[]{"gone", "age", "every"}) {
                Object v = p.opt(k);
                if (v instanceof Number) q.put(k, dur(((Number) v).longValue(), false));
            }
            for (String k : new String[]{"names", "labels"}) {
                JSONArray a = p.optJSONArray(k);
                if (a != null) q.put(k, join(a));
            }
            if (truthy(p, "cause")) q.put("cause", t("cause." + p.optString("cause")));
            String why = p.optString("why", "");
            if (why.length() > 0 && has("maint.why." + why)) q.put("why", t("maint.why." + why));
            for (String k : new String[]{"n", "restarts", "hours"}) {
                if (p.has(k) && !p.isNull(k)) {
                    q.put("n", p.get(k));
                    break;
                }
            }
        } catch (Exception ignored) {
        }
        return q;
    }

    /** Заголовок и текст события или беды; без шаблона — русский текст сторожа. */
    public String[] ev(JSONObject e) {
        String type = e.optString("type", "");
        String base = "ev." + type + ".";
        JSONObject p = e.optJSONObject("p");
        if (p == null || type.length() == 0 || !has(base + "t")) return new String[]{e.optString("title"), e.optString("text")};
        String v = variant(type, p);
        String tk = v.length() > 0 && has(base + "t." + v) ? base + "t." + v : base + "t";
        String xk = v.length() > 0 && has(base + "x." + v) ? base + "x." + v : base + "x";
        JSONObject q = params(p);
        return new String[]{t(tk, q), has(xk) ? t(xk, q) : ""};
    }

    /** Ошибка из ответа агента или сторожа: {code, p, error}. */
    public String err(JSONObject r) {
        String code = r.optString("code", "");
        if (code.length() > 0 && has("err." + code)) {
            JSONObject p = r.optJSONObject("p");
            return t("err." + code, p == null ? new JSONObject() : p);
        }
        String e = r.optString("error", "");
        return e.length() > 0 ? e : t("err.generic");
    }

    /** Текст Java-исключения: ключи j.net.* заменяются переводом. */
    public String exc(Throwable x) {
        String m = x.getMessage() == null ? x.toString() : x.getMessage();
        java.util.regex.Matcher mt = java.util.regex.Pattern.compile("j\\.net\\.[a-z]+").matcher(m);
        return mt.find() ? t(mt.group()) : m;
    }
}
