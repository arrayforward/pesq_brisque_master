package com.example.pesq;

public final class P563 {

    static {
        System.loadLibrary("p563");
    }

    private P563() {
    }

    public static native float nativeMeasure(short[] pcm8k);

    private static final int TAPS = 31;
    private static final double[] FIR = buildFir();

    private static double[] buildFir() {
        double[] h = new double[TAPS];
        int m = TAPS - 1;
        double fc = 3400.0 / 16000.0;
        for (int i = 0; i < TAPS; i++) {
            double x = i - m / 2.0;
            double sinc = x == 0 ? 2 * fc : Math.sin(2 * Math.PI * fc * x) / (Math.PI * x);
            double win = 0.54 - 0.46 * Math.cos(2 * Math.PI * i / m);
            h[i] = sinc * win;
        }
        double sum = 0;
        for (double v : h) sum += v;
        for (int i = 0; i < TAPS; i++) h[i] /= sum;
        return h;
    }

    public static short[] downsample16to8(short[] in) {
        int n = in.length / 2;
        short[] out = new short[n];
        int half = TAPS / 2;
        for (int i = 0; i < n; i++) {
            int center = i * 2;
            double acc = 0;
            for (int k = -half; k <= half; k++) {
                int idx = center + k;
                if (idx < 0) idx = 0;
                if (idx >= in.length) idx = in.length - 1;
                acc += in[idx] * FIR[k + half];
            }
            if (acc > 32767) acc = 32767;
            if (acc < -32768) acc = -32768;
            out[i] = (short) acc;
        }
        return out;
    }
}
