/**
 * Client-side photo clean-up, run on every picture before it is uploaded.
 *
 * Phones and cheap cameras give dark, tilted, sideways or slightly blurry pictures. Before the
 * photo leaves the device we:
 *   1. turn a sideways phone photo the right way up (EXIF orientation),
 *   2. shrink it so uploads stay small,
 *   3. brighten and add contrast to dull / dark photos (auto-levels),
 *   4. sharpen mild blur (unsharp mask),
 *   5. for documents (invoice / bill / slip / receipt / proof) straighten the tilt and trim the
 *      blank margins so the paper sits square in the frame.
 *
 * Every step is defensive: on any error we fall back to a plain resized JPEG, and we never throw.
 */

type Canvas = HTMLCanvasElement;

/** Load the file honouring EXIF orientation (so sideways phone photos come up straight) and
 *  draw it onto a canvas shrunk so the longest side is ≤ max. */
async function load(file: File, max: number): Promise<Canvas> {
  let src: ImageBitmap | HTMLImageElement;
  let sw: number;
  let sh: number;
  try {
    const bmp = await createImageBitmap(file, { imageOrientation: "from-image" });
    src = bmp;
    sw = bmp.width;
    sh = bmp.height;
  } catch {
    const url = URL.createObjectURL(file);
    try {
      const img = await new Promise<HTMLImageElement>((res, rej) => {
        const i = new Image();
        i.onload = () => res(i);
        i.onerror = rej;
        i.src = url;
      });
      src = img;
      sw = img.naturalWidth || img.width;
      sh = img.naturalHeight || img.height;
    } finally {
      URL.revokeObjectURL(url);
    }
  }
  const scale = Math.min(1, max / Math.max(sw, sh || 1));
  const c = document.createElement("canvas");
  c.width = Math.max(1, Math.round(sw * scale));
  c.height = Math.max(1, Math.round(sh * scale));
  c.getContext("2d")!.drawImage(src as CanvasImageSource, 0, 0, c.width, c.height);
  if (typeof (src as ImageBitmap).close === "function") (src as ImageBitmap).close();
  return c;
}

/** Luminance (0–255) of every pixel, for skew / crop detection. */
function luma(d: ImageData): Float32Array {
  const n = d.width * d.height;
  const g = new Float32Array(n);
  const p = d.data;
  for (let i = 0; i < n; i++) g[i] = 0.299 * p[i * 4] + 0.587 * p[i * 4 + 1] + 0.114 * p[i * 4 + 2];
  return g;
}

/** Find the paper's tilt (degrees, −7..7) by the angle whose horizontal "ink" profile is sharpest
 *  (text lines line up into rows). Returns 0 when nothing clear is found. */
function detectSkew(c: Canvas): number {
  try {
    // work on a small copy for speed
    const W = 320;
    const scale = Math.min(1, W / c.width);
    const w = Math.max(1, Math.round(c.width * scale));
    const h = Math.max(1, Math.round(c.height * scale));
    const t = document.createElement("canvas");
    t.width = w;
    t.height = h;
    t.getContext("2d")!.drawImage(c, 0, 0, w, h);
    const g = luma(t.getContext("2d")!.getImageData(0, 0, w, h));
    let mean = 0;
    for (let i = 0; i < g.length; i++) mean += g[i];
    mean /= g.length;
    // "ink" = pixels clearly darker than the page
    const thr = mean - 18;
    const ink: number[] = [];
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) if (g[y * w + x] < thr) ink.push(x, y);
    if (ink.length < 200) return 0;
    const cx = w / 2;
    const cy = h / 2;
    const score = (deg: number) => {
      const r = (deg * Math.PI) / 180;
      const s = Math.sin(r);
      const co = Math.cos(r);
      const rows = new Float64Array(h + 2);
      for (let k = 0; k < ink.length; k += 2) {
        const dx = ink[k] - cx;
        const dy = ink[k + 1] - cy;
        const ry = Math.round(cy + dx * s + dy * co);
        if (ry >= 0 && ry < h) rows[ry]++;
      }
      let m = 0;
      for (let i = 0; i < h; i++) m += rows[i];
      m /= h;
      let v = 0;
      for (let i = 0; i < h; i++) v += (rows[i] - m) * (rows[i] - m);
      return v;
    };
    let best = 0;
    let bestV = score(0);
    const base = bestV;
    for (let d = -7; d <= 7; d++) {
      if (d === 0) continue;
      const v = score(d);
      if (v > bestV) {
        bestV = v;
        best = d;
      }
    }
    // refine around the best whole degree
    for (let d = best - 0.75; d <= best + 0.75; d += 0.25) {
      const v = score(d);
      if (v > bestV) {
        bestV = v;
        best = d;
      }
    }
    // only straighten when the tilt is real and clearly better than flat
    if (Math.abs(best) < 0.75 || bestV < base * 1.08) return 0;
    return best;
  } catch {
    return 0;
  }
}

/** Rotate the canvas by −deg (to undo the measured tilt), keeping the whole image on a white page. */
function rotate(c: Canvas, deg: number): Canvas {
  const r = (-deg * Math.PI) / 180;
  const sin = Math.abs(Math.sin(r));
  const cos = Math.abs(Math.cos(r));
  const w = Math.round(c.width * cos + c.height * sin);
  const h = Math.round(c.width * sin + c.height * cos);
  const out = document.createElement("canvas");
  out.width = w;
  out.height = h;
  const ctx = out.getContext("2d")!;
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, w, h);
  ctx.translate(w / 2, h / 2);
  ctx.rotate(r);
  ctx.drawImage(c, -c.width / 2, -c.height / 2);
  return out;
}

