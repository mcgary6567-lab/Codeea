/** Banks and mobile wallets in Pakistan (SBP scheduled banks, Islamic, microfinance and digital). */
export type BankInfo = { name: string; short: string; domain: string; color: string };
const B = (name: string, short: string, domain: string, color: string): BankInfo => ({ name, short, domain, color });

export const PK_BANKS: { group: string; banks: BankInfo[] }[] = [
  { group: "Big banks", banks: [
    B("Habib Bank (HBL)", "HBL", "hbl.com", "#00816a"), B("United Bank (UBL)", "UBL", "ubldigital.com", "#0057a8"),
    B("MCB Bank", "MCB", "mcb.com.pk", "#00704a"), B("Allied Bank (ABL)", "ABL", "abl.com", "#003a70"),
    B("National Bank of Pakistan (NBP)", "NBP", "nbp.com.pk", "#006747"), B("Bank Alfalah", "BAF", "bankalfalah.com", "#d71920"),
    B("Meezan Bank", "MBL", "meezanbank.com", "#6e2c6b"), B("Bank of Punjab (BOP)", "BOP", "bop.com.pk", "#0b7a3e"),
    B("Askari Bank", "AKBL", "askaribank.com", "#004b8d"), B("Faysal Bank", "FBL", "faysalbank.com", "#00843d"),
    B("Bank AL Habib", "BAHL", "bankalhabib.com", "#00693c"), B("Habib Metropolitan Bank", "HMB", "habibmetro.com", "#00843f"),
    B("Standard Chartered Pakistan", "SCB", "sc.com", "#0473ea"),
  ] },
  { group: "Other banks", banks: [
    B("BankIslami", "BIPL", "bankislami.com.pk", "#0b6e4f"), B("Dubai Islamic Bank Pakistan", "DIB", "dibpak.com", "#00704a"),
    B("Al Baraka Bank", "ABPL", "albaraka.com.pk", "#8a6d1f"), B("MCB Islamic Bank", "MIB", "mcbislamicbank.com", "#00704a"),
    B("JS Bank", "JSBL", "jsbl.com", "#003c71"), B("Soneri Bank", "SNBL", "soneribank.com", "#c8102e"), B("Silkbank", "SILK", "silkbank.com.pk", "#7b2481"),
    B("Bank Makramah (Summit)", "BML", "bankmakramah.com", "#1d4f91"), B("Samba Bank", "SAMBA", "samba.com.pk", "#00539f"),
    B("Sindh Bank", "SNDB", "sindhbankltd.com", "#00843d"), B("Bank of Khyber", "BOK", "bok.com.pk", "#00703c"),
    B("First Women Bank", "FWBL", "fwbl.com.pk", "#c2185b"), B("Zarai Taraqiati Bank (ZTBL)", "ZTBL", "ztbl.com.pk", "#2e7d32"),
    B("Punjab Provincial Cooperative Bank", "PPCB", "ppcbl.com.pk", "#2e7d32"), B("Citibank", "CITI", "citigroup.com", "#056dae"),
    B("Deutsche Bank", "DB", "db.com", "#0018a8"), B("ICBC Pakistan", "ICBC", "icbc.com.cn", "#c7000b"), B("Bank of China", "BOC", "boc.cn", "#b81c22"),
  ] },
  { group: "Mobile wallets & microfinance", banks: [
    B("Easypaisa (Telenor Microfinance)", "EP", "easypaisa.com.pk", "#3eb54a"), B("JazzCash (Mobilink Microfinance)", "JC", "jazzcash.com.pk", "#e4202b"),
    B("UBL Omni", "OMNI", "ubldigital.com", "#0057a8"), B("HBL Konnect", "HBL", "hbl.com", "#00816a"),
    B("U Microfinance (UPaisa)", "UP", "ubank.com.pk", "#6a1b9a"), B("Khushhali Microfinance Bank", "KMBL", "khushhalibank.com.pk", "#00843d"),
    B("HBL Microfinance Bank", "HBLM", "hblmfb.com", "#00816a"), B("NRSP Microfinance Bank", "NRSP", "nrspbank.com", "#0b7a3e"),
    B("FINCA Microfinance Bank", "FINCA", "finca.pk", "#0072bc"), B("SadaPay", "SP", "sadapay.pk", "#ff6e5a"), B("NayaPay", "NP", "nayapay.com", "#f05a28"),
    B("Raqami Islamic Digital Bank", "RAQ", "raqami.com.pk", "#0b6e4f"), B("Mashreq Pakistan", "MSQ", "mashreqbank.com", "#f15a22"),
  ] },
];
export const ALL_PK_BANKS = PK_BANKS.flatMap((g) => g.banks.map((b) => b.name));
const ALL = PK_BANKS.flatMap((g) => g.banks);

/** Logo, colour and short name for a bank name as saved (also finds "HBL Ferozepur Road" or "Meezan"). */
export function bankInfo(name?: string | null): BankInfo | null {
  if (!name) return null;
  const n = name.toLowerCase();
  return ALL.find((b) => n.startsWith(b.name.toLowerCase()))
    ?? ALL.find((b) => n.includes(b.name.toLowerCase().replace(/\s*\(.*\)/, "")))
    ?? ALL.find((b) => new RegExp(`\\b${b.short.toLowerCase()}\\b`).test(n))
    ?? null;
}
/** The bank's own website icon (fetched by the browser; a coloured badge is shown when offline). */
export const logoUrl = (b: BankInfo) => `https://www.google.com/s2/favicons?domain=${b.domain}&sz=128`;
