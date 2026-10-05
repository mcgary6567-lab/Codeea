/** Banks and mobile wallets in Pakistan (SBP scheduled banks, Islamic, microfinance and digital). */
export const PK_BANKS: { group: string; banks: string[] }[] = [
  { group: "Big banks", banks: [
    "Habib Bank (HBL)", "United Bank (UBL)", "MCB Bank", "Allied Bank (ABL)", "National Bank of Pakistan (NBP)", "Bank Alfalah", "Meezan Bank",
    "Bank of Punjab (BOP)", "Askari Bank", "Faysal Bank", "Bank AL Habib", "Habib Metropolitan Bank", "Standard Chartered Pakistan",
  ] },
  { group: "Other banks", banks: [
    "BankIslami", "Dubai Islamic Bank Pakistan", "Al Baraka Bank", "MCB Islamic Bank", "JS Bank", "Soneri Bank", "Silkbank", "Bank Makramah (Summit)",
    "Samba Bank", "Sindh Bank", "Bank of Khyber", "First Women Bank", "Zarai Taraqiati Bank (ZTBL)", "Punjab Provincial Cooperative Bank",
    "Citibank", "Deutsche Bank", "ICBC Pakistan", "Bank of China",
  ] },
  { group: "Mobile wallets & microfinance", banks: [
    "Easypaisa (Telenor Microfinance)", "JazzCash (Mobilink Microfinance)", "UBL Omni", "HBL Konnect", "U Microfinance (UPaisa)", "Khushhali Microfinance Bank",
    "HBL Microfinance Bank", "NRSP Microfinance Bank", "FINCA Microfinance Bank", "SadaPay", "NayaPay", "Raqami Islamic Digital Bank", "Mashreq Pakistan",
  ] },
];
export const ALL_PK_BANKS = PK_BANKS.flatMap((g) => g.banks);
