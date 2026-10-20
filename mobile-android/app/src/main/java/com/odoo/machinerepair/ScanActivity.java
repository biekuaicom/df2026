package com.odoo.machinerepair;

import android.app.Activity;
import android.content.Intent;
import android.content.pm.ActivityInfo;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Rect;
import android.hardware.Camera;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.SurfaceHolder;
import android.view.SurfaceView;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.widget.FrameLayout;
import android.widget.TextView;

import com.google.zxing.BarcodeFormat;
import com.google.zxing.BinaryBitmap;
import com.google.zxing.DecodeHintType;
import com.google.zxing.MultiFormatReader;
import com.google.zxing.PlanarYUVLuminanceSource;
import com.google.zxing.qrcode.QRCodeReader;
import com.google.zxing.Result;
import com.google.zxing.common.HybridBinarizer;

import java.util.ArrayList;
import java.util.EnumMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * 原生连续扫码页（对齐快递 App 的做法）：
 *
 *  1. 连续扫码     —— 扫到就回传，不退出，镜头对上就能一直扫下一个
 *  2. 视频流逐帧   —— Camera 预览回调里逐帧解码，而不是"拍一张解一次"
 *  3. 独立解码线程 —— 单线程执行器 + 丢帧策略（忙就丢帧，不排队、不阻塞 UI）
 *  4. 对焦/曝光    —— 连续自动对焦 + 点按对焦 + 补光 + 曝光补偿
 *  5. 快速通道     —— 先只用 QRCodeReader（最快），不中再上 MultiFormatReader
 *
 * 结果回传：命中时通过 {@link Listener} 实时推给 WebView（窗口保持打开，可连续扫）。
 */
