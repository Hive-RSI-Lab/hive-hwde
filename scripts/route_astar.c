/* The grid search in route_finish.py's astar(), about 50 times faster: a
 * whole-board grid at 0.05 mm is 10M nodes. Same nodes, costs and tie-breaks
 * as the Python, so the same path. Built on first use (route_finish.py). */
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <time.h>

typedef struct { double f, g; int32_t n; } Ent;
typedef struct { Ent *a; size_t len, cap; } Heap;

static int less(const Ent *x, const Ent *y)
{
    if (x->f != y->f) return x->f < y->f;
    if (x->g != y->g) return x->g < y->g;
    return x->n < y->n;
}

static int push(Heap *h, double f, double g, int32_t n)
{
    if (h->len == h->cap) {
        size_t cap = h->cap ? h->cap * 2 : 1 << 16;
        Ent *a = realloc(h->a, cap * sizeof(Ent));
        if (!a) return -1;
        h->a = a;
        h->cap = cap;
    }
    size_t i = h->len++;
    Ent e = {f, g, n};
    while (i) {
        size_t p = (i - 1) / 2;
        if (!less(&e, &h->a[p])) break;
        h->a[i] = h->a[p];
        i = p;
    }
    h->a[i] = e;
    return 0;
}

static Ent pop(Heap *h)
{
    Ent top = h->a[0], e = h->a[--h->len];
    size_t i = 0, n = h->len;
    for (;;) {
        size_t c = 2 * i + 1;
        if (c >= n) break;
        if (c + 1 < n && less(&h->a[c + 1], &h->a[c])) c++;
        if (!less(&h->a[c], &e)) break;
        h->a[i] = h->a[c];
        i = c;
    }
    if (n) h->a[i] = e;
    return top;
}

static double now(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec * 1e-9;
}

enum { FOUND = 0, EXHAUSTED = 1, GAVE_UP = 2, DEADLINE = 3, NOMEM = 4 };
#define PUSH(F, G, N) do { if (push(&h, (F), (G), (N))) { rc = NOMEM; goto done; } } while (0)

/* prev has K*nx*ny entries; *found is the end node, negative for a via drop
 * into B's plane (-2 - node). deadline < 0: none. */
int route_astar(int nx, int ny, int K, const uint8_t **blk, const uint8_t *vblk,
                const double **cost, const uint8_t **in_a, const uint8_t **in_b,
                const uint8_t *a_plane, const uint8_t *b_plane, double via_cost,
                long max_nodes, double deadline, double weight, int ti, int tj,
                int32_t *prev, int32_t *found)
{
    static const int DI[8] = {1, -1, 0, 0, 1, 1, -1, -1};
    static const int DJ[8] = {0, 0, 1, -1, 1, -1, 1, -1};
    const double DIAG = sqrt(2.0);
    int64_t N = (int64_t)nx * ny;
    int64_t total = K * N;
    Heap h = {0};
    int rc = EXHAUSTED;
    double *dist = malloc(total * sizeof(double));
    uint8_t *via = malloc(N);
    if (!dist || !via) { rc = NOMEM; goto done; }
    for (int64_t n = 0; n < total; n++) dist[n] = INFINITY;
    for (int64_t c = 0; c < N; c++) {
        via[c] = !vblk[c];
        for (int k = 0; k < K && via[c]; k++)
            if (blk[k][c]) via[c] = 0;
    }
#define H(C) (weight * (fmax(abs((int)((C) % nx) - ti), abs((int)((C) / nx) - tj)) \
             + (DIAG - 1) * fmin(abs((int)((C) % nx) - ti), abs((int)((C) / nx) - tj))))
    for (int k = 0; k < K; k++)
        for (int64_t c = 0; c < N; c++)
            if (in_a[k][c] && !blk[k][c]) {
                int64_t n = k * N + c;
                dist[n] = 0.0;
                prev[n] = -1;
                PUSH(H(c), 0.0, (int32_t)n);
            }
    for (int64_t c = 0; c < N; c++)
        if (a_plane[c] && via[c])
            for (int k = 0; k < K; k++) {
                int64_t n = k * N + c;
                if (via_cost < dist[n]) {
                    dist[n] = via_cost;
                    prev[n] = (int32_t)(-2 - c);
                    PUSH(via_cost + H(c), via_cost, (int32_t)n);
                }
            }
    long expanded = 0;
    while (h.len) {
        Ent e = pop(&h);
        if (e.n < 0) { *found = e.n; rc = FOUND; goto done; }
        if (e.g > dist[e.n]) continue;
        int k = (int)(e.n / N);
        int64_t c = e.n % N;
        if (in_b[k][c]) { *found = e.n; rc = FOUND; goto done; }
        if (++expanded > max_nodes) { rc = GAVE_UP; goto done; }
        if (deadline >= 0 && !(expanded & 0x3FFF) && now() >= deadline) {
            rc = DEADLINE;
            goto done;
        }
        int i = (int)(c % nx), j = (int)(c / nx);
        const uint8_t *b = blk[k];
        for (int m = 0; m < 8; m++) {
            int di = DI[m], dj = DJ[m], ii = i + di, jj = j + dj;
            if (ii < 0 || ii >= nx || jj < 0 || jj >= ny) continue;
            int64_t c2 = (int64_t)jj * nx + ii;
            if (b[c2]) continue;
            if (di && dj && b[(int64_t)j * nx + ii] && b[(int64_t)jj * nx + i]) continue;
            double ng = e.g + (di && dj ? DIAG : 1.0) * cost[k][c2];
            int64_t n2 = k * N + c2;
            if (ng < dist[n2]) {
                dist[n2] = ng;
                prev[n2] = e.n;
                PUSH(ng + H(c2), ng, (int32_t)n2);
            }
        }
        if (via[c]) {
            double ng = e.g + via_cost;
            for (int k2 = 0; k2 < K; k2++) {
                if (k2 == k) continue;
                int64_t n2 = k2 * N + c;
                if (ng < dist[n2]) {
                    dist[n2] = ng;
                    prev[n2] = e.n;
                    PUSH(ng + H(c), ng, (int32_t)n2);
                }
            }
            /* first drop to reach this cell's node wins, as with the dict */
            if (b_plane[c]) PUSH(ng, ng, (int32_t)(-2 - e.n));
        }
    }
done:
    free(h.a);
    free(dist);
    free(via);
    return rc;
}
