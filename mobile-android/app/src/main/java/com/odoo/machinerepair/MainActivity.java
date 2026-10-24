package com.odoo.machinerepair;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.PowerManager;
import android.provider.Settings;
import android.view.KeyEvent;
import android.view.View;
import android.view.Window;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.GeolocationPermissions;
import android.webkit.JavascriptInterface;
import android.webkit.PermissionRequest;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.ArrayList;
import java.util.List;

/**
 * 机台维修 App —— 轻量原生壳。
 *
 * 设计取舍：
 *  - 界面仍然复用服务器上的 PWA（一处修改、手机立即生效），但装在手机里当 App 用：
 *    有图标、全屏、**拿到原生摄像头权限**（WebView 里 getUserMedia 也能用）。
 *  - 不引入任何第三方库（不依赖 androidx），因此可以用 aapt2 + javac + d8 直接打包，
 *    不需要 Gradle / Android Studio / 在线仓库。
 *  - 拍照用 <input type=file capture> 走系统相机，同样无需额外权限代码。
 */
public class MainActivity extends Activity {

    private static final String START_URL = "https://df2026.213579.com/machine_repair/mobile";
    private static final int REQ_CAMERA = 1001;
    private static final int REQ_FILE = 1002;
    private static final int REQ_SCAN = 1003;
    private static final int REQ_CAPTURE = 1004;
    private static final int REQ_LOCATION = 1005;
    private static final int REQ_STARTUP_PERMISSIONS = 1006;

