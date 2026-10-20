package com.odoo.machinerepair;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Bundle;
import android.view.KeyEvent;
import android.view.View;
import android.view.Window;
import android.view.WindowManager;
import android.webkit.CookieManager;
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

    private WebView webView;
    private ProgressBar progress;
    private TextView errorBox;
    private ValueCallback<Uri[]> fileCallback;

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
                    // 视频：直接用系统的"录制"意图，并限制 5 分钟，少一步文件选择
                    boolean wantsVideo = false;
                    if (params.getAcceptTypes() != null) {
                        for (String type : params.getAcceptTypes()) {
                            if (type != null && type.startsWith("video")) { wantsVideo = true; }
                        }
                    }
                    if (wantsVideo) {
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

        if (!hasCameraPermission()) {
            requestPermissions(new String[]{"android.permission.CAMERA"}, REQ_CAMERA);
        }
        webView.loadUrl(START_URL);
    }

    private boolean hasCameraPermission() {
        return checkSelfPermission("android.permission.CAMERA") == PackageManager.PERMISSION_GRANTED;
    }

    // ------------------------------------------------------------------ 原生扫码
    /** 暴露给网页的桥：window.AndroidScanner.scan() / .scanSn() */
    public class ScanBridge {
        @JavascriptInterface
        public boolean available() { return true; }

        @JavascriptInterface
        public String version() { return "native-1"; }

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
