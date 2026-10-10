/** Amount in words, Pakistani style (lakh / crore) — printed on the voucher. */
export function toWords(n: number): string {
  const a = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"];
  const b = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"];
  const two = (x: number) => (x < 20 ? a[x] : `${b[Math.floor(x / 10)]}${x % 10 ? " " + a[x % 10] : ""}`);
  const three = (x: number) => `${x >= 100 ? a[Math.floor(x / 100)] + " Hundred" + (x % 100 ? " " : "") : ""}${x % 100 ? two(x % 100) : ""}`;
  let x = Math.round(n);
  if (!x) return "Zero rupees";
  const parts: string[] = [];
  for (const [v, w] of [[10_000_000, "Crore"], [100_000, "Lakh"], [1000, "Thousand"]] as const) {
    if (x >= v) { parts.push(`${v === 10_000_000 && x >= 1_000_000_000 ? toWords(Math.floor(x / v)).replace(" rupees only", "") : three(Math.floor(x / v))} ${w}`); x %= v; }
  }
  if (x) parts.push(three(x));
  return `${parts.join(" ")} rupees only`;
}
