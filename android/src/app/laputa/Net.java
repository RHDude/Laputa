package app.laputa;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.URL;
import java.security.MessageDigest;
import java.security.cert.CertificateException;
import java.security.cert.X509Certificate;
import java.util.HashMap;

import javax.net.ssl.HostnameVerifier;
import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLSession;
import javax.net.ssl.SSLSocketFactory;
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509TrustManager;

/** HTTPS to a watchman. Trust comes from its certificate fingerprint, not from CAs. */
public final class Net {
    public static final class Resp {
        public final int status;
        public final String body;

        Resp(int status, String body) {
            this.status = status;
            this.body = body;
        }
    }

    private static final HashMap<String, SSLSocketFactory> FACTORIES = new HashMap<String, SSLSocketFactory>();
    private static final HostnameVerifier ANY_HOST = new HostnameVerifier() {
        @Override
        public boolean verify(String hostname, SSLSession session) {
            return true; // the host name does not matter: the certificate is checked by fingerprint
        }
    };

    private Net() {
    }

    static String hex(byte[] b) {
        StringBuilder sb = new StringBuilder(b.length * 2);
        for (byte x : b) {
            sb.append(Character.forDigit((x >> 4) & 15, 16)).append(Character.forDigit(x & 15, 16));
        }
        return sb.toString();
    }

    private static final class Pinned implements X509TrustManager {
        private final String pin;

        Pinned(String pin) {
            this.pin = pin;
        }

        @Override
        public void checkClientTrusted(X509Certificate[] chain, String authType) throws CertificateException {
            throw new CertificateException("not used");
        }

        @Override
        public void checkServerTrusted(X509Certificate[] chain, String authType) throws CertificateException {
            if (chain == null || chain.length == 0) {
                throw new CertificateException("j.net.nocert");
            }
            String got;
            try {
                got = hex(MessageDigest.getInstance("SHA-256").digest(chain[0].getEncoded()));
            } catch (Exception e) {
                throw new CertificateException(e);
            }
            if (!MessageDigest.isEqual(got.getBytes(), pin.getBytes())) {
                throw new CertificateException("j.net.pin");
            }
        }

        @Override
        public X509Certificate[] getAcceptedIssuers() {
            return new X509Certificate[0];
        }
    }

    private static synchronized SSLSocketFactory factory(String pin) throws IOException {
        SSLSocketFactory f = FACTORIES.get(pin);
        if (f == null) {
            try {
                SSLContext ctx = SSLContext.getInstance("TLS");
                ctx.init(null, new TrustManager[]{new Pinned(pin)}, null);
                f = ctx.getSocketFactory();
            } catch (Exception e) {
                throw new IOException(e);
            }
            FACTORIES.put(pin, f);
        }
        return f;
    }

    public static Resp request(String url, String pin, String token, String method, String body, int timeoutMs) throws IOException {
        if (url == null || !url.startsWith("https://")) {
            throw new IOException("j.net.https");
        }
        if (pin == null || pin.length() != 64) {
            throw new IOException("j.net.nopin");
        }
        HttpsURLConnection c = (HttpsURLConnection) new URL(url).openConnection();
        try {
            c.setSSLSocketFactory(factory(pin.toLowerCase()));
            c.setHostnameVerifier(ANY_HOST);
            c.setConnectTimeout(Math.min(timeoutMs, 12000));
            c.setReadTimeout(timeoutMs);
            c.setUseCaches(false);
            c.setInstanceFollowRedirects(false);
            c.setRequestMethod(method == null ? "GET" : method);
            c.setRequestProperty("Authorization", "Bearer " + token);
            c.setRequestProperty("Connection", "close");
            if (body != null && body.length() > 0) {
                byte[] b = body.getBytes("UTF-8");
                c.setDoOutput(true);
                c.setRequestProperty("Content-Type", "application/json");
                c.setFixedLengthStreamingMode(b.length);
                OutputStream out = c.getOutputStream();
                out.write(b);
                out.close();
            }
            int code = c.getResponseCode();
            InputStream in = code >= 400 ? c.getErrorStream() : c.getInputStream();
            ByteArrayOutputStream buf = new ByteArrayOutputStream();
            if (in != null) {
                byte[] chunk = new byte[8192];
                int n;
                while ((n = in.read(chunk)) > 0 && buf.size() < 4 * 1024 * 1024) {
                    buf.write(chunk, 0, n);
                }
                in.close();
            }
            return new Resp(code, buf.toString("UTF-8"));
        } finally {
            c.disconnect();
        }
    }
}
