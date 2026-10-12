package app.laputa;

import android.content.Context;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/** Короткий журнал приложения в файле: виден в настройках, помогает разобраться, почему не пришла тревога. */
public final class L {
    private static final int MAX = 48 * 1024;
    private static File file;

    private L() {
    }

    public static synchronized void init(Context c) {
        if (file == null) file = new File(c.getApplicationContext().getFilesDir(), "log.txt");
    }

    public static synchronized void i(String msg) {
        android.util.Log.i("Laputa", msg);
        if (file == null) return;
        try {
            if (file.length() > MAX) {
                String old = read();
                FileOutputStream w = new FileOutputStream(file, false);
                w.write(old.substring(old.length() / 2).getBytes("UTF-8"));
                w.close();
            }
            FileOutputStream w = new FileOutputStream(file, true);
            String ts = new SimpleDateFormat("dd.MM HH:mm:ss", Locale.US).format(new Date());
            w.write((ts + "  " + msg + "\n").getBytes("UTF-8"));
            w.close();
        } catch (Exception ignored) {
        }
    }

    public static synchronized String read() {
        if (file == null || !file.exists()) return "";
        try {
            FileInputStream in = new FileInputStream(file);
            byte[] b = new byte[(int) Math.min(file.length(), MAX * 2)];
            int n = in.read(b);
            in.close();
            return n > 0 ? new String(b, 0, n, "UTF-8") : "";
        } catch (Exception e) {
            return "";
        }
    }
}
