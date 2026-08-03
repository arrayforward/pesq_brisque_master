package com.example.pesq;

import android.content.Context;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

public final class Brisque {

    private static final int KERNEL_SIZE = 7;
    private static final double SIGMA = 7.0 / 6.0;
    private static final double C = 1.0 / 255.0;
    private static final int FEAT_COUNT = 36;

    private static final double[] MIN = {
            0.336999, 0.019667, 0.23, -0.125959, 0.000167, 0.000616,
            0.231, -0.125873, 0.000165, 0.0006,
            0.241, -0.128814, 0.000179, 0.000386,
            0.243, -0.13308, 0.000182, 0.000421,
            0.436998, 0.016929, 0.247, -0.200231, 0.000104, 0.000834,
            0.257, -0.200017, 0.000112, 0.000876,
            0.257, -0.155072, 0.000112, 0.000356,
            0.258, -0.154374, 0.000117, 0.000351
    };
    private static final double[] MAX = {
            9.999411, 0.807472, 1.644021, 0.202917, 0.712384, 0.468672,
            1.644021, 0.169548, 0.713132, 0.467896,
            1.553016, 0.101368, 0.687324, 0.533087,
            1.554016, 0.101, 0.689177, 0.533133,
            3.639918, 0.800955, 1.096995, 0.175286, 0.755547, 0.39927,
            1.095995, 0.155928, 0.751488, 0.402398,
            1.041992, 0.093209, 0.623516, 0.532925,
            1.042992, 0.093714, 0.621958, 0.534484
    };

    private static Brisque instance;

    private double[][] svs;
    private double[] coefs;
    private double rho;
    private double gamma = 0.05;

    private Brisque() {
    }

    public static synchronized Brisque get(Context ctx) throws IOException {
        if (instance == null) {
            instance = new Brisque();
            instance.loadModel(ctx);
        }
        return instance;
    }

    private void loadModel(Context ctx) throws IOException {
        InputStream in = ctx.getAssets().open("brisque_svm.txt");
        BufferedReader r = new BufferedReader(new InputStreamReader(in));
        List<double[]> svList = new ArrayList<>();
        List<Double> coefList = new ArrayList<>();
        String line;
        boolean svSection = false;
        while ((line = r.readLine()) != null) {
            line = line.trim();
            if (line.isEmpty()) continue;
            if (!svSection) {
                if (line.startsWith("gamma")) {
                    gamma = Double.parseDouble(line.substring(5).trim());
                } else if (line.startsWith("rho")) {
                    rho = Double.parseDouble(line.substring(3).trim());
                } else if (line.equals("SV")) {
                    svSection = true;
                }
                continue;
            }
            String[] parts = line.split("\\s+");
            coefList.add(Double.parseDouble(parts[0]));
            double[] feat = new double[FEAT_COUNT];
            for (int i = 1; i < parts.length; i++) {
                String[] kv = parts[i].split(":");
                int idx = Integer.parseInt(kv[0]) - 1;
                if (idx >= 0 && idx < FEAT_COUNT) {
                    feat[idx] = Double.parseDouble(kv[1]);
                }
            }
            svList.add(feat);
        }
        r.close();
        svs = svList.toArray(new double[0][]);
        coefs = new double[coefList.size()];
        for (int i = 0; i < coefs.length; i++) coefs[i] = coefList.get(i);
    }

    public double score(float[] gray, int w, int h) {
        double[] f1 = features(gray, w, h);
        float[] half = downscaleHalf(gray, w, h);
        double[] f2 = features(half, w / 2, h / 2);
        double[] feats = new double[FEAT_COUNT];
        System.arraycopy(f1, 0, feats, 0, 18);
        System.arraycopy(f2, 0, feats, 18, 18);

        double[] scaled = new double[FEAT_COUNT];
        for (int i = 0; i < FEAT_COUNT; i++) {
            scaled[i] = -1.0 + 2.0 * (feats[i] - MIN[i]) / (MAX[i] - MIN[i]);
        }

        double sum = 0;
        for (int i = 0; i < svs.length; i++) {
            double[] sv = svs[i];
            double d2 = 0;
            for (int j = 0; j < FEAT_COUNT; j++) {
                double d = scaled[j] - sv[j];
                d2 += d * d;
            }
            sum += coefs[i] * Math.exp(-gamma * d2);
        }
        return sum - rho;
    }