public class ScanActivity extends Activity implements SurfaceHolder.Callback,
        Camera.PreviewCallback, Camera.AutoFocusCallback {

    public static final String EXTRA_RESULT = "SCAN_RESULT";
    public static final String EXTRA_MODE = "SCAN_MODE";     // qr | sn
    private static final String TAG = "MRScan";

    /** 命中回调（由 MainActivity 注入，用于实时推给 WebView） */
    public interface Listener {
        void onScanned(String text);
    }

    private static volatile Listener sListener;

    public static void setListener(Listener listener) {
        sListener = listener;
    }

    private FrameLayout root;
    private SurfaceView preview;
    private OverlayView overlay;

    private Camera camera;
    private Camera.Size previewSize;
    private volatile boolean finished = false;
    private boolean torchOn = false;
    private String mode = "qr";

    private final Handler ui = new Handler(Looper.getMainLooper());
    private final ExecutorService decoder = Executors.newSingleThreadExecutor();
    private final AtomicBoolean busy = new AtomicBoolean(false);

    private final QRCodeReader qrReader = new QRCodeReader();
    private final MultiFormatReader multiReader = new MultiFormatReader();

    private volatile int frames = 0;
    private volatile int hits = 0;
    private String lastText = null;
    private byte[] rotatedBuf = null;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_PORTRAIT);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        String m = getIntent().getStringExtra(EXTRA_MODE);
        if (m != null) { mode = m; }
        buildHints();

        root = new FrameLayout(this);
        root.setBackgroundColor(Color.BLACK);

        preview = new SurfaceView(this);
        preview.getHolder().addCallback(this);
        root.addView(preview, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        overlay = new OverlayView();
        root.addView(overlay, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        TextView close = new TextView(this);
        close.setText("完成 ✕");
        close.setTextColor(Color.WHITE);
        close.setTextSize(17);
        close.setGravity(Gravity.CENTER);
        close.setPadding(30, 30, 30, 30);
        close.setOnClickListener(v -> finish());
        FrameLayout.LayoutParams cLP = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        cLP.gravity = Gravity.TOP | Gravity.END;
        root.addView(close, cLP);

        TextView torch = new TextView(this);
        torch.setText("灯");
        torch.setTextColor(Color.WHITE);
        torch.setTextSize(18);
        torch.setGravity(Gravity.CENTER);
        torch.setPadding(30, 30, 30, 30);
        torch.setOnClickListener(v -> toggleTorch());
        FrameLayout.LayoutParams tLP = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        tLP.gravity = Gravity.TOP | Gravity.START;
        root.addView(torch, tLP);

        setContentView(root);
    }

    private void buildHints() {
        Map<DecodeHintType, Object> hints = new EnumMap<>(DecodeHintType.class);
        hints.put(DecodeHintType.TRY_HARDER, Boolean.TRUE);
        List<BarcodeFormat> formats = new ArrayList<>();
        formats.add(BarcodeFormat.QR_CODE);
        formats.add(BarcodeFormat.CODE_128);   // 快递单主力
        formats.add(BarcodeFormat.CODE_39);
        formats.add(BarcodeFormat.CODE_93);
        formats.add(BarcodeFormat.CODABAR);
        formats.add(BarcodeFormat.EAN_13);
        formats.add(BarcodeFormat.EAN_8);
        formats.add(BarcodeFormat.UPC_A);
        hints.put(DecodeHintType.POSSIBLE_FORMATS, formats);
        hints.put(DecodeHintType.CHARACTER_SET, "UTF-8");
        multiReader.setHints(hints);
    }

    // ------------------------------------------------------------ Surface / Camera
    @Override
    public void surfaceCreated(SurfaceHolder holder) { openCamera(holder); }

    @Override
    public void surfaceChanged(SurfaceHolder holder, int format, int width, int height) {
        configureCamera(holder);
    }

    @Override
    public void surfaceDestroyed(SurfaceHolder holder) { releaseCamera(); }

    private void openCamera(SurfaceHolder holder) {
        try {
            camera = Camera.open();
        } catch (Exception e) {
            Log.e(TAG, "Camera.open failed", e);
            toast("摄像头打不开：" + e.getMessage());
            return;
        }
        if (camera == null) { toast("没有可用摄像头"); return; }
        configureCamera(holder);
    }

    private void configureCamera(SurfaceHolder holder) {
        if (camera == null) { return; }
        try {
            Camera.Parameters p = camera.getParameters();
            if (previewSize == null) { previewSize = choosePreviewSize(p); }
            p.setPreviewSize(previewSize.width, previewSize.height);
            p.setPreviewFormat(android.graphics.ImageFormat.NV21);
            applyFocusMode(p);
            applyExposure(p);
            camera.setParameters(p);
            camera.setDisplayOrientation(90);
            camera.setPreviewDisplay(holder);
            camera.setPreviewCallback(this);
            camera.startPreview();
        } catch (Exception e) {
            Log.e(TAG, "configureCamera", e);
            toast("相机配置失败：" + e.getMessage());
        }
    }

    private Camera.Size choosePreviewSize(Camera.Parameters p) {
        Camera.Size best = null;
        for (Camera.Size s : p.getSupportedPreviewSizes()) {
            if (s.width > 1600) { continue; }
            if (best == null) { best = s; continue; }
            int score = Math.abs(s.width - 1280) + Math.abs(s.height - 720);
            int bestScore = Math.abs(best.width - 1280) + Math.abs(best.height - 720);
            if (score < bestScore) { best = s; }
        }
        return best != null ? best : p.getPreviewSize();
    }

    private void applyFocusMode(Camera.Parameters p) {
        try {
            List<String> modes = p.getSupportedFocusModes();
            if (modes == null) { return; }
            if (modes.contains(Camera.Parameters.FOCUS_MODE_CONTINUOUS_PICTURE)) {
                p.setFocusMode(Camera.Parameters.FOCUS_MODE_CONTINUOUS_PICTURE);
            } else if (modes.contains(Camera.Parameters.FOCUS_MODE_CONTINUOUS_VIDEO)) {
                p.setFocusMode(Camera.Parameters.FOCUS_MODE_CONTINUOUS_VIDEO);
            } else if (modes.contains(Camera.Parameters.FOCUS_MODE_AUTO)) {
                p.setFocusMode(Camera.Parameters.FOCUS_MODE_AUTO);
            }
        } catch (Exception ignored) { }
    }

    private void applyExposure(Camera.Parameters p) {
        try {
            int min = p.getMinExposureCompensation();
            int max = p.getMaxExposureCompensation();
            if (min < 0 && max > 0) { p.setExposureCompensation(Math.min(max, 1)); }
        } catch (Exception ignored) { }
    }

    private void toggleTorch() {
        if (camera == null) { return; }
        try {
            Camera.Parameters p = camera.getParameters();
            List<String> modes = p.getSupportedFlashModes();
            if (modes == null) { toast("这台机器没有闪光灯"); return; }
            torchOn = !torchOn;
            p.setFlashMode(torchOn ? Camera.Parameters.FLASH_MODE_TORCH
                                   : Camera.Parameters.FLASH_MODE_OFF);
            camera.setParameters(p);
        } catch (Exception e) { Log.e(TAG, "torch", e); }
    }

    private void releaseCamera() {
        if (camera == null) { return; }
        try {
            camera.setPreviewCallback(null);
            camera.stopPreview();
        } catch (Exception ignored) { }
        try { camera.release(); } catch (Exception ignored) { }
        camera = null;
    }

    // ------------------------------------------------------------ 逐帧解码
    @Override
    public void onPreviewFrame(final byte[] data, Camera cam) {
        if (data == null || finished || previewSize == null) { return; }
        // 解码线程忙就丢帧 —— "快"的关键：不排队、不积压
        if (!busy.compareAndSet(false, true)) { return; }
        frames++;
        final int w = previewSize.width;
        final int h = previewSize.height;
        // 用整帧解码，**不要裁剪**：
        // 实测（见 .tools/zxingcheck）中央 80%x58% 的裁剪会把二维码的静区切掉，
        // 导致"取景框里明明对得很正却解不出来"。整帧 1280x720 解码实测只要 51~63ms。
        // 屏幕上的取景框只作为对位提示，不参与裁剪。
        final int cw = w;
        final int ch = h;
        final int left = 0;
        final int top = 0;

        decoder.execute(() -> {
            String text = null;
            try {
                // ① 整帧、原始方向：二维码快速通道
                BinaryBitmap raw = new BinaryBitmap(new HybridBinarizer(
                        new PlanarYUVLuminanceSource(data, w, h, 0, 0, w, h, false)));
                Result result = null;
                try { result = qrReader.decode(raw); } catch (Exception ignored) { }
                // ② 整帧全格式（横向摆放的一维码在这里命中）
                if (result == null) {
                    try { result = multiReader.decodeWithState(raw); } catch (Exception ignored) { }
                }
                // ③ 旋转 90° 再试 —— 竖着拿手机时一维码在缓冲区里是竖的，
                //    而 ZXing 的一维码只沿水平行扫描，不旋转就永远读不出。
                //    这是"二维码能扫、条形码很弱"的真正原因。
                if (result == null) {
                    byte[] rot = rotateY90(data, w, h);
                    BinaryBitmap rotated = new BinaryBitmap(new HybridBinarizer(
                            new PlanarYUVLuminanceSource(rot, h, w, 0, 0, h, w, false)));
                    try { result = multiReader.decodeWithState(rotated); } catch (Exception ignored) { }
                    if (result == null) {
                        try { result = qrReader.decode(rotated); } catch (Exception ignored) { }
                    }
                }
                if (result != null) { text = result.getText(); }
            } catch (Exception ignored) {
                // 常态：这一帧没读到
            } finally {
                busy.set(false);
            }
            final String found = text;
            ui.post(() -> {
                overlay.refresh(frames, hits, lastText);
                if (found != null && !found.isEmpty()) { onHit(found); }
            });
        });
    }

    /** 把 NV21 的 Y 平面旋转 90°（逆时针），用于读取竖向摆放的一维码 */
    private byte[] rotateY90(byte[] y, int w, int h) {
        if (rotatedBuf == null || rotatedBuf.length < w * h) {
            rotatedBuf = new byte[w * h];
        }
        for (int row = 0; row < h; row++) {
            int srcBase = row * w;
            int dstCol = h - 1 - row;
            for (int col = 0; col < w; col++) {
                rotatedBuf[col * h + dstCol] = y[srcBase + col];
            }
        }
        return rotatedBuf;
    }

    /** 单次扫描：命中即回传并退出（维修场景不需要连续累加） */
    private void onHit(String text) {
        if (finished) { return; }
        finished = true;
        hits++;
        lastText = text;
        overlay.banner("✓ 已识别：" + shorten(text) + "，正在返回…");
        Listener listener = sListener;
        if (listener != null) {
            try { listener.onScanned(text); } catch (Exception e) { Log.e(TAG, "listener", e); }
        }
        // 停掉预览省电，350ms 后带结果返回（让用户看清绿色横幅）
        try {
            if (camera != null) { camera.setPreviewCallback(null); camera.stopPreview(); }
        } catch (Exception ignored) { }
        ui.postDelayed(() -> {
            Intent out = new Intent();
            out.putExtra(EXTRA_RESULT, text);
            setResult(RESULT_OK, out);
            finish();
        }, 350);
    }

    private static String shorten(String text) {
        return text.length() <= 34 ? text : text.substring(0, 34) + "…";
    }

    // ------------------------------------------------------------ 点按对焦
    @Override
    public void onAutoFocus(boolean success, Camera cam) { }

    private void focusAt(float x, float y, int viewW, int viewH) {
        if (camera == null || viewW == 0 || viewH == 0) { return; }
        try {
            Camera.Parameters p = camera.getParameters();
            if (p.getMaxNumFocusAreas() <= 0) { return; }
            int cx = (int) (2000f * (x / viewW) - 1000f);
            int cy = (int) (2000f * (y / viewH) - 1000f);
            Rect area = new Rect(cx - 200, cy - 200, cx + 200, cy + 200);
            List<Camera.Area> areas = new ArrayList<>();
            areas.add(new Camera.Area(area, 800));
            p.setFocusAreas(areas);
            camera.setParameters(p);
            camera.cancelAutoFocus();
            camera.autoFocus(this);
        } catch (Exception e) { Log.e(TAG, "focusAt", e); }
    }

    private void toast(final String msg) {
        ui.post(() -> {
            TextView t = new TextView(this);
            t.setText(msg);
            t.setTextColor(Color.WHITE);
            t.setTextSize(16);
            t.setPadding(24, 16, 24, 16);
            t.setBackgroundColor(0xCC000000);
            FrameLayout.LayoutParams lp = new FrameLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            lp.gravity = Gravity.CENTER;
            root.addView(t, lp);
            ui.postDelayed(() -> root.removeView(t), 3000);
        });
    }

    @Override
    protected void onPause() {
        super.onPause();
        releaseCamera();
    }

    @Override
    protected void onDestroy() {
        finished = true;
        try { decoder.shutdownNow(); } catch (Exception ignored) { }
        super.onDestroy();
    }

    // ------------------------------------------------------------ 取景框 + 扫描线 + 状态
    private class OverlayView extends View {
        private final Paint framePaint = new Paint();
        private final Paint linePaint = new Paint();
        private final Paint textPaint = new Paint();
        private final Paint bannerPaint = new Paint();
        private final Paint maskPaint = new Paint();
        private float lineTop = 0f;
        private boolean down = true;
        private int fCount = 0, hCount = 0;
        private String lastSeen = null;
        private String banner = null;

        OverlayView() {
            super(ScanActivity.this);
            framePaint.setColor(0xCCFFFFFF);
            framePaint.setStyle(Paint.Style.STROKE);
            framePaint.setStrokeWidth(4f);
            linePaint.setColor(0xFFFF3B3B);
            linePaint.setStrokeWidth(4f);
            textPaint.setColor(Color.WHITE);
            textPaint.setTextSize(30f);
            textPaint.setAntiAlias(true);
            bannerPaint.setColor(0xFF1E7B45);
            bannerPaint.setTextSize(32f);
            bannerPaint.setAntiAlias(true);
            maskPaint.setColor(0x66000000);
        }

        void refresh(int frames, int hits, String text) {
            fCount = frames;
            hCount = hits;
            lastSeen = text;
            invalidate();
        }

        void banner(String msg) {
            banner = msg;
            invalidate();
            ui.postDelayed(() -> { banner = null; invalidate(); }, 2200);
        }

        @Override
        public boolean onTouchEvent(MotionEvent event) {
            if (event.getAction() == MotionEvent.ACTION_DOWN) {
                focusAt(event.getX(), event.getY(), getWidth(), getHeight());
                return true;
            }
            return super.onTouchEvent(event);
        }

        @Override
        protected void onDraw(Canvas canvas) {
            super.onDraw(canvas);
            int w = getWidth(), h = getHeight();
            float fw = w * 0.80f, fh = h * 0.42f;
            float left = (w - fw) / 2, top = (h - fh) / 2;

            canvas.drawRect(0, 0, w, top, maskPaint);
            canvas.drawRect(0, top + fh, w, h, maskPaint);
            canvas.drawRect(0, top, left, top + fh, maskPaint);
            canvas.drawRect(left + fw, top, w, top + fh, maskPaint);
            canvas.drawRect(new Rect((int) left, (int) top,
                    (int) (left + fw), (int) (top + fh)), framePaint);

            lineTop += down ? 14f : -14f;
            if (lineTop > fh - 6) { down = false; }
            if (lineTop < 6) { down = true; }
            canvas.drawLine(left + 6, top + lineTop, left + fw - 6, top + lineTop, linePaint);

            String hint = ("sn".equals(mode) ? "对准原厂条码 / 快递单" : "对准二维码 / 条形码") +
                    "  · 扫到一条即返回  帧 " + fCount;
            canvas.drawText(hint, 28, Math.max(46, top - 26), textPaint);
            if (lastSeen != null) {
                canvas.drawText("最近：" + shorten(lastSeen), 28, h - 40, textPaint);
            }
            if (banner != null) {
                canvas.drawRect(0, h * 0.42f, w, h * 0.42f + 70, bannerPaint);
                canvas.drawText(banner, 28, h * 0.42f + 46, textPaint);
            }
            invalidate();
        }
    }
}
