package com.example.pesq;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.graphics.drawable.GradientDrawable;
import android.media.projection.MediaProjectionManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.text.method.ScrollingMovementMethod;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.BaseAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ListView;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

public class MainActivity extends Activity {

    private static final int REQ_PERMISSIONS = 1;
    private static final int REQ_PROJECTION = 2;
    private static final int REQ_PROJECTION_MOS = 3;

    private TextView tvStatus, tvCountdown, tvStatMax, tvStatMin, tvStatAvg, tvStatTrim, tvAudio, tvLog;
    private TextView tabAV, tabAudio, tabLog, tabConfig;
    private ScrollView panelAV, panelAudio, panelConfig;
    private ProgressBar progressBar;
    private Button btnStart, btnMos;
    private EditText etCountdown, etCaptureSec, etMosDuration;
    private FrameAdapter adapter;

    private double audioMos = Double.NaN;
    private double audioP563 = Double.NaN;
    private String audioDetail = null;

    private final BroadcastReceiver eventReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            String type = intent.getStringExtra(CaptureService.EXTRA_TYPE);
            if (type == null) return;
            String msg = intent.getStringExtra(CaptureService.EXTRA_MSG);
            double score = intent.getDoubleExtra(CaptureService.EXTRA_SCORE, 0);
            switch (type) {
                case "countdown":
                    tvStatus.setText(msg);
                    tvCountdown.setVisibility(View.VISIBLE);
                    try {
                        tvCountdown.setText(msg.replaceAll("[^0-9]", ""));
                    } catch (Exception ignored) {
                    }
                    break;
                case "capture_tick": {
                    int left = intent.getIntExtra(CaptureService.EXTRA_IDX, 0);
                    tvCountdown.setVisibility(View.VISIBLE);
                    tvCountdown.setText(String.valueOf(left));
                    tvStatus.setText("录制中, 剩余 " + left + " 秒");
                    break;
                }
                case "status":
                case "progress":
                    tvStatus.setText(msg);
                    if (msg != null) appendLog(msg);
                    if (msg != null && (msg.contains("开始采集") || msg.contains("结束"))) {
                        tvCountdown.setVisibility(View.GONE);
                    }
                    break;
                case "frame": {
                    int idx = intent.getIntExtra(CaptureService.EXTRA_IDX, 0);
                    int total = 100;
                    try {
                        total = Integer.parseInt(msg);
                    } catch (Exception ignored) {
                    }
                    adapter.add(idx, score);
                    tvStatus.setText(String.format(Locale.US, "打分中 %d/%d", idx, total));
                    progressBar.setProgress((int) (100L * idx / Math.max(1, total)));
                    break;
                }
                case "brisque_stats":
                    tvStatMax.setText(fmt(intent.getDoubleExtra(CaptureService.EXTRA_MAX, 0)));
                    tvStatMin.setText(fmt(intent.getDoubleExtra(CaptureService.EXTRA_MIN, 0)));
                    tvStatAvg.setText(fmt(intent.getDoubleExtra(CaptureService.EXTRA_AVG, 0)));
                    tvStatTrim.setText(fmt(score));
                    tvStatus.setText("画面打分完成 (" + (msg != null ? msg : "") + ")");
                    break;
                case "audio":
                    audioDetail = msg;
                    renderAudio();
                    break;
                case "mos_seg": {
                    int idx = intent.getIntExtra(CaptureService.EXTRA_IDX, 0);
                    double p563 = intent.getDoubleExtra(CaptureService.EXTRA_AVG, 0);
                    String line = String.format(Locale.US,
                            "第 %d 段: MOSNet=%.2f  P.563=%.2f", idx, score, p563);
                    tvStatus.setText(line);
                    appendLog(line);
                    break;
                }
                case "mos":
                    audioMos = score;
                    audioP563 = intent.getDoubleExtra(CaptureService.EXTRA_AVG, Double.NaN);
                    renderAudio();
                    break;
                case "error":
                    tvStatus.setText("出错 (详见日志)");
                    appendLog("ERROR\n" + msg);
                    break;
                case "done":
                    tvStatus.setText("评估完成");
                    tvCountdown.setVisibility(View.GONE);
                    progressBar.setProgress(100);
                    btnStart.setEnabled(true);
                    btnMos.setEnabled(true);
                    break;
            }
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        tvStatus = findViewById(R.id.tvStatus);
        tvCountdown = findViewById(R.id.tvCountdown);
        tvStatMax = findViewById(R.id.tvStatMax);
        tvStatMin = findViewById(R.id.tvStatMin);
        tvStatAvg = findViewById(R.id.tvStatAvg);
        tvStatTrim = findViewById(R.id.tvStatTrim);
        tvAudio = findViewById(R.id.tvAudio);
        tvLog = findViewById(R.id.tvLog);
        tvLog.setMovementMethod(new ScrollingMovementMethod());
        progressBar = findViewById(R.id.progressBar);
        btnStart = findViewById(R.id.btnStart);
        btnMos = findViewById(R.id.btnMos);
        etCountdown = findViewById(R.id.etCountdown);
        etCaptureSec = findViewById(R.id.etCaptureSec);
        etMosDuration = findViewById(R.id.etMosDuration);

