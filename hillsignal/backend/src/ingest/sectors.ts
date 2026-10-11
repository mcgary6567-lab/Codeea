// Starter ticker → GICS sector map for the most-traded names, so AI search can
// answer "tech stocks" style questions. Replace with a market-data feed for full coverage.
const SECTORS: Record<string, string> = {};

const groups: Record<string, string[]> = {
  "Information Technology": ["AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CRM", "AMD", "ADBE", "CSCO", "ACN", "INTC", "IBM", "QCOM", "TXN", "NOW", "INTU", "AMAT", "MU", "LRCX", "KLAC", "PANW", "SNPS", "CDNS", "ANET", "CRWD", "PLTR", "SMCI", "DELL", "HPQ", "ASML", "TSM"],
  "Communication Services": ["GOOGL", "GOOG", "META", "NFLX", "DIS", "CMCSA", "T", "VZ", "TMUS", "CHTR", "EA", "TTWO", "SPOT", "RBLX"],
  "Consumer Discretionary": ["AMZN", "TSLA", "HD", "MCD", "NKE", "LOW", "SBUX", "BKNG", "TJX", "CMG", "ABNB", "GM", "F", "UBER", "LULU", "MAR", "RCL"],
  "Consumer Staples": ["WMT", "PG", "KO", "PEP", "COST", "PM", "MO", "MDLZ", "CL", "KMB", "TGT", "KR", "GIS"],
  Financials: ["JPM", "BAC", "WFC", "GS", "MS", "C", "BLK", "SCHW", "AXP", "V", "MA", "PYPL", "BRK.B", "SPGI", "CB", "PGR", "COF", "USB", "COIN", "HOOD"],
  "Health Care": ["UNH", "JNJ", "LLY", "ABBV", "MRK", "PFE", "TMO", "ABT", "DHR", "BMY", "AMGN", "GILD", "CVS", "ISRG", "MDT", "VRTX", "REGN", "MRNA", "NVO", "HUM", "CI", "ELV"],
  Industrials: ["BA", "LMT", "RTX", "NOC", "GD", "GE", "CAT", "DE", "HON", "UPS", "FDX", "UNP", "MMM", "LHX", "WM", "ETN", "EMR"],
  Energy: ["XOM", "CVX", "COP", "SLB", "EOG", "OXY", "PSX", "MPC", "VLO", "KMI", "WMB", "HAL"],
  Utilities: ["NEE", "DUK", "SO", "D", "AEP", "EXC", "SRE", "XEL"],
  "Real Estate": ["AMT", "PLD", "CCI", "EQIX", "SPG", "O", "PSA", "WELL"],
  Materials: ["LIN", "SHW", "APD", "FCX", "NEM", "DOW", "DD", "NUE"],
};

for (const [sector, tickers] of Object.entries(groups)) {
  for (const t of tickers) SECTORS[t] = sector;
}

export const KNOWN_SECTORS = Object.keys(groups);

export function sectorForTicker(ticker: string): string | null {
  return SECTORS[ticker.toUpperCase()] ?? null;
}
