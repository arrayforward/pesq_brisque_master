package com.example.pesq;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.graphics.PixelFormat;
import android.hardware.display.DisplayManager;
import android.hardware.display.VirtualDisplay;
import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioPlaybackCaptureConfiguration;
import android.media.AudioRecord;
import android.media.Image;
import android.media.ImageReader;
import android.media.projection.MediaProjection;
import android.media.projection.MediaProjectionManager;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.util.DisplayMetrics;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.ByteBuffer;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

public class CaptureService extends Service {

    public static final String ACTION_START = "com.example.pesq.START";
    public static final String ACTION_MOS = "com.example.pesq.MOS";
    public static final String ACTION_FILE = "com.example.pesq.FILE";
    public static final String ACTION_STOP = "com.example.pesq.STOP";
    public static final String ACTION_EVENT = "com.example.pesq.EVENT";

    public static final String EXTRA_RESULT_CODE = "resultCode";
    public static final String EXTRA_RESULT_DATA = "resultData";
    public static final String EXTRA_DURATION_SEC = "durationSec";
    public static final String EXTRA_COUNTDOWN_SEC = "countdownSec";
    public static final String EXTRA_AUDIO_MODE = "audioMode";
    public static final String EXTRA_VIDEO_URI = "videoUri";
    public static final String EXTRA_FRAME_INTERVAL_MS = "frameIntervalMs";

    public static final int AUDIO_MODE_AUTO = 0;
    public static final int AUDIO_MODE_PLAYBACK = 1;
    public static final int AUDIO_MODE_MIC = 2;

    public static final String EXTRA_TYPE = "type";
    public static final String EXTRA_T = "t";
    public static final String EXTRA_SCORE = "score";
    public static final String EXTRA_MAX = "max";
    public static final String EXTRA_MIN = "min";
    public static final String EXTRA_AVG = "avg";
    public static final String EXTRA_MSG = "msg";
    public static final String EXTRA_IDX = "idx";

    private static final int NOTIF_ID = 1;
    private static final String CHANNEL_ID = "capture";

    private static final int FRAME_INTERVAL_MS = 100;
    private static final int SCORE_WIDTH = 512;
    private static final int TRIM_HIGH = 5;
    private static final int TRIM_LOW = 3;

    private MediaProjection projection;
    private VirtualDisplay virtualDisplay;
    private ImageReader imageReader;
    private ScheduledExecutorService captureExecutor;
    private Thread audioThread;
    private final Handler handler = new Handler(Looper.getMainLooper());

    private volatile boolean running = false;
    private volatile boolean mosOnly = false;
    private long startTimeMs;
    private final int sampleRate = 16000;
    private int mosDurationSec = 30;
    private int captureSec = 10;
    private int countdownSec = 5;
    private int audioMode = AUDIO_MODE_AUTO;

    private final ByteArrayOutputStream audioBuf = new ByteArrayOutputStream();
    private final List<byte[]> frames = new ArrayList<>();
    private int width, height, densityDpi;
    private int grayW, grayH;
    private Brisque brisque;

    @Override
    public void onCreate() {
        super.onCreate();
        NotificationChannel ch = new NotificationChannel(CHANNEL_ID, "质量评估",
                NotificationManager.IMPORTANCE_LOW);
        getSystemService(NotificationManager.class).createNotificationChannel(ch);
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent == null) return START_NOT_STICKY;
        String action = intent.getAction();
        if (ACTION_STOP.equals(action)) {
            stopMeasurement();
            return START_NOT_STICKY;
        }
        if (ACTION_FILE.equals(action)) {
            if (running) return START_NOT_STICKY;
            String uriStr = intent.getStringExtra(EXTRA_VIDEO_URI);
            int intervalMs = Math.max(100, intent.getIntExtra(EXTRA_FRAME_INTERVAL_MS, 200));
            if (uriStr == null) {
                emit("error", "未选择视频文件");
                stopSelf();
                return START_NOT_STICKY;
            }
            running = true;
            startForeground(NOTIF_ID, buildNotification("正在评估本地视频…"));
            new Thread(() -> runFileEvaluation(android.net.Uri.parse(uriStr), intervalMs),
                    "file-eval").start();
            return START_NOT_STICKY;
        }
        if ((!ACTION_START.equals(action) && !ACTION_MOS.equals(action)) || running) {
            return START_NOT_STICKY;
        }
        mosOnly = ACTION_MOS.equals(action);
        if (mosOnly) {
            mosDurationSec = Math.max(5, Math.min(600,
                    intent.getIntExtra(EXTRA_DURATION_SEC, 30)));
        } else {
            captureSec = Math.max(5, Math.min(60,
                    intent.getIntExtra(EXTRA_DURATION_SEC, 10)));
        }
        countdownSec = Math.max(0, Math.min(30,
                intent.getIntExtra(EXTRA_COUNTDOWN_SEC, 5)));
        audioMode = intent.getIntExtra(EXTRA_AUDIO_MODE, AUDIO_MODE_AUTO);

