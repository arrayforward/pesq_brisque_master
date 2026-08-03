#include <jni.h>

#include <cmath>
#include <cstdint>
#include <cstring>
#include <map>
#include <string>
#include <vector>

namespace {

struct Tensor {
    std::vector<int> dims;
    std::vector<float> data;
};

std::map<std::string, Tensor> g_weights;
bool g_loaded = false;

uint32_t readU32(const uint8_t *&p) {
    uint32_t v;
    memcpy(&v, p, 4);
    p += 4;
    return v;
}

bool loadWeights(const uint8_t *buf, size_t len) {
    const uint8_t *p = buf;
    if (len < 8) return false;
    if (readU32(p) != 0x4d4f5357) return false;
    uint32_t count = readU32(p);
    g_weights.clear();
    for (uint32_t i = 0; i < count; i++) {
        uint32_t nameLen = readU32(p);
        if (p + nameLen > buf + len) return false;
        std::string name((const char *) p, nameLen);
        p += nameLen;
        uint32_t ndims = readU32(p);
        Tensor t;
        size_t n = 1;
        for (uint32_t d = 0; d < ndims; d++) {
            int dim = (int) readU32(p);
            t.dims.push_back(dim);
            n *= (size_t) dim;
        }
        if (p + n * 4 > buf + len) return false;
        t.data.resize(n);
        memcpy(t.data.data(), p, n * 4);
        p += n * 4;
        g_weights[name] = std::move(t);
    }
    return true;
}

const Tensor &W(const std::string &name) {
    return g_weights.at(name);
}

inline float sigmoidf(float x) {
    if (x >= 0) {
        return 1.0f / (1.0f + expf(-x));
    }
    float e = expf(x);
    return e / (1.0f + e);
}

void fftRadix2(std::vector<float> &re, std::vector<float> &im) {
    int n = (int) re.size();
    for (int i = 1, j = 0; i < n; i++) {
        int bit = n >> 1;
        for (; j & bit; bit >>= 1) j ^= bit;
        j ^= bit;
        if (i < j) {
            std::swap(re[i], re[j]);
            std::swap(im[i], im[j]);
        }
    }
    for (int len = 2; len <= n; len <<= 1) {
        double ang = -2.0 * M_PI / len;
        float wr = (float) cos(ang), wi = (float) sin(ang);
        int half = len >> 1;
        for (int i = 0; i < n; i += len) {
            float cwr = 1, cwi = 0;
            for (int j = 0; j < half; j++) {
                int a = i + j, b = i + j + half;
                float vr = re[b] * cwr - im[b] * cwi;
                float vi = re[b] * cwi + im[b] * cwr;
                re[b] = re[a] - vr;
                im[b] = im[a] - vi;
                re[a] += vr;
                im[a] += vi;
                float nwr = cwr * wr - cwi * wi;
                cwi = cwr * wi + cwi * wr;
                cwr = nwr;
            }
        }
    }
}

const int FFT_N = 512;
const int HOP = 256;
const int SGRAM = 257;

std::vector<float> stftMag(const int16_t *pcm, int n, int &framesOut) {
    int frames = n / HOP + 1;
    framesOut = frames;
    std::vector<float> mag((size_t) frames * SGRAM);
    std::vector<float> win(FFT_N);
    for (int i = 0; i < FFT_N; i++) {
        win[i] = (float) (0.54 - 0.46 * cos(2.0 * M_PI * i / (FFT_N - 1)));
    }
    auto sampleAt = [&](int idx) -> float {
        if (idx < 0) idx = -idx;
        else if (idx >= n) idx = 2 * n - 2 - idx;
        if (idx < 0) idx = 0;
        if (idx >= n) idx = n - 1;
        return pcm[idx] / 32768.0f;
    };
    std::vector<float> re(FFT_N), im(FFT_N);
    for (int f = 0; f < frames; f++) {
        int start = f * HOP - HOP;
        for (int i = 0; i < FFT_N; i++) {
            re[i] = sampleAt(start + i) * win[i];
            im[i] = 0;
        }
        fftRadix2(re, im);
        for (int k = 0; k < SGRAM; k++) {
            mag[(size_t) f * SGRAM + k] = sqrtf(re[k] * re[k] + im[k] * im[k]);
        }
    }
    return mag;
}

void conv2d3x3(const float *in, int T, int F, int Cin,
               const float *kernel, const float *bias,
               int strideF, float *out, int Fout, int Cout) {
    int padT = 1;
    int padF;
    if (strideF == 1) {
        padF = 1;
    } else {
        int padTotal = (Fout - 1) * strideF + 3 - F;
        if (padTotal < 0) padTotal = 0;
        padF = padTotal / 2;
    }
    for (int t = 0; t < T; t++) {
        for (int fo = 0; fo < Fout; fo++) {
            float *outRow = out + ((size_t) t * Fout + fo) * Cout;
            for (int co = 0; co < Cout; co++) outRow[co] = bias[co];
            for (int ky = 0; ky < 3; ky++) {
                int ti = t + ky - padT;
                if (ti < 0 || ti >= T) continue;
                for (int kx = 0; kx < 3; kx++) {
                    int fi = fo * strideF + kx - padF;
                    if (fi < 0 || fi >= F) continue;
                    const float *inRow = in + ((size_t) ti * F + fi) * Cin;
                    const float *kBase = kernel + ((size_t) ky * 3 + kx) * Cin * Cout;
                    for (int ci = 0; ci < Cin; ci++) {
                        float iv = inRow[ci];
                        const float *k = kBase + (size_t) ci * Cout;
                        for (int co = 0; co < Cout; co++) {
                            outRow[co] += iv * k[co];
                        }
                    }
                }
            }
            for (int co = 0; co < Cout; co++) {
                if (outRow[co] < 0) outRow[co] = 0;
            }
        }
    }
}

void lstmDirection(const float *x, int T, int xDim,
                   const float *K, const float *R, const float *b,
                   float *hOut, int units, bool backward) {
    std::vector<float> h(units, 0), c(units, 0), z(4 * units);
    for (int step = 0; step < T; step++) {
        int t = backward ? (T - 1 - step) : step;
        const float *xt = x + (size_t) t * xDim;
        for (int g = 0; g < 4 * units; g++) z[g] = b[g];
        for (int i = 0; i < xDim; i++) {
            float xv = xt[i];
            const float *krow = K + (size_t) i * 4 * units;
            for (int g = 0; g < 4 * units; g++) z[g] += xv * krow[g];
        }
        for (int i = 0; i < units; i++) {
            float hv = h[i];
            const float *rrow = R + (size_t) i * 4 * units;
            for (int g = 0; g < 4 * units; g++) z[g] += hv * rrow[g];
        }
        for (int u = 0; u < units; u++) {
            float ig = sigmoidf(z[u]);
            float fg = sigmoidf(z[units + u]);
            float cg = tanhf(z[2 * units + u]);
            float og = sigmoidf(z[3 * units + u]);
            c[u] = fg * c[u] + ig * cg;
            h[u] = og * tanhf(c[u]);
        }
        memcpy(hOut + (size_t) t * units, h.data(), units * sizeof(float));
    }
}

float mosnetScore(const int16_t *pcm, int n) {
    int T = 0;
    std::vector<float> mag = stftMag(pcm, n, T);
    if (T <= 0) return -1;

    const int convCh[12] = {16, 16, 16, 32, 32, 32, 64, 64, 64, 128, 128, 128};
    const int convStride[12] = {1, 1, 3, 1, 1, 3, 1, 1, 3, 1, 1, 3};

    float *cur = (float *) malloc((size_t) T * SGRAM * sizeof(float));
    for (int t = 0; t < T; t++) {
        for (int f = 0; f < SGRAM; f++) {
            cur[(size_t) t * SGRAM + f] = mag[(size_t) t * SGRAM + f];
        }
    }
    int curF = SGRAM, curC = 1;

    for (int l = 0; l < 12; l++) {
        int cout = convCh[l];
        int stride = convStride[l];
        int fout = (curF + stride - 1) / stride;
        const Tensor &k = W("conv2d_" + std::to_string(l + 1) + "/kernel");
        const Tensor &b = W("conv2d_" + std::to_string(l + 1) + "/bias");
        float *next = (float *) malloc((size_t) T * fout * cout * sizeof(float));
        conv2d3x3(cur, T, curF, curC, k.data.data(), b.data.data(),
                  stride, next, fout, cout);
        free(cur);
        cur = next;
        curF = fout;
        curC = cout;
    }

    int xDim = curF * curC;

    const Tensor &kF = W("lstm_f/kernel");
    const Tensor &rF = W("lstm_f/recurrent");
    const Tensor &bF = W("lstm_f/bias");
    const Tensor &kB = W("lstm_b/kernel");
    const Tensor &rB = W("lstm_b/recurrent");
    const Tensor &bB = W("lstm_b/bias");

    int units = 128;
    std::vector<float> hF((size_t) T * units), hB((size_t) T * units);
    lstmDirection(cur, T, xDim, kF.data.data(), rF.data.data(), bF.data.data(),
                  hF.data(), units, false);
    lstmDirection(cur, T, xDim, kB.data.data(), rB.data.data(), bB.data.data(),
                  hB.data(), units, true);
    free(cur);

    const Tensor &kD1 = W("dense1/kernel");
    const Tensor &bD1 = W("dense1/bias");
    const Tensor &kD2 = W("dense2/kernel");
    const Tensor &bD2 = W("dense2/bias");

    std::vector<float> d1(128);
    double sum = 0;
    for (int t = 0; t < T; t++) {
        const float *hf = hF.data() + (size_t) t * units;
        const float *hb = hB.data() + (size_t) t * units;
        for (int j = 0; j < 128; j++) d1[j] = bD1.data[j];
        for (int i = 0; i < units; i++) {
            float vf = hf[i], vb = hb[i];
            const float *krow = kD1.data.data() + (size_t) i * 128;
            const float *krow2 = krow + (size_t) units * 128;
            for (int j = 0; j < 128; j++) {
                d1[j] += vf * krow[j] + vb * krow2[j];
            }
        }
        float s = bD2.data[0];
        for (int j = 0; j < 128; j++) {
            if (d1[j] < 0) d1[j] = 0;
            s += d1[j] * kD2.data[j];
        }
        sum += s;
    }
    return (float) (sum / T);
}

} // namespace

extern "C" {

JNIEXPORT jboolean JNICALL
Java_com_example_pesq_MosNet_nativeInit(JNIEnv *env, jclass clazz, jbyteArray weights) {
    jsize len = env->GetArrayLength(weights);
    jbyte *data = env->GetByteArrayElements(weights, nullptr);
    bool ok = loadWeights((const uint8_t *) data, (size_t) len);
    env->ReleaseByteArrayElements(weights, data, JNI_ABORT);
    g_loaded = ok;
    return ok ? JNI_TRUE : JNI_FALSE;
}

JNIEXPORT jfloat JNICALL
Java_com_example_pesq_MosNet_nativeMeasure(JNIEnv *env, jclass clazz, jshortArray pcm) {
    if (!g_loaded) return -1.0f;
    jsize len = env->GetArrayLength(pcm);
    jshort *data = env->GetShortArrayElements(pcm, nullptr);
    float result = mosnetScore((const int16_t *) data, (int) len);
    env->ReleaseShortArrayElements(pcm, data, JNI_ABORT);
    return result;
}

}
