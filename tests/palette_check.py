"""Port of the dataviz six-checks validator (no node available).

OKLab distance, Vienot-1999 dichromat simulation, WCAG contrast.
"""
import itertools, math, sys

def srgb_to_lin(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

def hex_rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def lin_rgb(h):
    return tuple(srgb_to_lin(v) for v in hex_rgb(h))

def oklab(h):
    r, g, b = lin_rgb(h)
    l = 0.4122214708*r + 0.5363325363*g + 0.0514459929*b
    m = 0.2119034982*r + 0.6806995451*g + 0.1073969566*b
    s = 0.0883024619*r + 0.2817188376*g + 0.6299787005*b
    l_, m_, s_ = l ** (1/3), m ** (1/3), s ** (1/3)
    return (0.2104542553*l_ + 0.7936177850*m_ - 0.0040720468*s_,
            1.9779984951*l_ - 2.4285922050*m_ + 0.4505937099*s_,
            0.0259040371*l_ + 0.7827717662*m_ - 0.8086757660*s_)

def chroma(h):
    L, a, b = oklab(h)
    return math.hypot(a, b)

def dE(h1, h2):
    a, b = oklab(h1), oklab(h2)
    return 100 * math.dist(a, b)

# Vienot/Brettel-style dichromat simulation in linear RGB
CVD = {
 'deuteranopia': ((0.625,0.375,0.0),(0.70,0.30,0.0),(0.0,0.30,0.70)),
 'protanopia':   ((0.1115,0.8885,0.0),(0.1115,0.8885,0.0),(0.0,0.0,1.0)),
 'tritanopia':   ((1.0,0.1667,-0.1667),(0.0,1.0,0.0),(0.0,0.0,1.0)),
}

def lin_to_hex(r, g, b):
    def enc(c):
        c = max(0.0, min(1.0, c))
        c = 12.92*c if c <= 0.0031308 else 1.055*(c ** (1/2.4)) - 0.055
        return int(round(max(0, min(1, c)) * 255))
    return '#%02X%02X%02X' % (enc(r), enc(g), enc(b))

def simulate(h, kind):
    r, g, b = lin_rgb(h)
    M = CVD[kind]
    return lin_to_hex(M[0][0]*r+M[0][1]*g+M[0][2]*b,
                      M[1][0]*r+M[1][1]*g+M[1][2]*b,
                      M[2][0]*r+M[2][1]*g+M[2][2]*b)

def rel_lum(h):
    r, g, b = lin_rgb(h)
    return 0.2126*r + 0.7152*g + 0.0722*b

def contrast(h1, h2):
    a, b = rel_lum(h1), rel_lum(h2)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)

def report(palette, surface, mode):
    print("\n=== %s  (surface %s) ===" % (mode.upper(), surface))
    ok = True
    for h in palette:
        L = oklab(h)[0]; C = chroma(h); ct = contrast(h, surface)
        band = (0.45 <= L <= 0.75) if mode == 'light' else (0.55 <= L <= 0.85)
        f_band = "pass" if band else "WARN"
        f_chr = "pass" if C >= 0.05 else "FAIL"
        f_ct = "pass" if ct >= 3.0 else ("WARN" if ct >= 2.0 else "FAIL")
        if f_chr == "FAIL" or f_ct == "FAIL":
            ok = False
        print("  %s  L=%.3f %-4s  C=%.3f %-4s  contrast=%.2f %-4s"
              % (h, L, f_band, C, f_chr, ct, f_ct))
    print("  -- adjacent / all pairs --")
    for h1, h2 in itertools.combinations(palette, 2):
        nv = dE(h1, h2)
        worst_kind, worst = None, 999
        for k in CVD:
            d = dE(simulate(h1, k), simulate(h2, k))
            if d < worst:
                worst, worst_kind = d, k
        v_nv = "pass" if nv >= 15 else "FAIL(normal-vision floor)"
        v_cvd = "pass" if worst >= 8 else ("floor-only" if worst >= 6 else "FAIL")
        if "FAIL" in v_nv or v_cvd == "FAIL":
            ok = False
        print("  %s vs %s  dE_normal=%5.1f %-26s  dE_%s=%5.1f %s"
              % (h1, h2, nv, v_nv, worst_kind[:5], worst, v_cvd))
    print("  RESULT:", "PASS" if ok else "FAIL")
    return ok

if __name__ == '__main__':
    light_ok = report(["#8E2F55", "#1F6F6B", "#A8762A"], "#F7F3F1", "light")
    dark_ok = report(["#E58AAC", "#5FC3BB", "#D9A75A"], "#17131A", "dark")
    sys.exit(0 if (light_ok and dark_ok) else 1)