    private WebView webView;
    private ProgressBar progress;
    private TextView errorBox;
    private ValueCallback<Uri[]> fileCallback;
    private GeolocationPermissions.Callback geoCallback;
    private String geoOrigin;
    private boolean startupPermissionPending = false;
    private boolean appPageLoaded = false;
    private boolean cookieWarningShown = false;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        requestWindowFeature(Window.FEATURE_NO_TITLE);
        getWindow().setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE);

        FrameLayout root = new FrameLayout(this);
        webView = new WebView(this);
        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progress.setMax(100);
        errorBox = new TextView(this);
        errorBox.setVisibility(View.GONE);
        errorBox.setPadding(40, 120, 40, 40);
        errorBox.setTextSize(16);
        errorBox.setText("打不开服务器。请检查网络后重试。\n\n" + START_URL);

        root.addView(webView, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));
        root.addView(progress, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, 8));
        root.addView(errorBox, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));
        setContentView(root);

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        // 网页里的 navigator.geolocation（位置上报）必须开启，否则页面会一直拿不到定位
        settings.setGeolocationEnabled(true);
        settings.setCacheMode(WebSettings.LOAD_DEFAULT);
        settings.setUserAgentString(settings.getUserAgentString() + " MachineRepairApp/1.0");

        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, true);

        // 原生扫码桥：网页里判断 window.AndroidScanner 存在就用原生相机
        webView.addJavascriptInterface(new ScanBridge(), "AndroidScanner");

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                progress.setVisibility(View.GONE);
                errorBox.setVisibility(View.GONE);
                handlePageFinished(url);
            }

            @Override
            public void onReceivedError(WebView view, int errorCode, String description, String failingUrl) {
                errorBox.setVisibility(View.VISIBLE);
            }
        });

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onPermissionRequest(final PermissionRequest request) {
                // 自有业务系统：直接授予摄像头/麦克风，避免"无法请求授权"
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        request.grant(request.getResources());
                    }
                });
            }

            /**
             * 网页 navigator.geolocation 的授权入口。
             *
             * 只信任我们自己的域名；用户还没给系统定位权限时先弹系统授权框，
             * 授权结果回来后再用 callback 通知网页（不能在这里假装同意）。
             */
            @Override
            public void onGeolocationPermissionsShowPrompt(String origin,
                                                           GeolocationPermissions.Callback callback) {
                if (origin == null) {
                    callback.invoke(origin, false, false);
                    return;
                }
                boolean ours = origin.startsWith("https://df2026.213579.com")
                        || origin.startsWith("http://127.0.0.1");
                if (!ours) {
                    callback.invoke(origin, false, false);
                    return;
                }
                if (hasLocationPermission()) {
                    callback.invoke(origin, true, false);
                    return;
                }
                // 启动时的权限对话框还在等用户点击：先记住网页的请求，
                // 结果回来后统一回调，避免同时弹两个权限框。
                if (startupPermissionPending) {
                    geoCallback = callback;
                    geoOrigin = origin;
                    return;
                }
                geoCallback = callback;
                geoOrigin = origin;
                requestPermissions(new String[]{
                        "android.permission.ACCESS_FINE_LOCATION",
                        "android.permission.ACCESS_COARSE_LOCATION"}, REQ_LOCATION);
            }

            @Override
            public void onProgressChanged(WebView view, int newProgress) {
                progress.setProgress(newProgress);
                progress.setVisibility(newProgress >= 100 ? View.GONE : View.VISIBLE);
            }

            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback,
                                             FileChooserParams params) {
                fileCallback = callback;
                try {
                    Intent intent = params.createIntent();
                    // 视频分两种入口：
                    //   1) <input type=file capture>  -> 直接开系统相机录制（限 5 分钟）
                    //   2) <input type=file>（不带 capture）-> 保持系统文件选择器，可以选手机里已有的视频
                    // 以前不看 isCaptureEnabled()，只要 accept 是 video 就强行改成录制，
                    // 所以「从相册上传」永远打不开 —— 这是本次修复的关键。
                    boolean wantsVideo = false;
                    if (params.getAcceptTypes() != null) {
                        for (String type : params.getAcceptTypes()) {
                            if (type != null && type.startsWith("video")) { wantsVideo = true; }
                        }
                    }
                    if (wantsVideo && params.isCaptureEnabled()) {
                        Intent record = new Intent(android.provider.MediaStore.ACTION_VIDEO_CAPTURE);
                        record.putExtra(android.provider.MediaStore.EXTRA_DURATION_LIMIT, 300);
                        record.putExtra(android.provider.MediaStore.EXTRA_VIDEO_QUALITY, 0);
                        if (record.resolveActivity(getPackageManager()) != null) {
                            intent = record;
                        }
                    }
                    startActivityForResult(intent, REQ_FILE);
                    return true;
                } catch (Exception e) {
                    fileCallback = null;
                    return false;
                }
            }
        });

        // 首次启动像摄像头一样自动弹系统权限菜单：相机 + 定位（+ 通知）。
        // 定位用于「是否在工作位置」；通知用于后台上报的常驻提示。
        List<String> missing = new ArrayList<>();
        if (!hasCameraPermission()) {
            missing.add("android.permission.CAMERA");
        }
        if (!hasLocationPermission()) {
            missing.add("android.permission.ACCESS_FINE_LOCATION");
            missing.add("android.permission.ACCESS_COARSE_LOCATION");
        }
        if (Build.VERSION.SDK_INT >= 33 && !hasNotificationPermission()) {
            missing.add("android.permission.POST_NOTIFICATIONS");
        }
        if (!missing.isEmpty()) {
            startupPermissionPending = true;
            requestPermissions(missing.toArray(new String[0]), REQ_STARTUP_PERMISSIONS);
        }
        webView.loadUrl(START_URL);
    }

    private boolean hasCameraPermission() {
        return checkSelfPermission("android.permission.CAMERA") == PackageManager.PERMISSION_GRANTED;
    }

    private boolean hasLocationPermission() {
        return checkSelfPermission("android.permission.ACCESS_FINE_LOCATION")
                == PackageManager.PERMISSION_GRANTED
                || checkSelfPermission("android.permission.ACCESS_COARSE_LOCATION")
                == PackageManager.PERMISSION_GRANTED;
    }

    private boolean hasBackgroundLocation() {
        if (Build.VERSION.SDK_INT < 29) {
            return true;
        }
        return checkSelfPermission("android.permission.ACCESS_BACKGROUND_LOCATION")
                == PackageManager.PERMISSION_GRANTED;
    }

    private boolean hasNotificationPermission() {
        if (Build.VERSION.SDK_INT < 33) {
            return true;
        }
        return checkSelfPermission("android.permission.POST_NOTIFICATIONS")
                == PackageManager.PERMISSION_GRANTED;
    }

    // ------------------------------------------------------------ 后台上报
    private void handlePageFinished(String url) {
        if (url == null) {
            return;
        }
        if (url.contains("/machine_repair/mobile")) {
            appPageLoaded = true;
            saveLoginCookie();
            String cookie = getSharedPreferences(LocationService.PREFS, MODE_PRIVATE)
                    .getString(LocationService.PREF_COOKIE, "");
            if (cookie.isEmpty() && !cookieWarningShown) {
                cookieWarningShown = true;
                Toast.makeText(this, "未取到登录状态，后台上报未启动；请退出重新登录一次。",
                        Toast.LENGTH_LONG).show();
                // Cookie 偶尔比 onPageFinished 晚一点落盘，1.5 秒后再补一次
                if (webView != null) {
                    webView.postDelayed(() -> {
                        saveLoginCookie();
                        startLocationService();
                    }, 1500);
                }
            }
            // 在 App 仍在前台时启动前台服务：避开 Android 12+ 的「后台不能启动前台服务」限制。
            // 服务启动后一直运行，App 关闭/锁屏都继续上报；网页 JS 在 App 里会跳过上报，避免双份。
            startLocationService();
            maybePromptBackgroundLocation();
            maybePromptBatteryOptimization();
        } else if (url.contains("/web/login") || url.contains("/web/session/logout")) {
            appPageLoaded = false;
            cookieWarningShown = false;
            stopLocationService();
            getSharedPreferences(LocationService.PREFS, MODE_PRIVATE)
                    .edit().remove(LocationService.PREF_COOKIE).apply();
        }
    }

    private void saveLoginCookie() {
        String cookie = CookieManager.getInstance().getCookie(START_URL);
        if (cookie != null && cookie.contains("session_id=")) {
            getSharedPreferences(LocationService.PREFS, MODE_PRIVATE)
                    .edit().putString(LocationService.PREF_COOKIE, cookie).apply();
        }
    }

    private void startLocationService() {
        if (!appPageLoaded || !hasLocationPermission()) {
            return;
        }
        String cookie = getSharedPreferences(LocationService.PREFS, MODE_PRIVATE)
                .getString(LocationService.PREF_COOKIE, "");
        if (cookie.isEmpty()) {
            return;
        }
        try {
            Intent service = new Intent(this, LocationService.class);
            if (Build.VERSION.SDK_INT >= 26) {
                startForegroundService(service);
            } else {
                startService(service);
            }
        } catch (Exception e) {
            android.util.Log.w("MR-Main", "start location service failed", e);
        }
    }

    private void stopLocationService() {
        try {
            stopService(new Intent(this, LocationService.class));
        } catch (Exception ignored) {
        }
    }

    private void maybePromptBackgroundLocation() {
        if (Build.VERSION.SDK_INT < 29 || !hasLocationPermission()
                || hasBackgroundLocation()) {
            return;
        }
        SharedPreferences prefs = getSharedPreferences(LocationService.PREFS, MODE_PRIVATE);
        long last = prefs.getLong("bg_prompt_at", 0L);
        if (System.currentTimeMillis() - last < 24L * 3600L * 1000L) {
            return;
        }
        prefs.edit().putLong("bg_prompt_at", System.currentTimeMillis()).apply();
        new AlertDialog.Builder(this)
                .setTitle("允许后台定位")
                .setMessage("为了在关闭 App 后仍能上报位置，请把「位置」权限改为「始终允许」。\n\n"
                        + "设置 → 应用 → 机台维修 → 权限 → 位置 → 始终允许。\n\n"
                        + "如果手机有「自启动 / 后台运行」开关，也请允许，"
                        + "否则系统可能结束后台上报。")
                .setPositiveButton("去设置", (dialog, which) -> openAppDetailsSettings())
                .setNegativeButton("稍后", null)
                .show();
    }

    private boolean isIgnoringBatteryOptimizations() {
        if (Build.VERSION.SDK_INT < 23) {
            return true;
        }
        try {
            PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
            return pm != null && pm.isIgnoringBatteryOptimizations(getPackageName());
        } catch (Exception e) {
            return true;
        }
    }

    private void maybePromptBatteryOptimization() {
        if (Build.VERSION.SDK_INT < 23 || !appPageLoaded || !hasLocationPermission()
                || !hasBackgroundLocation() || isIgnoringBatteryOptimizations()) {
            return;
        }
        SharedPreferences prefs = getSharedPreferences(LocationService.PREFS, MODE_PRIVATE);
        long last = prefs.getLong("battery_prompt_at", 0L);
        if (System.currentTimeMillis() - last < 24L * 3600L * 1000L) {
            return;
        }
        prefs.edit().putLong("battery_prompt_at", System.currentTimeMillis()).apply();
        new AlertDialog.Builder(this)
                .setTitle("允许后台运行")
                .setMessage("手机省电策略可能会在关闭 App 后杀掉后台上报。\n\n"
                        + "请允许「机台维修」忽略电池优化；小米/华为/OPPO/vivo 还要在"
                        + "「应用启动管理」里允许自启动和后台活动。")
                .setPositiveButton("去设置", (dialog, which) -> openBatterySettings())
                .setNegativeButton("稍后", null)
                .show();
    }

    private void openBatterySettings() {
        try {
            Intent intent = new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                    Uri.parse("package:" + getPackageName()));
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(intent);
        } catch (Exception e) {
            openAppDetailsSettings();
        }
    }

    private void openAppDetailsSettings() {
        try {
            Intent intent = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                    Uri.parse("package:" + getPackageName()));
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(intent);
        } catch (Exception e) {
            Toast.makeText(this, "打不开设置，请手动到系统设置里允许定位。",
                    Toast.LENGTH_LONG).show();
        }
    }

    // ------------------------------------------------------------------ 原生扫码
    /** 暴露给网页的桥：window.AndroidScanner.scan() / .scanSn() */
    public class ScanBridge {
        @JavascriptInterface
        public boolean available() { return true; }

        /** v1.13+：告诉网页「位置由原生前台服务负责」，网页 JS 不再重复上报。 */
        @JavascriptInterface
        public boolean nativeLocation() { return true; }

        @JavascriptInterface
        public String version() { return "native-1.15"; }

        /** 视频转发：下载到缓存 -> content:// 临时授权 -> 系统「分享/转发」面板（微信、QQ 都在里面）。 */
        @JavascriptInterface
        public void shareVideo(final String url, final String filename) {
            shareVideoAsync(url, filename);
        }

        @JavascriptInterface
        public void scan() { startScan("qr"); }

        @JavascriptInterface
        public void scanSn() { startScan("sn"); }

        @JavascriptInterface
        public void takePhoto(String kind) { startCapture(kind); }
    }

    private String scanMode = "qr";

    private void startScan(final String mode) {
        if (!hasCameraPermission()) {
            runOnUiThread(() -> {
                Toast.makeText(this, "请先允许摄像头权限，再扫码", Toast.LENGTH_LONG).show();
                requestPermissions(new String[]{"android.permission.CAMERA"}, REQ_CAMERA);
            });
            return;
        }
        scanMode = mode;
        // 连续扫码：命中就实时回传到页面，扫码窗口保持打开
        ScanActivity.setListener(text -> deliverScanResult(text));
        runOnUiThread(() -> {
            try {
                Intent intent = new Intent(MainActivity.this, ScanActivity.class);
                intent.putExtra(ScanActivity.EXTRA_MODE, mode);
                startActivityForResult(intent, REQ_SCAN);
            } catch (Exception e) {
                Toast.makeText(this, "打不开扫码页：" + e.getMessage(), Toast.LENGTH_LONG).show();
            }
        });
    }

    private void startCapture(final String kind) {
        if (!hasCameraPermission()) {
            runOnUiThread(() -> {
                Toast.makeText(this, "请先允许摄像头权限", Toast.LENGTH_LONG).show();
                requestPermissions(new String[]{"android.permission.CAMERA"}, REQ_CAMERA);
            });
            return;
        }
        runOnUiThread(() -> {
            CaptureActivity.setListener((k, dataUrl) -> {
                if (webView == null || dataUrl == null) { return; }
                String js = "window.onNativePhoto && window.onNativePhoto("
                        + jsString(k) + "," + jsString(dataUrl) + ");";
                webView.evaluateJavascript(js, null);
            });
            try {
                Intent intent = new Intent(MainActivity.this, CaptureActivity.class);
                intent.putExtra(CaptureActivity.EXTRA_KIND, kind == null ? "front" : kind);
                startActivityForResult(intent, REQ_CAPTURE);
            } catch (Exception e) {
                Toast.makeText(this, "打不开拍照页：" + e.getMessage(), Toast.LENGTH_LONG).show();
            }
        });
    }

    private void deliverScanResult(String text) {
        if (webView == null || text == null) { return; }
        String fn = "sn".equals(scanMode) ? "onNativeScanSn" : "onNativeScan";
        String js = "window." + fn + " && window." + fn + "(" + jsString(text) + ");";
        webView.evaluateJavascript(js, null);
    }

    // ------------------------------------------------------------------ 视频转发
    /**
     * 把服务器上的视频下载到缓存，再交给系统「分享/转发」面板。
     *
     * 为什么不在网页里用 navigator.share：Android WebView 里没有这个 API，
     * 微信内置浏览器也没有；而 App 自己发 ACTION_SEND 才能真正把视频文件
     * 转发给微信 / QQ（不是发一条链接）。
     *
     * 视频 URL 需要登录态，所以下载时带上 WebView 的 Cookie。
     */
    private void shareVideoAsync(final String url, final String filename) {
        if (url == null || url.isEmpty()) {
            notifyWebShare(false, "视频地址为空");
            return;
        }
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    File file = downloadToCache(url, filename);
                    Uri uri = ShareFileProvider.uriFor(file);
                    Intent send = new Intent(Intent.ACTION_SEND);
                    send.setType("video/mp4");
                    send.putExtra(Intent.EXTRA_STREAM, uri);
                    send.putExtra(Intent.EXTRA_SUBJECT, "机台维修视频");
                    send.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                    final Intent chooser = Intent.createChooser(send, "转发维修视频到微信 / QQ");
                    chooser.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                    chooser.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            try {
                                startActivity(chooser);
                                notifyWebShare(true, "已打开转发面板，选微信 / QQ 即可");
                            } catch (Exception e) {
                                notifyWebShare(false, "打不开发送面板：" + e.getMessage());
                            }
                        }
                    });
                } catch (Exception e) {
                    notifyWebShare(false, shortError(e));
                }
            }
        }).start();
    }

    private static String shortError(Exception e) {
        String msg = e.getMessage();
        if (msg == null) { msg = e.getClass().getSimpleName(); }
        return msg.length() > 90 ? msg.substring(0, 90) : msg;
    }

    private void notifyWebShare(final boolean ok, final String message) {
        if (webView == null) { return; }
        final String js = "window.onNativeShare && window.onNativeShare(" + ok + ","
                + jsString(message == null ? "" : message) + ");";
        runOnUiThread(new Runnable() {
            @Override
            public void run() {
                if (webView != null) { webView.evaluateJavascript(js, null); }
            }
        });
    }

    private File downloadToCache(String url, String filename) throws IOException {
        File dir = new File(getCacheDir(), "share");
        if (!dir.exists() && !dir.mkdirs()) {
            throw new IOException("无法创建缓存目录");
        }
        File out = new File(dir, safeFileName(filename));
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        conn.setConnectTimeout(15000);
        conn.setReadTimeout(120000);
        conn.setInstanceFollowRedirects(true);
        String cookie = CookieManager.getInstance().getCookie(url);
        if (cookie != null) { conn.setRequestProperty("Cookie", cookie); }
        conn.setRequestProperty("User-Agent", "MachineRepairApp/1.15");
        try {
            int code = conn.getResponseCode();
            if (code != 200) { throw new IOException("服务器返回 HTTP " + code); }
            // Content-Length 用来算百分比；服务器没给（chunked）就退化成“已下载多少 MB”
            long contentLength = conn.getContentLengthLong();
            long total = 0;
            int lastPercent = -1;
            long lastNotify = 0;
            notifyWebShareProgress(0);
            InputStream in = conn.getInputStream();
            FileOutputStream fos = new FileOutputStream(out);
            try {
                byte[] buf = new byte[64 * 1024];
                int n;
                while ((n = in.read(buf)) > 0) {
                    total += n;
                    if (total > 300L * 1024 * 1024) {
                        throw new IOException("视频超过 300MB，建议先压缩");
                    }
                    fos.write(buf, 0, n);
                    long now = System.currentTimeMillis();
                    // 每 1% 或每 300ms 推一次，避免把 JS 桥打爆
                    if (now - lastNotify >= 300) {
                        int percent = contentLength > 0
                                ? (int) Math.min(99, total * 100 / contentLength)
                                : -1;
                        if (percent >= 0 && percent != lastPercent) {
                            lastPercent = percent;
                            lastNotify = now;
                            notifyWebShareProgress(percent);
                        } else if (percent < 0) {
                            lastNotify = now;
                            notifyWebShareProgress(-1);
                        }
                    }
                }
            } finally {
                try { fos.close(); } catch (Exception ignored) { }
                try { in.close(); } catch (Exception ignored) { }
            }
            if (out.length() == 0) { throw new IOException("下载到的文件是空的"); }
            notifyWebShareProgress(100);
        } finally {
            conn.disconnect();
        }
        return out;
    }

    /** 把下载进度推回网页（0–100；-1 表示总大小未知，页面显示“下载中…”）。 */
    private void notifyWebShareProgress(final int percent) {
        if (webView == null) { return; }
        final String js = "window.onNativeShareProgress && window.onNativeShareProgress("
                + percent + ");";
        runOnUiThread(new Runnable() {
            @Override
            public void run() {
                if (webView != null) { webView.evaluateJavascript(js, null); }
            }
        });
    }

    /** 文件名里不能有路径分隔符/引号之类，中文保留。 */
    private static String safeFileName(String name) {
        String base = (name == null || name.trim().isEmpty()) ? "repair.mp4" : name.trim();
        base = base.replaceAll("[\\\\/:*?\"<>|\\s]", "_");
        if (base.length() > 80) { base = base.substring(base.length() - 80); }
        if (!base.contains(".")) { base = base + ".mp4"; }
        return base;
    }

    private static String jsString(String s) {
        StringBuilder sb = new StringBuilder("\"");
        for (char c : s.toCharArray()) {
            switch (c) {
                case '"': sb.append("\\\""); break;
                case '\\': sb.append("\\\\"); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                default:
                    if (c < 0x20) { sb.append(String.format("\\u%04x", (int) c)); }
                    else { sb.append(c); }
            }
        }
        return sb.append("\"").toString();
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == REQ_CAMERA && (grantResults.length == 0
                || grantResults[0] != PackageManager.PERMISSION_GRANTED)) {
            Toast.makeText(this, "没有摄像头权限，扫码功能不可用；可到系统设置里开启。", Toast.LENGTH_LONG).show();
        }
        if (requestCode == REQ_STARTUP_PERMISSIONS) {
            startupPermissionPending = false;
            if (!hasCameraPermission()) {
                Toast.makeText(this, "没有摄像头权限，扫码功能不可用；可到系统设置里开启。",
                        Toast.LENGTH_LONG).show();
            }
            if (!hasLocationPermission()) {
                Toast.makeText(this, "没有定位权限，无法自动上报工作位置；"
                        + "可到系统设置 → 应用 → 机台维修 → 权限 里开启位置。", Toast.LENGTH_LONG).show();
            }
            // 网页在权限框弹出期间可能已经请求过定位，把结果补给它
            if (geoCallback != null) {
                geoCallback.invoke(geoOrigin, hasLocationPermission(), false);
                geoCallback = null;
                geoOrigin = null;
            }
            if (hasLocationPermission() && appPageLoaded) {
                startLocationService();
                maybePromptBackgroundLocation();
                maybePromptBatteryOptimization();
            }
        }
        if (requestCode == REQ_LOCATION) {
            boolean granted = false;
            for (int result : grantResults) {
                if (result == PackageManager.PERMISSION_GRANTED) { granted = true; break; }
            }
            if (geoCallback != null) {
                geoCallback.invoke(geoOrigin, granted, false);
                geoCallback = null;
                geoOrigin = null;
            }
            if (!granted) {
                Toast.makeText(this, "没有定位权限，无法判断是否在工作位置；"
                        + "可到系统设置 → 应用 → 机台维修 → 权限 里开启位置。", Toast.LENGTH_LONG).show();
            }
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode == REQ_CAPTURE) {
            CaptureActivity.setListener(null);
            return;
        }
        if (requestCode == REQ_SCAN) {
            ScanActivity.setListener(null);
            if (resultCode == RESULT_OK && data != null) {
                String text = data.getStringExtra(ScanActivity.EXTRA_RESULT);
                if (text != null) { deliverScanResult(text); }
            }
            scanMode = "qr";
            return;
        }
        if (requestCode == REQ_FILE) {
            if (fileCallback != null) {
                fileCallback.onReceiveValue(
                        WebChromeClient.FileChooserParams.parseResult(resultCode, data));
                fileCallback = null;
            }
            return;
        }
        super.onActivityResult(requestCode, resultCode, data);
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK && webView != null && webView.canGoBack()) {
            webView.goBack();
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }

    @Override
    protected void onPause() {
        super.onPause();
        if (webView != null) {
            webView.onPause();
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (webView != null) {
            webView.onResume();
        }
        // 前台再补一次启动：万一页面加载时权限/登录态还没准备好
        startLocationService();
        maybePromptBatteryOptimization();
    }

    @Override
    protected void onDestroy() {
        if (webView != null) {
            webView.destroy();
            webView = null;
        }
        super.onDestroy();
    }
}
