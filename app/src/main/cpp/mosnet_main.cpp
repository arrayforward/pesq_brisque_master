#include <cstdio>
#include <cstdlib>
#include <vector>

#include "mosnet.cpp"

int main(int argc, char **argv) {
    if (argc < 3) {
        printf("usage: mosnet_main weights.bin audio.s16le\n");
        return 1;
    }
    FILE *f = fopen(argv[1], "rb");
    fseek(f, 0, SEEK_END);
    long wlen = ftell(f);
    fseek(f, 0, SEEK_SET);
    std::vector<uint8_t> wbuf(wlen);
    fread(wbuf.data(), 1, wlen, f);
    fclose(f);
    if (!loadWeights(wbuf.data(), wbuf.size())) {
        printf("weight load failed\n");
        return 1;
    }
    FILE *a = fopen(argv[2], "rb");
    fseek(a, 0, SEEK_END);
    long alen = ftell(a);
    fseek(a, 0, SEEK_SET);
    std::vector<int16_t> pcm(alen / 2);
    fread(pcm.data(), 1, alen, a);
    fclose(a);
    float mos = mosnetScore(pcm.data(), (int) pcm.size());
    printf("MOS=%.3f samples=%zd\n", mos, pcm.size());
    return 0;
}