/** Trim wide blank (uniform) margins around a document so the paper fills the frame. */
function autoCrop(c: Canvas): Canvas {
  try {
    const ctx = c.getContext("2d")!;
    const d = ctx.getImageData(0, 0, c.width, c.height);
    const g = luma(d);
    const w = c.width;
    const h = c.height;
    let mean = 0;
    for (let i = 0; i < g.length; i++) mean += g[i];
    mean /= g.length;
    const thr = mean - 18;
    const colInk = new Uint32Array(w);
    const rowInk = new Uint32Array(h);
    for (let y = 0; y < h; y++)
      for (let x = 0; x < w; x++)
        if (g[y * w + x] < thr) {
          colInk[x]++;
          rowInk[y]++;
        }
    const firstRow = (arr: Uint32Array, from: number, to: number, step: number) => {
      const need = 2;
      for (let i = from; i !== to; i += step) if (arr[i] >= need) return i;
      return -1;
    };
    let x0 = firstRow(colInk, 0, w, 1);
    let x1 = firstRow(colInk, w - 1, -1, -1);
    let y0 = firstRow(rowInk, 0, h, 1);
    let y1 = firstRow(rowInk, h - 1, -1, -1);
    if (x0 < 0 || y0 < 0 || x1 <= x0 || y1 <= y0) return c;
    const pad = Math.round(Math.min(w, h) * 0.02);
    x0 = Math.max(0, x0 - pad);
    y0 = Math.max(0, y0 - pad);
    x1 = Math.min(w - 1, x1 + pad);
    y1 = Math.min(h - 1, y1 + pad);
    const nw = x1 - x0 + 1;
    const nh = y1 - y0 + 1;
    // only crop if we actually remove a meaningful margin, and keep most of the picture
    if (nw * nh > w * h * 0.95 || nw * nh < w * h * 0.35) return c;
    const out = document.createElement("canvas");
    out.width = nw;
    out.height = nh;
    out.getContext("2d")!.drawImage(c, x0, y0, nw, nh, 0, 0, nw, nh);
    return out;
  } catch {
    return c;
  }
}

/** Brighten / add contrast so dull and dark photos read clearly (percentile auto-levels). */
function autoLevel(d: ImageData) {
  try {
    const g = luma(d);
    const hist = new Uint32Array(256);
    for (let i = 0; i < g.length; i++) hist[Math.max(0, Math.min(255, g[i] | 0))]++;
    const total = g.length;
    const at = (frac: number) => {
      let acc = 0;
      const want = total * frac;
      for (let i = 0; i < 256; i++) {
        acc += hist[i];
        if (acc >= want) return i;
      }
      return 255;
    };
    const lo = at(0.005);
    const hi = at(0.995);
    if (hi - lo < 16) return; // already full range — leave it
    const span = 255 / (hi - lo);
    const p = d.data;
    const blend = 0.8; // mild, to avoid a harsh / posterised look
    for (let i = 0; i < p.length; i += 4) {
      for (let k = 0; k < 3; k++) {
        const v = p[i + k];
        const stretched = (v - lo) * span;
        p[i + k] = Math.max(0, Math.min(255, v + (stretched - v) * blend));
      }
    }
  } catch {
    /* leave the image as-is */
  }
}

/** Sharpen mild blur with a light unsharp mask (3×3), blended so it never haloes. */
function sharpen(d: ImageData) {
  try {
    const { width: w, height: h, data } = d;
    const src = new Uint8ClampedArray(data);
    const amt = 0.6;
    const idx = (x: number, y: number, k: number) => (y * w + x) * 4 + k;
    for (let y = 1; y < h - 1; y++) {
      for (let x = 1; x < w - 1; x++) {
        for (let k = 0; k < 3; k++) {
          const center = src[idx(x, y, k)];
          const around = src[idx(x - 1, y, k)] + src[idx(x + 1, y, k)] + src[idx(x, y - 1, k)] + src[idx(x, y + 1, k)];
          const lap = center * 4 - around; // high-pass
          data[idx(x, y, k)] = Math.max(0, Math.min(255, center + amt * lap * 0.25));
        }
      }
    }
  } catch {
    /* leave the image as-is */
  }
}

/**
 * Clean up and shrink a photo, returning a JPEG data URL ready to upload.
 * @param doc  true for documents (invoice/bill/slip/receipt/proof) — also straightens tilt and trims margins.
 */
export async function enhanceImage(file: File, opts: { max?: number; doc?: boolean } = {}): Promise<string> {
  const max = opts.max ?? 1600;
  try {
    let c = await load(file, max);
    if (opts.doc) {
      const skew = detectSkew(c);
      if (skew) c = rotate(c, skew);
      c = autoCrop(c);
    }
    const ctx = c.getContext("2d")!;
    const d = ctx.getImageData(0, 0, c.width, c.height);
    autoLevel(d);
    sharpen(d);
    ctx.putImageData(d, 0, 0);
    return c.toDataURL("image/jpeg", 0.85);
  } catch {
    // last-ditch fallback: plain resize with no clean-up
    const c = await load(file, max);
    return c.toDataURL("image/jpeg", 0.82);
  }
}
