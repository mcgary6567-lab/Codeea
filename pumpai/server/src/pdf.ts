/**
 * A very small PDF writer (A4, Helvetica) for one-page documents like the salary slip.
 * Text is Latin only (WinAnsi); anything else is replaced so the file always opens.
 */
export class Pdf {
  private ops: string[] = [];
  static readonly W = 595.28;
  static readonly H = 841.89;

  private static clean(s: string) {
    return s.replace(/[—–]/g, "-").replace(/[‘’]/g, "'").replace(/[“”]/g, '"').replace(/×/g, "x").replace(/•/g, "-")
      .replace(/[^\x20-\x7e\xa0-\xff]/g, "?").replace(/\\/g, "\\\\").replace(/\(/g, "\\(").replace(/\)/g, "\\)");
  }
  /** Approximate Helvetica width (good enough to right-align numbers). */
  static width(s: string, size: number, bold = false) {
    let w = 0;
    for (const c of s) w += /[0-9]/.test(c) ? 0.556 : /[ .,:;'|!il]/.test(c) ? 0.278 : /[A-Z]/.test(c) ? 0.68 : /[mwMW]/.test(c) ? 0.85 : 0.53;
    return w * size * (bold ? 1.05 : 1);
  }
  /** y is measured from the top of the page. */
  text(x: number, y: number, s: string, size = 10, opts: { bold?: boolean; align?: "left" | "right" | "center"; gray?: number } = {}) {
    const w = Pdf.width(s, size, opts.bold);
    const left = opts.align === "right" ? x - w : opts.align === "center" ? x - w / 2 : x;
    this.ops.push(`BT ${opts.gray != null ? `${opts.gray} g ` : "0 g "}/${opts.bold ? "F2" : "F1"} ${size} Tf ${left.toFixed(2)} ${(Pdf.H - y).toFixed(2)} Td (${Pdf.clean(s)}) Tj ET`);
    return this;
  }
  line(x1: number, y1: number, x2: number, y2: number, width = 0.7, gray = 0) {
    this.ops.push(`${gray} G ${width} w ${x1.toFixed(2)} ${(Pdf.H - y1).toFixed(2)} m ${x2.toFixed(2)} ${(Pdf.H - y2).toFixed(2)} l S`);
    return this;
  }
  rect(x: number, y: number, w: number, h: number, fillGray = 0.93) {
    this.ops.push(`${fillGray} g ${x.toFixed(2)} ${(Pdf.H - y - h).toFixed(2)} ${w.toFixed(2)} ${h.toFixed(2)} re f`);
    return this;
  }
  build(title = "Document"): Buffer {
    const content = this.ops.join("\n");
    const objs = [
      "<< /Type /Catalog /Pages 2 0 R >>",
      "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
      `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${Pdf.W} ${Pdf.H}] /Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> /Contents 4 0 R >>`,
      `<< /Length ${Buffer.byteLength(content, "latin1")} >>\nstream\n${content}\nendstream`,
      "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
      "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
      `<< /Title (${Pdf.clean(title)}) /Producer (PumpAI) >>`,
    ];
    let out = "%PDF-1.4\n";
    const offsets: number[] = [];
    objs.forEach((o, i) => { offsets.push(Buffer.byteLength(out, "latin1")); out += `${i + 1} 0 obj\n${o}\nendobj\n`; });
    const xref = Buffer.byteLength(out, "latin1");
    out += `xref\n0 ${objs.length + 1}\n0000000000 65535 f \n${offsets.map((o) => `${String(o).padStart(10, "0")} 00000 n \n`).join("")}`;
    out += `trailer\n<< /Size ${objs.length + 1} /Root 1 0 R /Info ${objs.length} 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
    return Buffer.from(out, "latin1");
  }
}
