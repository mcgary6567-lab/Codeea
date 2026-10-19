/** The pump's social pages (Settings → Business profile), printed at the foot of every paper. Same list as the server's brandPrint.ts. */
const link = (v: string, base: string) => {
  const s = v.trim();
  return /^https?:\/\//i.test(s) ? s : /^[\w.-]+\.\w{2,}\//.test(s) ? `https://${s}` : base + s.replace(/^@/, "");
};
export const SOCIALS: { key: string; label: string; ur: string; hint: string; url: (v: string) => string; svg: string }[] = [
  { key: "facebook", label: "Facebook", ur: "فیس بک", hint: "facebook.com/YourPump", url: (v) => link(v, "https://facebook.com/"), svg: '<path d="M14 8h3V4h-3c-2.8 0-5 2.2-5 5v2H7v4h2v9h4v-9h3l1-4h-4V9c0-.6.4-1 1-1z"/>' },
  { key: "instagram", label: "Instagram", ur: "انسٹاگرام", hint: "@yourpump", url: (v) => link(v, "https://instagram.com/"), svg: '<path d="M7 2h10a5 5 0 0 1 5 5v10a5 5 0 0 1-5 5H7a5 5 0 0 1-5-5V7a5 5 0 0 1 5-5zm0 2a3 3 0 0 0-3 3v10a3 3 0 0 0 3 3h10a3 3 0 0 0 3-3V7a3 3 0 0 0-3-3H7zm5 3.5a4.5 4.5 0 1 1 0 9 4.5 4.5 0 0 1 0-9zm0 2a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5zM17.5 5.5a1 1 0 1 1 0 2 1 1 0 0 1 0-2z"/>' },
  { key: "whatsapp", label: "WhatsApp", ur: "واٹس ایپ", hint: "0300 1234567", url: (v) => (/^https?:/i.test(v) ? v : `https://wa.me/${v.replace(/\D/g, "").replace(/^0/, "92")}`), svg: '<path d="M12 2a10 10 0 0 0-8.6 15.1L2 22l5-1.3A10 10 0 1 0 12 2zm0 2a8 8 0 1 1-4.1 14.9l-.3-.2-3 .8.8-2.9-.2-.3A8 8 0 0 1 12 4zm-3.3 3.5c-.2 0-.6.1-.9.4-.3.3-1.1 1.1-1.1 2.7s1.1 3.1 1.3 3.3c.2.2 2.2 3.4 5.4 4.6 2.6 1 3.2.8 3.7.8.6-.1 1.8-.8 2.1-1.5.3-.7.3-1.4.2-1.5l-.6-.4-2.1-1c-.3-.1-.5-.2-.7.1l-1 1.2c-.2.2-.4.2-.7.1-.3-.2-1.3-.5-2.5-1.5-.9-.8-1.5-1.8-1.7-2.1-.2-.3 0-.5.1-.6l.5-.6.3-.5v-.5l-1-2.3c-.2-.5-.5-.5-.7-.5h-.6z"/>' },
  { key: "tiktok", label: "TikTok", ur: "ٹک ٹاک", hint: "@yourpump", url: (v) => link(v, "https://tiktok.com/@"), svg: '<path d="M16.5 2c.3 2.5 1.8 4.1 4.5 4.3v3.3c-1.6.1-3-.4-4.5-1.3v6.2c0 7.8-8.5 10.3-11.9 4.7-2.2-3.6-.9-9.9 6.2-10.2v3.5c-.5.1-1.1.2-1.6.4-1.6.5-2.5 1.5-2.3 3.3.5 3.4 6.7 4.4 6.2-2.3V2h3.4z"/>' },
  { key: "youtube", label: "YouTube", ur: "یوٹیوب", hint: "@yourpump", url: (v) => link(v, "https://youtube.com/@"), svg: '<path d="M23 7.2a3 3 0 0 0-2.1-2.1C19 4.6 12 4.6 12 4.6s-7 0-8.9.5A3 3 0 0 0 1 7.2 31 31 0 0 0 .5 12a31 31 0 0 0 .5 4.8 3 3 0 0 0 2.1 2.1c1.9.5 8.9.5 8.9.5s7 0 8.9-.5a3 3 0 0 0 2.1-2.1 31 31 0 0 0 .5-4.8 31 31 0 0 0-.5-4.8zM9.8 15.1V8.9l5.8 3.1-5.8 3.1z"/>' },
  { key: "twitter", label: "X (Twitter)", ur: "ایکس", hint: "@yourpump", url: (v) => link(v, "https://x.com/"), svg: '<path d="M17.8 3h3.3l-7.2 8.2L22.3 21h-6.6l-5.2-6.8L4.6 21H1.3l7.7-8.8L1 3h6.8l4.7 6.2L17.8 3zm-1.2 16.1h1.8L7.5 4.8H5.6l11 14.3z"/>' },
];
export const SOCIAL_KEYS = SOCIALS.map((s) => s.key);
/** "https://facebook.com/AlMadina" → "AlMadina": the short handle printed next to the icon. */
export const handle = (v: string) => v.trim().replace(/^https?:\/\/(www\.)?/i, "").replace(/^(facebook|fb|instagram|tiktok|youtube|x|twitter)\.com\/@?/i, "").replace(/^wa\.me\//i, "+").replace(/\/$/, "");