        tabAV = findViewById(R.id.tabAV);
        tabAudio = findViewById(R.id.tabAudio);
        tabLog = findViewById(R.id.tabLog);
        tabConfig = findViewById(R.id.tabConfig);
        panelAV = findViewById(R.id.panelAV);
        panelAudio = findViewById(R.id.panelAudio);
        panelConfig = findViewById(R.id.panelConfig);

        tabAV.setOnClickListener(v -> selectTab(0));
        tabAudio.setOnClickListener(v -> selectTab(1));
        tabLog.setOnClickListener(v -> selectTab(2));
        tabConfig.setOnClickListener(v -> selectTab(3));
        selectTab(0);

        ListView lv = findViewById(R.id.lvFrames);
        adapter = new FrameAdapter();
        lv.setAdapter(adapter);
        lv.setOnItemClickListener((parent, view, position, id) -> {
            FrameItem it = adapter.items.get(position);
            String path = new java.io.File(getFilesDir(),
                    String.format(Locale.US, "frames/frame_%03d.jpg", it.idx)).getAbsolutePath();
            Intent i = new Intent(this, FrameViewActivity.class);
            i.putExtra(FrameViewActivity.EXTRA_PATH, path);
            i.putExtra(FrameViewActivity.EXTRA_IDX, it.idx);
            i.putExtra(FrameViewActivity.EXTRA_SCORE, it.score);
            startActivity(i);
        });

        btnStart.setOnClickListener(v -> begin());
        btnMos.setOnClickListener(v -> beginMos());

        IntentFilter filter = new IntentFilter(CaptureService.ACTION_EVENT);
        if (Build.VERSION.SDK_INT >= 33) {
            registerReceiver(eventReceiver, filter, Context.RECEIVER_NOT_EXPORTED);
        } else {
            registerReceiver(eventReceiver, filter);
        }