    private double[] features(float[] gray, int w, int h) {
        double[] k = gaussianKernel();
        float[] mu = convolveSeparable(gray, w, h, k);
        int n = w * h;
        float[] sq = new float[n];
        for (int i = 0; i < n; i++) sq[i] = gray[i] * gray[i];
        float[] sigmaSq = convolveSeparable(sq, w, h, k);

        float[] mscn = new float[n];
        for (int i = 0; i < n; i++) {
            double v = mu[i] * mu[i];
            double sd = Math.sqrt(Math.abs(sigmaSq[i] - v));
            mscn[i] = (float) ((gray[i] - mu[i]) / (sd + C));
        }

        double[] out = new double[18];
        double[] mscnFit = fitAggd(mscn, 0, 0, w, h);
        out[0] = mscnFit[0];
        out[1] = (mscnFit[2] * mscnFit[2] + mscnFit[3] * mscnFit[3]) / 2.0;

        int[][] shifts = {{0, 1}, {1, 0}, {1, 1}, {1, -1}};
        for (int s = 0; s < 4; s++) {
            double[] fit = fitAggdProduct(mscn, w, h, shifts[s][0], shifts[s][1]);
            out[2 + s * 4] = fit[0];
            out[3 + s * 4] = fit[1];
            out[4 + s * 4] = fit[2] * fit[2];
            out[5 + s * 4] = fit[3] * fit[3];
        }
        return out;
    }

    private static double[] gaussianKernel() {
        double[] k = new double[KERNEL_SIZE];
        int c = KERNEL_SIZE / 2;
        double sum = 0;
        for (int i = 0; i < KERNEL_SIZE; i++) {
            int d = i - c;
            k[i] = Math.exp(-(d * d) / (2 * SIGMA * SIGMA));
            sum += k[i];
        }
        for (int i = 0; i < KERNEL_SIZE; i++) k[i] /= sum;
        return k;
    }

    private static float[] convolveSeparable(float[] img, int w, int h, double[] k) {
        int r = KERNEL_SIZE / 2;
        float[] tmp = new float[w * h];
        for (int y = 0; y < h; y++) {
            int row = y * w;
            for (int x = 0; x < w; x++) {
                double acc = 0;
                for (int j = -r; j <= r; j++) {
                    int xx = x + j;
                    if (xx < 0 || xx >= w) continue;
                    acc += img[row + xx] * k[j + r];
                }
                tmp[row + x] = (float) acc;
            }
        }
        float[] out = new float[w * h];
        for (int y = 0; y < h; y++) {
            int row = y * w;
            for (int x = 0; x < w; x++) {
                double acc = 0;
                for (int j = -r; j <= r; j++) {
                    int yy = y + j;
                    if (yy < 0 || yy >= h) continue;
                    acc += tmp[yy * w + x] * k[j + r];
                }
                out[row + x] = (float) acc;
            }
        }
        return out;
    }

    private static float[] downscaleHalf(float[] img, int w, int h) {
        int ow = w / 2, oh = h / 2;
        float[] out = new float[ow * oh];
        double a = -0.75;
        for (int y = 0; y < oh; y++) {
            double sy = (y + 0.5) * 2.0 - 0.5;
            int iy = (int) Math.floor(sy);
            double fy = sy - iy;
            for (int x = 0; x < ow; x++) {
                double sx = (x + 0.5) * 2.0 - 0.5;
                int ix = (int) Math.floor(sx);
                double fx = sx - ix;
                double acc = 0;
                for (int m = -1; m <= 2; m++) {
                    int yy = clamp(iy + m, 0, h - 1);
                    double wy = cubicWeight(m - fy, a);
                    for (int nn = -1; nn <= 2; nn++) {
                        int xx = clamp(ix + nn, 0, w - 1);
                        double wx = cubicWeight(nn - fx, a);
                        acc += img[yy * w + xx] * wx * wy;
                    }
                }
                out[y * ow + x] = (float) acc;
            }
        }
        return out;
    }

    private static int clamp(int v, int lo, int hi) {
        return v < lo ? lo : (v > hi ? hi : v);
    }

    private static double cubicWeight(double t, double a) {
        t = Math.abs(t);
        if (t <= 1) {
            return (a + 2) * t * t * t - (a + 3) * t * t + 1;
        } else if (t < 2) {
            return a * t * t * t - 5 * a * t * t + 8 * a * t - 4 * a;
        }
        return 0;
    }

