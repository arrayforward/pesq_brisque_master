package com.example.pesq;

import java.util.Arrays;
import java.util.Locale;

public final class AudioQuality {

    public static class Result {
        public double score;
        public double bandwidthHz;
        public double bandwidth99Hz;
        public double snrDb;
        public double clipRatio;
        public double dynamicDb;
        public double activeRatio;
        public double rmsDb;

        public String detail() {
            return String.format(Locale.US,
                    "带宽95=%.1fkHz 带宽99=%.1fkHz 底噪SNR=%.1fdB 动态=%.1fdB 削波=%.2f%% 有效发声=%.0f%% 电平=%.1fdBFS",
                    bandwidthHz / 1000.0, bandwidth99Hz / 1000.0, snrDb, dynamicDb,
                    clipRatio * 100, activeRatio * 100, rmsDb);
        }
    }

    private static final int N = 2048;
    private static final int HOP = 1024;

    public static Result analyze(short[] pcm, int rate) {
        Result res = new Result();
        if (pcm == null || pcm.length < N * 2) {
            res.score = 0;
            return res;
        }

        int nFrames = (pcm.length - N) / HOP + 1;
        double[] frameRms = new double[nFrames];
        double[] frameRolloff = new double[nFrames];
        double[] frameRolloff99 = new double[nFrames];
        double[] hann = new double[N];
        for (int i = 0; i < N; i++) {
            hann[i] = 0.5 * (1 - Math.cos(2 * Math.PI * i / (N - 1)));
        }
        double[] re = new double[N];
        double[] im = new double[N];
        double binHz = (double) rate / N;

        int clipCount = 0;
        for (short s : pcm) {
            if (s >= 32000 || s <= -32000) clipCount++;
        }
        res.clipRatio = (double) clipCount / pcm.length;

        double maxRms = 1e-9;
        for (int f = 0; f < nFrames; f++) {
            double acc = 0;
            int base = f * HOP;
            for (int i = 0; i < N; i++) {
                double v = pcm[base + i] / 32768.0;
                acc += v * v;
                re[i] = v * hann[i];
                im[i] = 0;
            }
            frameRms[f] = Math.sqrt(acc / N);
            if (frameRms[f] > maxRms) maxRms = frameRms[f];

            fft(re, im);
            double total = 0;
            int nb = N / 2;
            double[] mag2 = new double[nb];
            for (int i = 0; i < nb; i++) {
                mag2[i] = re[i] * re[i] + im[i] * im[i];
                total += mag2[i];
            }
            double target = total * 0.95;
            double target99 = total * 0.99;
            double cum = 0;
            int roll = nb - 1, roll99 = nb - 1;
            for (int i = 0; i < nb; i++) {
                cum += mag2[i];
                if (cum >= target && roll == nb - 1) roll = i;
                if (cum >= target99) {
                    roll99 = i;
                    break;
                }
            }
            frameRolloff[f] = roll * binHz;
            frameRolloff99[f] = roll99 * binHz;
        }

        double activeTh = maxRms * 0.05;
        int active = 0;
        double sumActiveRms = 0;
        for (int f = 0; f < nFrames; f++) {
            if (frameRms[f] > activeTh) {
                active++;
                sumActiveRms += frameRms[f];
            }
        }
        res.activeRatio = (double) active / nFrames;
        double avgActiveRms = active > 0 ? sumActiveRms / active : 1e-9;
        res.rmsDb = 20 * Math.log10(avgActiveRms + 1e-9);

        double[] activeRolloff = new double[Math.max(active, 1)];
        double[] activeRolloff99 = new double[Math.max(active, 1)];
        int idx = 0;
        for (int f = 0; f < nFrames; f++) {
            if (frameRms[f] > activeTh) {
                activeRolloff[idx] = frameRolloff[f];
                activeRolloff99[idx] = frameRolloff99[f];
                idx++;
            }
        }
        Arrays.sort(activeRolloff, 0, idx);
        Arrays.sort(activeRolloff99, 0, idx);
        res.bandwidthHz = idx > 0 ? activeRolloff[idx / 2] : 0;
        res.bandwidth99Hz = idx > 0 ? activeRolloff99[idx / 2] : 0;

        double[] sortedRms = frameRms.clone();
        Arrays.sort(sortedRms);
        int q = Math.max(1, nFrames / 10);
        double quietSum = 0;
        for (int i = 0; i < q; i++) quietSum += sortedRms[i];
        double noiseRms = quietSum / q;
        res.snrDb = clamp(20 * Math.log10((avgActiveRms + 1e-9) / (noiseRms + 1e-9)), 0, 60);

        double p10 = sortedRms[Math.min(nFrames - 1, (int) (nFrames * 0.10))];
        double p95 = sortedRms[Math.min(nFrames - 1, (int) (nFrames * 0.95))];
        res.dynamicDb = clamp(20 * Math.log10((p95 + 1e-9) / (p10 + 1e-9)), 0, 60);

        double nyq = rate / 2.0;
        double bwScore = clamp((res.bandwidth99Hz - 3500.0) / (nyq * 0.97 - 3500.0) * 100.0, 0, 100);
        double snrScore = clamp((res.snrDb - 8.0) / (40.0 - 8.0) * 100.0, 0, 100);
        double dynScore = clamp((res.dynamicDb - 4.0) / (16.0 - 4.0) * 100.0, 0, 100);
        double clipPenalty = clamp(res.clipRatio / 0.01, 0, 1) * 30.0;
        double levelPenalty = res.rmsDb < -50 ? 20 : 0;

        res.score = clamp(0.5 * bwScore + 0.3 * snrScore + 0.2 * dynScore
                - clipPenalty - levelPenalty, 0, 100);
        return res;
    }

    private static double clamp(double v, double lo, double hi) {
        return v < lo ? lo : (v > hi ? hi : v);
    }

    private static void fft(double[] re, double[] im) {
        int n = re.length;
        for (int i = 1, j = 0; i < n; i++) {
            int bit = n >> 1;
            for (; (j & bit) != 0; bit >>= 1) j ^= bit;
            j ^= bit;
            if (i < j) {
                double t = re[i];
                re[i] = re[j];
                re[j] = t;
                t = im[i];
                im[i] = im[j];
                im[j] = t;
            }
        }
        for (int len = 2; len <= n; len <<= 1) {
            double ang = -2 * Math.PI / len;
            double wr = Math.cos(ang), wi = Math.sin(ang);
            int half = len >> 1;
            for (int i = 0; i < n; i += len) {
                double cwr = 1, cwi = 0;
                for (int j = 0; j < half; j++) {
                    int a = i + j, b = i + j + half;
                    double vr = re[b] * cwr - im[b] * cwi;
                    double vi = re[b] * cwi + im[b] * cwr;
                    re[b] = re[a] - vr;
                    im[b] = im[a] - vi;
                    re[a] += vr;
                    im[a] += vi;
                    double nwr = cwr * wr - cwi * wi;
                    cwi = cwr * wi + cwi * wr;
                    cwr = nwr;
                }
            }
        }
    }
}