        ensureAudioPermission(true);
    }

    private void selectTab(int which) {
        panelAV.setVisibility(which == 0 ? View.VISIBLE : View.GONE);
        panelAudio.setVisibility(which == 1 ? View.VISIBLE : View.GONE);
        tvLog.setVisibility(which == 2 ? View.VISIBLE : View.GONE);
        panelConfig.setVisibility(which == 3 ? View.VISIBLE : View.GONE);
        styleTab(tabAV, which == 0);
        styleTab(tabAudio, which == 1);
        styleTab(tabLog, which == 2);
        styleTab(tabConfig, which == 3);
    }

    private void styleTab(TextView tab, boolean selected) {
        tab.setTextColor(getColor(selected ? R.color.primary : R.color.text_secondary));
        tab.setBackgroundColor(getColor(selected ? R.color.window_bg : R.color.card_bg));
        tab.setTypeface(null, selected ? android.graphics.Typeface.BOLD
                : android.graphics.Typeface.NORMAL);
    }

    private boolean hasAudioPermission() {
        return checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                == PackageManager.PERMISSION_GRANTED;
    }

    private void ensureAudioPermission(boolean showRationale) {
        if (hasAudioPermission()) return;
        if (showRationale) {
            new AlertDialog.Builder(this)
                    .setTitle("需要录音权限")
                    .setMessage("本应用通过 MediaProjection + AudioPlaybackCapture 采集系统播放的声音，"
                            + "用于无参考音频质量评分。\n\n"
                            + "Android 要求先授予 RECORD_AUDIO 权限，点击确定后将弹出系统授权框。")
                    .setCancelable(false)
                    .setPositiveButton("确定", (d, w) -> requestAudioPermission())
                    .setNegativeButton("退出", (d, w) -> finish())
                    .show();
        } else {
            requestAudioPermission();
        }
    }

    private void requestAudioPermission() {
        if (Build.VERSION.SDK_INT >= 33) {
            requestPermissions(new String[]{
                    Manifest.permission.RECORD_AUDIO, Manifest.permission.POST_NOTIFICATIONS},
                    REQ_PERMISSIONS);
        } else {
            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, REQ_PERMISSIONS);
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions,
                                           int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode != REQ_PERMISSIONS) return;
        if (hasAudioPermission()) {
            toast("录音权限已授予");
        } else {
            new AlertDialog.Builder(this)
                    .setTitle("权限被拒绝")
                    .setMessage("没有 RECORD_AUDIO 权限将无法采集系统声音进行音质评分。\n"
                            + "可前往系统设置手动开启。")
                    .setPositiveButton("去设置", (d, w) -> {
                        Intent i = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                                Uri.fromParts("package", getPackageName(), null));
                        startActivity(i);
                    })
                    .setNegativeButton("重试", (d, w) -> requestAudioPermission())
                    .show();
        }
    }

    private void resetResults() {
        audioMos = Double.NaN;
        audioP563 = Double.NaN;
        audioDetail = null;
        adapter.clear();
        tvStatMax.setText("--");
        tvStatMin.setText("--");
        tvStatAvg.setText("--");
        tvStatTrim.setText("--");
        tvLog.setText("");
        renderAudio();
        progressBar.setProgress(0);
    }

    private int readInt(EditText et, int def) {
        try {
            return Integer.parseInt(et.getText().toString().trim());
        } catch (Exception e) {
            return def;
        }
    }

    private void begin() {
        if (!hasAudioPermission()) {
            ensureAudioPermission(true);
            return;
        }
        resetResults();
        tvCountdown.setVisibility(View.GONE);
        MediaProjectionManager mpm = (MediaProjectionManager)
                getSystemService(Context.MEDIA_PROJECTION_SERVICE);
        startActivityForResult(mpm.createScreenCaptureIntent(), REQ_PROJECTION);
    }

    private void beginMos() {
        if (!hasAudioPermission()) {
            ensureAudioPermission(true);
            return;
        }
        resetResults();
        tvCountdown.setVisibility(View.GONE);
        MediaProjectionManager mpm = (MediaProjectionManager)
                getSystemService(Context.MEDIA_PROJECTION_SERVICE);
        startActivityForResult(mpm.createScreenCaptureIntent(), REQ_PROJECTION_MOS);
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != REQ_PROJECTION && requestCode != REQ_PROJECTION_MOS) return;
        if (resultCode != RESULT_OK || data == null) {
            toast("未授权截屏/系统声音采集");
            return;
        }
        Intent i = new Intent(this, CaptureService.class).setAction(
                requestCode == REQ_PROJECTION_MOS
                        ? CaptureService.ACTION_MOS : CaptureService.ACTION_START);
        i.putExtra(CaptureService.EXTRA_RESULT_CODE, resultCode);
        i.putExtra(CaptureService.EXTRA_RESULT_DATA, data);
        i.putExtra(CaptureService.EXTRA_COUNTDOWN_SEC, readInt(etCountdown, 5));
        i.putExtra(CaptureService.EXTRA_DURATION_SEC,
                requestCode == REQ_PROJECTION_MOS
                        ? readInt(etMosDuration, 30) : readInt(etCaptureSec, 10));
        startForegroundService(i);
        btnStart.setEnabled(false);
        btnMos.setEnabled(false);
    }

    private void renderAudio() {
        StringBuilder sb = new StringBuilder();
        if (!Double.isNaN(audioMos)) {
            sb.append(String.format(Locale.US, "MOSNet MOS: %.2f / 5.0 (%s)\n",
                    audioMos, mosQuality(audioMos)));
        }
        if (!Double.isNaN(audioP563)) {
            sb.append(String.format(Locale.US, "P.563 MOS-LQO: %.2f / 4.5 (%s)\n",
                    audioP563, mosQuality(audioP563)));
        }
        if (audioDetail != null) sb.append(audioDetail);
        if (sb.length() == 0) sb.append("音频质量: 等待评估…");
        tvAudio.setText(sb.toString());
    }

    static String fmt(double v) {
        return String.format(Locale.US, "%.2f", v);
    }

    static String brisqueQuality(double s) {
        if (s < 25) return "优秀";
        if (s < 40) return "良好";
        if (s < 60) return "一般";
        return "较差";
    }

    static String mosQuality(double m) {
        if (m >= 4.0) return "优秀";
        if (m >= 3.0) return "良好";
        if (m >= 2.0) return "一般";
        return "较差";
    }

    private void toast(String s) {
        Toast.makeText(this, s, Toast.LENGTH_LONG).show();
    }

    private void appendLog(String s) {
        String line = String.format(Locale.US, "[%tT] %s\n", System.currentTimeMillis(), s);
        tvLog.append(line);
        if (tvLog.getLayout() != null) {
            int scroll = tvLog.getLayout().getLineTop(tvLog.getLineCount()) - tvLog.getHeight();
            if (scroll > 0) tvLog.scrollTo(0, scroll);
        }
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        try {
            unregisterReceiver(eventReceiver);
        } catch (Exception ignored) {
        }
    }

    static class FrameItem {
        final int idx;
        final double score;

        FrameItem(int idx, double score) {
            this.idx = idx;
            this.score = score;
        }
    }

    class FrameAdapter extends BaseAdapter {
        final List<FrameItem> items = new ArrayList<>();

        void add(int idx, double score) {
            items.add(new FrameItem(idx, score));
            notifyDataSetChanged();
        }

        void clear() {
            items.clear();
            notifyDataSetChanged();
        }

        @Override
        public int getCount() {
            return items.size();
        }

        @Override
        public Object getItem(int position) {
            return items.get(position);
        }

        @Override
        public long getItemId(int position) {
            return position;
        }

        @Override
        public View getView(int position, View convertView, ViewGroup parent) {
            View v = convertView;
            if (v == null) {
                v = LayoutInflater.from(parent.getContext())
                        .inflate(R.layout.item_frame, parent, false);
            }
            FrameItem it = items.get(position);
            TextView tvIdx = v.findViewById(R.id.tvIdx);
            TextView tvScore = v.findViewById(R.id.tvScore);
            TextView tvTag = v.findViewById(R.id.tvTag);
            ProgressBar pbScore = v.findViewById(R.id.pbScore);

            tvIdx.setText("第 " + it.idx + " 帧");
            tvScore.setText(fmt(it.score));
            pbScore.setProgress((int) Math.round(it.score));
            tvTag.setText(brisqueQuality(it.score));

            int color;
            if (it.score < 25) color = R.color.q_good;
            else if (it.score < 40) color = R.color.q_ok;
            else if (it.score < 60) color = R.color.q_mid;
            else color = R.color.q_bad;
            GradientDrawable bg = (GradientDrawable)
                    getDrawable(R.drawable.bg_tag).mutate();
            bg.setColor(getColor(color));
            tvTag.setBackground(bg);
            return v;
        }
    }
}