    private static double[] fitAggd(float[] data, int x0, int y0, int w, int h) {
        return aggdyFromStats(data, 0, data.length, w, h, null, 0, 0);
    }

    private static double[] fitAggdProduct(float[] mscn, int w, int h, int dy, int dx) {
        int count = (h - Math.abs(dy)) * (w - Math.abs(dx));
        float[] prod = new float[count];
        int idx = 0;
        int yStart = dy >= 0 ? 0 : -dy;
        int xStart = dx >= 0 ? 0 : -dx;
        for (int y = 0; y < h - Math.abs(dy); y++) {
            for (int x = 0; x < w - Math.abs(dx); x++) {
                int yy = yStart + y;
                int xx = xStart + x;
                prod[idx++] = mscn[yy * w + xx] * mscn[(yy + dy) * w + (xx + dx)];
            }
        }
        return aggdyFromStats(prod, 0, prod.length, w, h, null, 0, 0);
    }

    private static double[] aggdyFromStats(float[] x, int start, int len, int w, int h,
                                           float[] unused, int u0, int u1) {
        double sumAbs = 0, sumSq = 0, lsq = 0, rsq = 0;
        int lc = 0, rc = 0;
        for (int i = start; i < start + len; i++) {
            double v = x[i];
            double av = Math.abs(v);
            sumAbs += av;
            sumSq += v * v;
            if (v < 0) {
                lsq += v * v;
                lc++;
            } else {
                rsq += v * v;
                rc++;
            }
        }
        if (lc == 0) lc = 1;
        if (rc == 0) rc = 1;
        double n = len;
        double rhat = (sumAbs / n) * (sumAbs / n) / (sumSq / n);
        double gam = Math.sqrt(lsq / lc) / Math.sqrt(rsq / rc);
        double rhatnorm = rhat * (Math.pow(gam, 3) + 1) * (gam + 1)
                / Math.pow(gam * gam + 1, 2);

        double alpha = solveAlpha(rhatnorm);
        double sigL = Math.sqrt(lsq / lc);
        double sigR = Math.sqrt(rsq / rc);
        double g1 = gammaFn(1.0 / alpha);
        double g2 = gammaFn(2.0 / alpha);
        double g3 = gammaFn(3.0 / alpha);
        double constant = Math.sqrt(g1 / g3);
        double mean = (sigR - sigL) * constant * (g2 / g1);
        return new double[]{alpha, mean, sigL, sigR};
    }

    private static double phi(double alpha) {
        double g2 = gammaFn(2.0 / alpha);
        return g2 * g2 / (gammaFn(1.0 / alpha) * gammaFn(3.0 / alpha));
    }

    private static double solveAlpha(double target) {
        double lo = 0.2, hi = 10.0;
        double flo = phi(lo) - target;
        double fhi = phi(hi) - target;
        if (flo == 0) return lo;
        if (fhi == 0) return hi;
        if (flo * fhi > 0) {
            return Math.abs(flo) < Math.abs(fhi) ? lo : hi;
        }
        for (int i = 0; i < 80; i++) {
            double mid = 0.5 * (lo + hi);
            double fm = phi(mid) - target;
            if (fm == 0) return mid;
            if (flo * fm < 0) {
                hi = mid;
                fhi = fm;
            } else {
                lo = mid;
                flo = fm;
            }
        }
        return 0.5 * (lo + hi);
    }

    private static final double[] LANCZOS = {
            0.99999999999980993, 676.5203681218851, -1259.1392167224028,
            771.32342877765313, -176.61502916214059, 12.507343278686905,
            -0.13857109526572012, 9.9843695780195716e-6, 1.5056327351493116e-7
    };

    private static double gammaFn(double z) {
        if (z < 0.5) {
            return Math.PI / (Math.sin(Math.PI * z) * gammaFn(1 - z));
        }
        z -= 1;
        double x = LANCZOS[0];
        for (int i = 1; i < LANCZOS.length; i++) {
            x += LANCZOS[i] / (z + i);
        }
        double t = z + 7.5;
        return Math.sqrt(2 * Math.PI) * Math.pow(t, z + 0.5) * Math.exp(-t) * x;
    }

    public static String format(double score) {
        return String.format(Locale.US, "%.2f", score);
    }
}