        int resultCode = intent.getIntExtra(EXTRA_RESULT_CODE, 0);
        Intent data = intent.getParcelableExtra(EXTRA_RESULT_DATA);
        if (data == null) {
            emit("error", "缺少截屏授权");
            stopSelf();
            return START_NOT_STICKY;
        }

        try {
            startForeground(NOTIF_ID, buildNotification(),
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION
                            | ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE);
        } catch (Throwable t) {
            emitError("启动前台服务(startForeground)", t);
            stopSelf();
            return START_NOT_STICKY;
        }

        try {
            MediaProjectionManager mpm = getSystemService(MediaProjectionManager.class);
            projection = mpm.getMediaProjection(resultCode, data);
        } catch (Throwable t) {
            emitError("获取截屏令牌(getMediaProjection)", t);
            stopForeground(true);
            stopSelf();
            return START_NOT_STICKY;
        }

        projection.registerCallback(new MediaProjection.Callback() {
            @Override
            public void onStop() {
                stopMeasurement();
            }
        }, handler);

        if (!mosOnly) {
            try {
                DisplayMetrics dm = getResources().getDisplayMetrics();
                width = dm.widthPixels & ~1;
                height = dm.heightPixels & ~1;
                densityDpi = dm.densityDpi;
                grayW = Math.min(SCORE_WIDTH, width) & ~1;
                grayH = (int) ((long) height * grayW / width) & ~1;
                imageReader = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 3);
                virtualDisplay = projection.createVirtualDisplay("pesq", width, height, densityDpi,
                        DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR, imageReader.getSurface(),
                        null, null);
            } catch (Throwable t) {
                emitError("启动截屏(createVirtualDisplay)", t);
                stopForeground(true);
                stopSelf();
                return START_NOT_STICKY;
            }
        }

        running = true;
        clearFrameImages();
        startCountdown();
        return START_NOT_STICKY;
    }

    private void startCountdown() {
        emit("status", "请切换到要评估的 App");
        final int[] left = {countdownSec};
        Runnable tick = new Runnable() {
            @Override
            public void run() {
                if (!running) return;
                if (left[0] > 0) {
                    emit("countdown", "倒数 " + left[0] + " 秒后开始采集…");
                    updateNotification("倒数 " + left[0] + " 秒后开始采集, 请切换到目标 App");
                    left[0]--;
                    handler.postDelayed(this, 1000);
                } else {
                    if (mosOnly) {
                        updateNotification("正在录制系统声音…");
                        beginMos();
                    } else {
                        updateNotification("正在采集: 截屏 + 录制系统声音…");
                        beginCapture();
                    }
                }
            }
        };
        handler.post(tick);
    }

    private void beginCapture() {
        startTimeMs = System.currentTimeMillis();
        emit("status", "开始采集: " + captureSec + " 秒, 每 100ms 截一帧");
        startAudio();
        captureExecutor = Executors.newSingleThreadScheduledExecutor();
        captureExecutor.scheduleAtFixedRate(this::captureFrame, 0,
                FRAME_INTERVAL_MS, TimeUnit.MILLISECONDS);
        startTicker(captureSec);
        handler.postDelayed(this::stopMeasurement, captureSec * 1000L);
    }

    private void beginMos() {
        startTimeMs = System.currentTimeMillis();
        emit("status", "开始录音 " + mosDurationSec + " 秒…");
        startAudio();
        startTicker(mosDurationSec);
        handler.postDelayed(this::stopMos, mosDurationSec * 1000L);
    }

    private void startTicker(int totalSec) {
        final int[] left = {totalSec};
        Runnable tick = new Runnable() {
            @Override
            public void run() {
                if (!running) return;
                Intent i = new Intent(ACTION_EVENT).setPackage(getPackageName());
                i.putExtra(EXTRA_TYPE, "capture_tick");
                i.putExtra(EXTRA_IDX, left[0]);
                sendBroadcast(i);
                updateNotification("录制中, 剩余 " + left[0] + " 秒");
                left[0]--;
                if (left[0] > 0) handler.postDelayed(this, 1000);
            }
        };
        handler.post(tick);
    }

    private synchronized void stopMos() {
        if (!running) {
            stopSelf();
            return;
        }
        running = false;
        handler.removeCallbacksAndMessages(null);
        if (audioThread != null) {
            try {
                audioThread.join(3000);
            } catch (InterruptedException ignored) {
            }
        }
        releaseProjection();
        stopForeground(true);

        byte[] pcm;
        synchronized (audioBuf) {
            pcm = audioBuf.toByteArray();
        }
        final short[] deg = new short[pcm.length / 2];
        int maxAbs = 0;
        for (int i = 0; i < deg.length; i++) {
            deg[i] = (short) ((pcm[2 * i] & 0xff) | (pcm[2 * i + 1] << 8));
            int a = Math.abs(deg[i]);
            if (a > maxAbs) maxAbs = a;
        }
        emit("status", "录音结束 (" + deg.length + " 样本, 最大振幅 " + maxAbs + "), 正在评分…");

        new Thread(() -> {
            boolean mosnetOk = MosNet.init(this);
            int segLen = sampleRate * 10;
            int segs = Math.max(1, deg.length / segLen);
            double mosnetSum = 0, p563Sum = 0;
            int counted = 0;
            for (int s = 0; s < segs; s++) {
                int from = s * segLen;
                int len = Math.min(segLen, deg.length - from);
                if (len < sampleRate * 3) continue;
                short[] seg = new short[len];
                System.arraycopy(deg, from, seg, 0, len);
                float mosnet = mosnetOk ? MosNet.measure(seg) : -1;
                float p563 = P563.nativeMeasure(P563.downsample16to8(seg));
                mosnetSum += mosnet;
                p563Sum += p563;
                counted++;
                emit("status", "第 " + (s + 1) + "/" + segs + " 段: MOSNet="
                        + fmt(mosnet) + "  P.563=" + fmt(p563));
                Intent si = new Intent(ACTION_EVENT).setPackage(getPackageName());
                si.putExtra(EXTRA_TYPE, "mos_seg");
                si.putExtra(EXTRA_IDX, s + 1);
                si.putExtra(EXTRA_SCORE, (double) mosnet);
                si.putExtra(EXTRA_AVG, (double) p563);
                sendBroadcast(si);
            }
            if (counted > 0) {
                Intent mi = new Intent(ACTION_EVENT).setPackage(getPackageName());
                mi.putExtra(EXTRA_TYPE, "mos");
                mi.putExtra(EXTRA_SCORE, mosnetSum / counted);
                mi.putExtra(EXTRA_AVG, p563Sum / counted);
                mi.putExtra(EXTRA_MSG, counted + " 段平均");
                sendBroadcast(mi);
            }
            if (deg.length > sampleRate) {
                AudioQuality.Result r = AudioQuality.analyze(deg, sampleRate);
                Intent ai = new Intent(ACTION_EVENT).setPackage(getPackageName());
                ai.putExtra(EXTRA_TYPE, "audio");
                ai.putExtra(EXTRA_MSG, r.detail());
                sendBroadcast(ai);
            }
            emit("done", null);
            stopSelf();
        }, "mos-calc").start();
    }

    private Notification buildNotification() {
        return buildNotification("正在截屏与录制系统声音…");
    }

    private Notification buildNotification(String text) {
        return new Notification.Builder(this, CHANNEL_ID)
                .setContentTitle("画面/音频质量评估中")
                .setContentText(text)
                .setSmallIcon(android.R.drawable.ic_dialog_info)
                .setOngoing(true)
                .build();
    }

    private void updateNotification(String text) {
        NotificationManager nm = getSystemService(NotificationManager.class);
        nm.notify(NOTIF_ID, buildNotification(text));
    }

    private volatile boolean useMic = false;

    private void startAudio() {
        useMic = (audioMode == AUDIO_MODE_MIC);
        audioThread = new Thread(this::recordLoop, "audio-rec");
        audioThread.start();
    }

    private void emitAudioMode(String label) {
        Intent i = new Intent(ACTION_EVENT).setPackage(getPackageName());
        i.putExtra(EXTRA_TYPE, "audio_mode");
        i.putExtra(EXTRA_MSG, label);
        sendBroadcast(i);
    }

    private AudioRecord buildPlaybackRecord() {
        AudioPlaybackCaptureConfiguration captureConfig =
                new AudioPlaybackCaptureConfiguration.Builder(projection)
                        .addMatchingUsage(AudioAttributes.USAGE_MEDIA)
                        .addMatchingUsage(AudioAttributes.USAGE_GAME)
                        .addMatchingUsage(AudioAttributes.USAGE_UNKNOWN)
                        .build();
        AudioFormat format = new AudioFormat.Builder()
                .setSampleRate(sampleRate)
                .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                .setChannelMask(AudioFormat.CHANNEL_IN_MONO)
                .build();
        int minBuf = AudioRecord.getMinBufferSize(sampleRate,
                AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        AudioRecord record = new AudioRecord.Builder()
                .setAudioPlaybackCaptureConfig(captureConfig)
                .setAudioFormat(format)
                .setBufferSizeInBytes(Math.max(minBuf, 8192))
                .build();
        if (record.getState() != AudioRecord.STATE_INITIALIZED) {
            record.release();
            return null;
        }
        return record;
    }

    private AudioRecord buildMicRecord() {
        int minBuf = AudioRecord.getMinBufferSize(sampleRate,
                AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        AudioRecord record = new AudioRecord(android.media.MediaRecorder.AudioSource.MIC,
                sampleRate, AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT, Math.max(minBuf, 8192));
        if (record.getState() != AudioRecord.STATE_INITIALIZED) {
            record.release();
            return null;
        }
        return record;
    }

    private void recordLoop() {
        while (running) {
            AudioRecord record = null;
            boolean fallback = false;
            try {
                if (useMic) {
                    emitAudioMode("麦克风模式");
                    record = buildMicRecord();
                    if (record == null) throw new IllegalStateException("麦克风初始化失败");
                } else {
                    emitAudioMode("数字采集 (系统回放)");
                    record = buildPlaybackRecord();
                    if (record == null) {
                        throw new IllegalStateException("回放采集初始化失败 (设备或目标 App 不支持)");
                    }
                }
                record.startRecording();
                byte[] buf = new byte[4096];
                long samplesRead = 0;
                int maxAbs = 0;
                while (running) {
                    int n = record.read(buf, 0, buf.length);
                    if (n > 0) {
                        synchronized (audioBuf) {
                            audioBuf.write(buf, 0, n);
                        }
                        samplesRead += n / 2;
                        if (!useMic && audioMode == AUDIO_MODE_AUTO
                                && samplesRead < sampleRate * 3) {
                            for (int i = 0; i + 1 < n; i += 2) {
                                int a = Math.abs((short) ((buf[i] & 0xff) | (buf[i + 1] << 8)));
                                if (a > maxAbs) maxAbs = a;
                            }
                            if (samplesRead >= sampleRate * 2 && maxAbs == 0) {
                                emit("error", "回放采集为静音 (目标 App 禁止被采集), 自动切换为麦克风模式");
                                useMic = true;
                                fallback = true;
                                break;
                            }
                        }
                    } else if (n < 0) {
                        throw new IllegalStateException("AudioRecord.read 返回 " + n);
                    }
                }
            } catch (Throwable t) {
                if (!useMic && audioMode == AUDIO_MODE_AUTO) {
                    emitError("回放采集不可用, 降级为麦克风模式", t);
                    useMic = true;
                    fallback = true;
                } else {
                    emitError("录制声音(AudioRecord)", t);
                }
            } finally {
                if (record != null) {
                    try {
                        record.stop();
                    } catch (Exception ignored) {
                    }
                    record.release();
                }
            }
            if (!fallback) break;
        }
    }

    private void captureFrame() {
        if (!running) return;
        Image image = null;
        try {
            image = imageReader.acquireLatestImage();
            if (image == null) return;
            int[] pixels = toPixelsDownscaled(image);
            byte[] gray = new byte[grayW * grayH];
            for (int i = 0; i < pixels.length; i++) {
                int p = pixels[i];
                int r = (p >> 16) & 0xff;
                int g = (p >> 8) & 0xff;
                int b = p & 0xff;
                gray[i] = (byte) (0.2125 * r + 0.7154 * g + 0.0721 * b);
            }
            int idx;
            synchronized (frames) {
                frames.add(gray);
                idx = frames.size();
            }
            saveFrameJpeg(pixels, idx);
            if (idx % 10 == 0) emit("progress", "已采集 " + idx + " 帧");
        } catch (Throwable t) {
            emitError("截屏取帧", t);
        } finally {
            if (image != null) image.close();
        }
    }

    private int[] toPixelsDownscaled(Image image) {
        Image.Plane plane = image.getPlanes()[0];
        ByteBuffer buf = plane.getBuffer();
        int pixelStride = plane.getPixelStride();
        int rowStride = plane.getRowStride();
        int[] pixels = new int[grayW * grayH];
        double sx = (double) width / grayW;
        double sy = (double) height / grayH;
        for (int y = 0; y < grayH; y++) {
            double fy = (y + 0.5) * sy - 0.5;
            int y0 = (int) Math.floor(fy);
            double wy = fy - y0;
            int ya = Math.max(0, Math.min(height - 1, y0));
            int yb = Math.max(0, Math.min(height - 1, y0 + 1));
            int outRow = y * grayW;
            for (int x = 0; x < grayW; x++) {
                double fx = (x + 0.5) * sx - 0.5;
                int x0 = (int) Math.floor(fx);
                double wx = fx - x0;
                int xa = Math.max(0, Math.min(width - 1, x0));
                int xb = Math.max(0, Math.min(width - 1, x0 + 1));
                int p00 = readPixel(buf, ya * rowStride + xa * pixelStride);
                int p01 = readPixel(buf, ya * rowStride + xb * pixelStride);
                int p10 = readPixel(buf, yb * rowStride + xa * pixelStride);
                int p11 = readPixel(buf, yb * rowStride + xb * pixelStride);
                int r = bilerp(p00, p01, p10, p11, wx, wy, 16);
                int g = bilerp(p00, p01, p10, p11, wx, wy, 8);
                int b = bilerp(p00, p01, p10, p11, wx, wy, 0);
                pixels[outRow + x] = 0xff000000 | (r << 16) | (g << 8) | b;
            }
        }
        return pixels;
    }

    private int readPixel(ByteBuffer buf, int off) {
        return (buf.get(off) & 0xff) << 16 | (buf.get(off + 1) & 0xff) << 8 | (buf.get(off + 2) & 0xff);
    }

    private int bilerp(int p00, int p01, int p10, int p11, double wx, double wy, int shift) {
        double c00 = (p00 >> shift) & 0xff;
        double c01 = (p01 >> shift) & 0xff;
        double c10 = (p10 >> shift) & 0xff;
        double c11 = (p11 >> shift) & 0xff;
        double top = c00 + (c01 - c00) * wx;
        double bot = c10 + (c11 - c10) * wx;
        return (int) Math.round(top + (bot - top) * wy);
    }

    private void saveFrameJpeg(int[] pixels, int idx) {
        try {
            android.graphics.Bitmap bmp = android.graphics.Bitmap.createBitmap(
                    pixels, grayW, grayH, android.graphics.Bitmap.Config.ARGB_8888);
            File f = new File(getFilesDir(), String.format(Locale.US, "frames/frame_%03d.jpg", idx));
            FileOutputStream fos = new FileOutputStream(f);
            bmp.compress(android.graphics.Bitmap.CompressFormat.JPEG, 85, fos);
            fos.close();
            bmp.recycle();
        } catch (Exception ignored) {
        }
    }

    private void clearFrameImages() {
        synchronized (frames) {
            frames.clear();
        }
        synchronized (audioBuf) {
            audioBuf.reset();
        }
        File dir = new File(getFilesDir(), "frames");
        File[] list = dir.listFiles();
        if (list != null) {
            for (File f : list) f.delete();
        }
        dir.mkdirs();
    }

    private synchronized void stopMeasurement() {
        if (!running) {
            stopSelf();
            return;
        }
        running = false;
        handler.removeCallbacksAndMessages(null);
        if (captureExecutor != null) captureExecutor.shutdownNow();
        if (audioThread != null) {
            try {
                audioThread.join(3000);
            } catch (InterruptedException ignored) {
            }
        }
        releaseProjection();
        stopForeground(true);

        final List<byte[]> captured;
        synchronized (frames) {
            captured = new ArrayList<>(frames);
        }
        byte[] pcm;
        synchronized (audioBuf) {
            pcm = audioBuf.toByteArray();
        }
        final short[] deg = new short[pcm.length / 2];
        int maxAbs = 0;
        for (int i = 0; i < deg.length; i++) {
            deg[i] = (short) ((pcm[2 * i] & 0xff) | (pcm[2 * i + 1] << 8));
            int a = Math.abs(deg[i]);
            if (a > maxAbs) maxAbs = a;
        }
        android.util.Log.i("PesqMeter", "recorded samples=" + deg.length + " maxAbs=" + maxAbs);
        try {
            FileOutputStream fos = new FileOutputStream(new File(getFilesDir(), "last_audio.pcm"));
            fos.write(pcm);
            fos.close();
        } catch (Exception ignored) {
        }
        emit("status", "录制样本 " + deg.length + " 个, 最大振幅 " + maxAbs);

        emit("status", "采集结束, 共 " + captured.size() + " 帧, 正在打分…");
        new Thread(() -> {
            try {
                if (brisque == null) brisque = Brisque.get(this);
            } catch (Exception e) {
                emit("error", "BRISQUE 模型加载失败: " + e.getMessage());
            }
            List<Double> scores = new ArrayList<>();
            if (brisque != null) {
                float[] buf = new float[grayW * grayH];
                int i = 0;
                for (byte[] g : captured) {
                    for (int k = 0; k < g.length; k++) buf[k] = (g[k] & 0xff) / 255.0f;
                    double s = brisque.score(buf, grayW, grayH);
                    scores.add(s);
                    i++;
                    Intent fi = new Intent(ACTION_EVENT).setPackage(getPackageName());
                    fi.putExtra(EXTRA_TYPE, "frame");
                    fi.putExtra(EXTRA_IDX, i);
                    fi.putExtra(EXTRA_SCORE, s);
                    fi.putExtra(EXTRA_MSG, captured.size() + "");
                    sendBroadcast(fi);
                }
            }
            if (!scores.isEmpty()) {
                double max = Collections.max(scores);
                double min = Collections.min(scores);
                double sum = 0;
                for (double s : scores) sum += s;
                double avg = sum / scores.size();

                List<Double> sorted = new ArrayList<>(scores);
                Collections.sort(sorted);
                int from = Math.min(TRIM_LOW, sorted.size() / 4);
                int to = Math.max(from + 1, sorted.size() - Math.min(TRIM_HIGH, sorted.size() / 4));
                double tsum = 0;
                for (int i = from; i < to; i++) tsum += sorted.get(i);
                double trimmed = tsum / (to - from);

                Intent stats = new Intent(ACTION_EVENT).setPackage(getPackageName());
                stats.putExtra(EXTRA_TYPE, "brisque_stats");
                stats.putExtra(EXTRA_MAX, max);
                stats.putExtra(EXTRA_MIN, min);
                stats.putExtra(EXTRA_AVG, avg);
                stats.putExtra(EXTRA_SCORE, trimmed);
                stats.putExtra(EXTRA_MSG, "有效帧数 " + scores.size());
                sendBroadcast(stats);
            }
            if (deg.length > sampleRate) {
                if (MosNet.init(this)) {
                    emit("status", "MOSNet / P.563 推理中…");
                    float mos = MosNet.measure(deg);
                    float p563 = P563.nativeMeasure(P563.downsample16to8(deg));
                    if (mos >= 0) {
                        Intent mi = new Intent(ACTION_EVENT).setPackage(getPackageName());
                        mi.putExtra(EXTRA_TYPE, "mos");
                        mi.putExtra(EXTRA_SCORE, (double) mos);
                        mi.putExtra(EXTRA_AVG, (double) p563);
                        sendBroadcast(mi);
                    } else {
                        emit("error", "MOSNet 推理失败");
                    }
                } else {
                    emit("error", "MOSNet 权重加载失败");
                }
                AudioQuality.Result r = AudioQuality.analyze(deg, sampleRate);
                Intent ai = new Intent(ACTION_EVENT).setPackage(getPackageName());
                ai.putExtra(EXTRA_TYPE, "audio");
                ai.putExtra(EXTRA_MSG, r.detail());
                sendBroadcast(ai);
            } else {
                emit("error", "未录到系统声音 (目标 App 可能禁止被采集)");
            }
            emit("done", null);
            stopForeground(true);
            stopSelf();
        }, "score-calc").start();
    }

    private void runFileEvaluation(android.net.Uri uri, int intervalMs) {
        android.media.MediaMetadataRetriever mmr = new android.media.MediaMetadataRetriever();
        try {
            clearFrameImages();
            mmr.setDataSource(this, uri);
            String durStr = mmr.extractMetadata(
                    android.media.MediaMetadataRetriever.METADATA_KEY_DURATION);
            long durMs = durStr != null ? Long.parseLong(durStr) : 0;
            if (durMs <= 0) throw new IllegalStateException("无法读取视频时长");
            int total = (int) (durMs / intervalMs);
            emit("status", "视频时长 " + (durMs / 1000) + "s, 抽帧间隔 " + intervalMs
                    + "ms, 约 " + total + " 帧");
            if (brisque == null) brisque = Brisque.get(this);

            List<Double> scores = new ArrayList<>();
            int idx = 0;
            for (long t = 0; t < durMs && running; t += intervalMs) {
                android.graphics.Bitmap bmp = mmr.getFrameAtTime(t * 1000,
                        android.media.MediaMetadataRetriever.OPTION_CLOSEST);
                if (bmp == null) continue;
                int w = bmp.getWidth(), h = bmp.getHeight();
                int gw = Math.min(SCORE_WIDTH, w) & ~1;
                int gh = (int) ((long) h * gw / w) & ~1;
                android.graphics.Bitmap small = android.graphics.Bitmap
                        .createScaledBitmap(bmp, gw, gh, true);
                int[] pixels = new int[gw * gh];
                small.getPixels(pixels, 0, gw, 0, 0, gw, gh);
                float[] gray = new float[gw * gh];
                for (int i = 0; i < pixels.length; i++) {
                    int p = pixels[i];
                    gray[i] = (float) ((0.2125 * ((p >> 16) & 0xff)
                            + 0.7154 * ((p >> 8) & 0xff) + 0.0721 * (p & 0xff)) / 255.0);
                }
                idx++;
                saveFrameJpeg(pixels, idx);
                double s = brisque.score(gray, gw, gh);
                scores.add(s);
                Intent fi = new Intent(ACTION_EVENT).setPackage(getPackageName());
                fi.putExtra(EXTRA_TYPE, "frame");
                fi.putExtra(EXTRA_IDX, idx);
                fi.putExtra(EXTRA_SCORE, s);
                fi.putExtra(EXTRA_MSG, String.valueOf(total));
                sendBroadcast(fi);
                bmp.recycle();
                small.recycle();
            }
            emitBrisqueStats(scores);

            emit("status", "正在解码音轨…");
            short[] pcm16k = decodeAudio16k(uri);
            if (pcm16k != null && pcm16k.length > sampleRate) {
                emitAudioScores(pcm16k);
            } else {
                emit("error", "无音轨或音轨解码失败");
            }
        } catch (Throwable t) {
            emitError("评估本地视频", t);
        } finally {
            try {
                mmr.release();
            } catch (Exception ignored) {
            }
            running = false;
            stopForeground(true);
            emit("done", null);
            stopSelf();
        }
    }

    private void emitBrisqueStats(List<Double> scores) {
        if (scores.isEmpty()) return;
        double max = Collections.max(scores);
        double min = Collections.min(scores);
        double sum = 0;
        for (double s : scores) sum += s;
        List<Double> sorted = new ArrayList<>(scores);
        Collections.sort(sorted);
        int from = Math.min(TRIM_LOW, sorted.size() / 4);
        int to = Math.max(from + 1, sorted.size() - Math.min(TRIM_HIGH, sorted.size() / 4));
        double tsum = 0;
        for (int i = from; i < to; i++) tsum += sorted.get(i);
        Intent stats = new Intent(ACTION_EVENT).setPackage(getPackageName());
        stats.putExtra(EXTRA_TYPE, "brisque_stats");
        stats.putExtra(EXTRA_MAX, max);
        stats.putExtra(EXTRA_MIN, min);
        stats.putExtra(EXTRA_AVG, sum / scores.size());
        stats.putExtra(EXTRA_SCORE, tsum / (to - from));
        stats.putExtra(EXTRA_MSG, "有效帧数 " + scores.size());
        sendBroadcast(stats);
    }

    private void emitAudioScores(short[] deg) {
        if (MosNet.init(this)) {
            emit("status", "MOSNet / P.563 推理中…");
            float mos = MosNet.measure(deg);
            float p563 = P563.nativeMeasure(P563.downsample16to8(deg));
            if (mos >= 0) {
                Intent mi = new Intent(ACTION_EVENT).setPackage(getPackageName());
                mi.putExtra(EXTRA_TYPE, "mos");
                mi.putExtra(EXTRA_SCORE, (double) mos);
                mi.putExtra(EXTRA_AVG, (double) p563);
                sendBroadcast(mi);
            } else {
                emit("error", "MOSNet 推理失败");
            }
        } else {
            emit("error", "MOSNet 权重加载失败");
        }
        AudioQuality.Result r = AudioQuality.analyze(deg, sampleRate);
        Intent ai = new Intent(ACTION_EVENT).setPackage(getPackageName());
        ai.putExtra(EXTRA_TYPE, "audio");
        ai.putExtra(EXTRA_MSG, r.detail());
        sendBroadcast(ai);
    }

    private short[] decodeAudio16k(android.net.Uri uri) {
        android.media.MediaExtractor ex = new android.media.MediaExtractor();
        android.media.MediaCodec codec = null;
        try {
            ex.setDataSource(this, uri, null);
            int track = -1;
            android.media.MediaFormat fmt = null;
            String mime = null;
            for (int i = 0; i < ex.getTrackCount(); i++) {
                android.media.MediaFormat f = ex.getTrackFormat(i);
                String m = f.getString(android.media.MediaFormat.KEY_MIME);
                if (m != null && m.startsWith("audio/")) {
                    track = i;
                    fmt = f;
                    mime = m;
                    break;
                }
            }
            if (track < 0) return null;
            ex.selectTrack(track);
            codec = android.media.MediaCodec.createDecoderByType(mime);
            codec.configure(fmt, null, null, 0);
            codec.start();

            ByteArrayOutputStream pcm = new ByteArrayOutputStream();
            android.media.MediaCodec.BufferInfo info = new android.media.MediaCodec.BufferInfo();
            boolean inputEos = false, outputEos = false;
            int channels = fmt.getInteger(android.media.MediaFormat.KEY_CHANNEL_COUNT);
            int rate = fmt.getInteger(android.media.MediaFormat.KEY_SAMPLE_RATE);
            while (!outputEos && running) {
                if (!inputEos) {
                    int inIdx = codec.dequeueInputBuffer(10000);
                    if (inIdx >= 0) {
                        ByteBuffer buf = codec.getInputBuffer(inIdx);
                        int n = ex.readSampleData(buf, 0);
                        if (n < 0) {
                            codec.queueInputBuffer(inIdx, 0, 0, 0,
                                    android.media.MediaCodec.BUFFER_FLAG_END_OF_STREAM);
                            inputEos = true;
                        } else {
                            codec.queueInputBuffer(inIdx, 0, n, ex.getSampleTime(), 0);
                            ex.advance();
                        }
                    }
                }
                int outIdx = codec.dequeueOutputBuffer(info, 10000);
                if (outIdx == android.media.MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                    android.media.MediaFormat nf = codec.getOutputFormat();
                    channels = nf.getInteger(android.media.MediaFormat.KEY_CHANNEL_COUNT);
                    rate = nf.getInteger(android.media.MediaFormat.KEY_SAMPLE_RATE);
                } else if (outIdx >= 0) {
                    if (info.size > 0) {
                        ByteBuffer out = codec.getOutputBuffer(outIdx);
                        byte[] chunk = new byte[info.size];
                        out.get(chunk);
                        pcm.write(chunk, 0, chunk.length);
                    }
                    codec.releaseOutputBuffer(outIdx, false);
                    if ((info.flags
                            & android.media.MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0) {
                        outputEos = true;
                    }
                }
            }
            byte[] raw = pcm.toByteArray();
            int frames = raw.length / 2 / channels;
            short[] mono = new short[frames];
            for (int i = 0; i < frames; i++) {
                int acc = 0;
                for (int c = 0; c < channels; c++) {
                    int off = (i * channels + c) * 2;
                    acc += (short) ((raw[off] & 0xff) | (raw[off + 1] << 8));
                }
                mono[i] = (short) (acc / channels);
            }
            if (rate == sampleRate) return mono;
            int outLen = (int) ((long) frames * sampleRate / rate);
            short[] out = new short[outLen];
            for (int j = 0; j < outLen; j++) {
                double pos = (double) j * rate / sampleRate;
                int i0 = (int) pos;
                if (i0 >= frames - 1) i0 = frames - 1;
                double f = pos - (int) pos;
                int i1 = Math.min(i0 + 1, frames - 1);
                out[j] = (short) (mono[i0] * (1 - f) + mono[i1] * f);
            }
            return out;
        } catch (Throwable t) {
            emitError("解码音轨", t);
            return null;
        } finally {
            try {
                ex.release();
            } catch (Exception ignored) {
            }
            if (codec != null) {
                try {
                    codec.stop();
                } catch (Exception ignored) {
                }
                codec.release();
            }
        }
    }

    private void releaseProjection() {
        if (virtualDisplay != null) {
            virtualDisplay.release();
            virtualDisplay = null;
        }
        if (imageReader != null) {
            imageReader.close();
            imageReader = null;
        }
        if (projection != null) {
            try {
                projection.stop();
            } catch (Exception ignored) {
            }
            projection = null;
        }
    }

    private void emit(String type, String msg) {
        Intent i = new Intent(ACTION_EVENT).setPackage(getPackageName());
        i.putExtra(EXTRA_TYPE, type);
        if (msg != null) i.putExtra(EXTRA_MSG, msg);
        sendBroadcast(i);
    }

    private void emitError(String where, Throwable t) {
        java.io.StringWriter sw = new java.io.StringWriter();
        t.printStackTrace(new java.io.PrintWriter(sw));
        String detail = where + " 失败\n" + t.getClass().getName() + ": " + t.getMessage()
                + "\n" + sw;
        android.util.Log.e("PesqMeter", detail);
        emit("error", detail);
    }

    @Override
    public void onDestroy() {
        running = false;
        if (captureExecutor != null) captureExecutor.shutdownNow();
        releaseProjection();
        super.onDestroy();
    }

    public static String fmt(double v) {
        return String.format(Locale.US, "%.2f", v);
    }
}
