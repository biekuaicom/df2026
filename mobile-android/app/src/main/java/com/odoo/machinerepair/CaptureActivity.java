package com.odoo.machinerepair;

import android.app.Activity;
import android.content.Intent;
import android.content.pm.ActivityInfo;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Color;
import android.graphics.Matrix;
import android.hardware.Camera;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.view.Gravity;
import android.view.SurfaceHolder;
import android.view.SurfaceView;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.widget.FrameLayout;
import android.widget.TextView;
import android.util.Base64;

import java.io.ByteArrayOutputStream;
import java.util.List;

/**
 * 原生拍照页：全屏预览 + 一个大快门按钮，拍完立刻返回（不再经过系统相机的"确认/使用"步骤）。
 *
 * 返回：通过静态 Listener 把 JPEG 的 dataURL（base64）推给 WebView，
 *      页面拿到后走原来的 /photo 上传接口，用户只需要"打开→拍"两步。
 */
public class CaptureActivity extends Activity implements SurfaceHolder.Callback {

    public static final String EXTRA_KIND = "CAPTURE_KIND";
    private static final String TAG = "MRCapture";
    private static final int MAX_SIDE = 1280;      // 长边压到 1600px，够看清又不至于太大

    public interface Listener {
        void onPhoto(String kind, String dataUrl);
    }

    private static volatile Listener sListener;

    public static void setListener(Listener listener) {
        sListener = listener;
    }

    private FrameLayout root;
    private SurfaceView preview;
    private TextView hint;
    private Camera camera;
    private boolean busy = false;
    private boolean torchOn = false;
    private String kind = "front";

    private final Handler ui = new Handler(Looper.getMainLooper());

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_PORTRAIT);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        String k = getIntent().getStringExtra(EXTRA_KIND);
        if (k != null) { kind = k; }

        root = new FrameLayout(this);
        root.setBackgroundColor(Color.BLACK);

        preview = new SurfaceView(this);
        preview.getHolder().addCallback(this);
        root.addView(preview, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        TextView close = new TextView(this);
        close.setText("✕");
        close.setTextColor(Color.WHITE);
        close.setTextSize(24);
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

        // 快门：一个大白圆
        TextView shutter = new TextView(this);
        shutter.setText("拍");
        shutter.setTextColor(0xFF5B3A8F);
        shutter.setTextSize(20);
        shutter.setGravity(Gravity.CENTER);
        shutter.setBackgroundColor(Color.WHITE);
        int size = (int) (getResources().getDisplayMetrics().density * 76);
        FrameLayout.LayoutParams sLP = new FrameLayout.LayoutParams(size, size);
        sLP.gravity = Gravity.BOTTOM | Gravity.CENTER_HORIZONTAL;
        sLP.bottomMargin = (int) (getResources().getDisplayMetrics().density * 34);
        shutter.setOnClickListener(v -> takePicture());
        root.addView(shutter, sLP);

        hint = new TextView(this);
        hint.setTextColor(Color.WHITE);
        hint.setTextSize(14);
        hint.setGravity(Gravity.CENTER);
        hint.setText("对准后点「拍」");
        FrameLayout.LayoutParams hLP = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        hLP.gravity = Gravity.TOP | Gravity.CENTER_HORIZONTAL;
        hLP.topMargin = (int) (getResources().getDisplayMetrics().density * 60);
        root.addView(hint, hLP);

        setContentView(root);
    }

    private void toggleTorch() {
        if (camera == null) { return; }
        try {
            Camera.Parameters p = camera.getParameters();
            List<String> modes = p.getSupportedFlashModes();
            if (modes == null) { return; }
            torchOn = !torchOn;
            p.setFlashMode(torchOn ? Camera.Parameters.FLASH_MODE_TORCH
                                   : Camera.Parameters.FLASH_MODE_OFF);
            camera.setParameters(p);
        } catch (Exception e) { Log.e(TAG, "torch", e); }
    }

    private void takePicture() {
        if (camera == null || busy) { return; }
        busy = true;
        hint.setText("已拍摄，正在返回…");
        try {
            camera.takePicture(null, null, (data, cam) -> {
                try {
                    String dataUrl = toDataUrl(data);
                    Listener listener = sListener;
                    if (listener != null && dataUrl != null) {
                        listener.onPhoto(kind, dataUrl);
                    }
                } catch (Exception e) {
                    Log.e(TAG, "encode", e);
                }
                ui.post(this::finish);
            });
        } catch (Exception e) {
            Log.e(TAG, "takePicture", e);
            busy = false;
            hint.setText("拍照失败：" + e.getMessage());
        }
    }

    /** 原始 JPEG -> 缩小 -> base64 dataURL */
    private String toDataUrl(byte[] jpeg) {
        BitmapFactory.Options bounds = new BitmapFactory.Options();
        bounds.inJustDecodeBounds = true;
        BitmapFactory.decodeByteArray(jpeg, 0, jpeg.length, bounds);
        int w = bounds.outWidth, h = bounds.outHeight;
        int sample = 1;
        while (Math.max(w, h) / sample > MAX_SIDE) { sample *= 2; }

        BitmapFactory.Options opts = new BitmapFactory.Options();
        opts.inSampleSize = sample;
        Bitmap bitmap = BitmapFactory.decodeByteArray(jpeg, 0, jpeg.length, opts);
        if (bitmap == null) { return null; }

        // 拍照是传感器方向，竖屏需要转 90 度
        Matrix matrix = new Matrix();
        matrix.postRotate(90);
        Bitmap rotated = Bitmap.createBitmap(bitmap, 0, 0, bitmap.getWidth(), bitmap.getHeight(),
                matrix, true);
        if (rotated != bitmap) { bitmap.recycle(); }

        ByteArrayOutputStream out = new ByteArrayOutputStream();
        rotated.compress(Bitmap.CompressFormat.JPEG, 72, out);
        rotated.recycle();
        String base64 = Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP);
        return "data:image/jpeg;base64," + base64;
    }

    // ---------------------------------------------------------------- Surface
    @Override
    public void surfaceCreated(SurfaceHolder holder) {
        try {
            camera = Camera.open();
            Camera.Parameters p = camera.getParameters();
            p.setPictureFormat(android.graphics.ImageFormat.JPEG);
            List<String> focusModes = p.getSupportedFocusModes();
            if (focusModes != null && focusModes.contains(Camera.Parameters.FOCUS_MODE_CONTINUOUS_PICTURE)) {
                p.setFocusMode(Camera.Parameters.FOCUS_MODE_CONTINUOUS_PICTURE);
            }
            camera.setParameters(p);
            camera.setDisplayOrientation(90);
            camera.setPreviewDisplay(holder);
            camera.startPreview();
        } catch (Exception e) {
            Log.e(TAG, "open", e);
            hint.setText("摄像头打不开：" + e.getMessage());
        }
    }

    @Override
    public void surfaceChanged(SurfaceHolder holder, int format, int width, int height) { }

    @Override
    public void surfaceDestroyed(SurfaceHolder holder) { release(); }

    private void release() {
        if (camera != null) {
            try { camera.stopPreview(); } catch (Exception ignored) { }
            try { camera.release(); } catch (Exception ignored) { }
            camera = null;
        }
    }

    @Override
    protected void onPause() {
        super.onPause();
        release();
    }
}
