/**
 * TV rate board: a page for a TV / LED screen at the pump showing today's prices in big English + Urdu
 * letters, with offers. It checks for changes every 20 seconds, so a price change shows up by itself.
 */
import { Router } from "express";
import { logoTag } from "./setup.js";
import jwt from "jsonwebtoken";
import { z } from "zod";
import { config, PRODUCTS } from "../config.js";
import { get, getSetting, setSetting } from "../db.js";
import { h, parse, tid, requirePerm } from "../auth.js";
import { currentPrices, AppError } from "../services.js";

export const board = Router();

const EN: Record<string, string> = { PMG: "Petrol", HOBC: "Hi-Octane", HSD: "Diesel" };
const URDU: Record<string, string> = { PMG: "پیٹرول", HOBC: "ہائی آکٹین", HSD: "ڈیزل" };
const COLOR: Record<string, string> = { PMG: "#2a78d6", HOBC: "#eb6834", HSD: "#1baf7a" };

const boardUrl = (t: number, stationId: number | null) =>
  `${config.publicUrl}/board/${jwt.sign({ board: t, st: stationId }, config.jwtSecret, { expiresIn: "3650d" })}`;

function verify(token: string): { board: number; st: number | null } | null {
  try {
    const p = jwt.verify(token, config.jwtSecret) as { board?: number; st?: number | null };
    return typeof p.board === "number" ? { board: p.board, st: p.st ?? null } : null;
  } catch { return null; }
}

export function boardData(token: string) {
  const p = verify(token);
  if (!p) return null;
  const prices = currentPrices(p.board);
  const station = p.st ? get("SELECT name FROM stations WHERE id=? AND tenant_id=?", p.st, p.board) : null;
  const products = p.st ? new Set((get("SELECT GROUP_CONCAT(DISTINCT product) v FROM tanks WHERE station_id=?", p.st)?.v ?? "").split(",")) : null;
  return {
    name: station?.name ?? get("SELECT name FROM tenants WHERE id=?", p.board)?.name ?? "PumpAI",
    prices: Object.entries(prices).filter(([k]) => !products || products.has(k)).map(([k, v]) => ({ product: k, en: EN[k] ?? PRODUCTS[k] ?? k, ur: URDU[k] ?? "", color: COLOR[k] ?? "#334155", price: v.price, since: v.effective_from })),
    offers: getSetting(p.board, "board_offers").split("\n").map((s) => s.trim()).filter(Boolean),
    updated_at: new Date().toISOString(),
  };
}

board.get("/board-link", requirePerm("prices.view"), h((req) => {
  const station = req.query.station_id ? Number(req.query.station_id) : null;
  if (station && !get("SELECT id FROM stations WHERE id=? AND tenant_id=?", station, tid(req))) throw new AppError(404, "Station not found");
  return { url: boardUrl(tid(req), station), offers: getSetting(tid(req), "board_offers") };
}));
board.put("/board/settings", requirePerm("prices.update"), h((req) => {
  const b = parse(z.object({ offers: z.string().max(600) }), req.body);
  setSetting(tid(req), "board_offers", b.offers);
  return { offers: b.offers };
}));

const esc = (s: string) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

export function renderBoard(token: string) {
  const d = boardData(token);
  if (!d) return null;
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${esc(d.name)} — Rates</title>
<link href="https://fonts.googleapis.com/css2?family=Noto+Nastaliq+Urdu:wght@600&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box}body{margin:0;background:#0b1220;color:#fff;font-family:system-ui,Segoe UI,Arial,sans-serif;min-height:100vh;display:flex;flex-direction:column}
header{display:flex;justify-content:space-between;align-items:center;padding:2vh 4vw;font-size:3.2vh;color:#cbd5e1}
header b{color:#fff;font-size:4.4vh}
main{flex:1;display:grid;gap:2.4vh;padding:0 4vw;align-content:center}
.row{display:grid;grid-template-columns:1.1fr 1fr 1.2fr;align-items:center;border-radius:2.4vh;padding:2.2vh 3vw;background:#111b2e;border-left:2vh solid var(--c)}
.en{font-size:6vh;font-weight:800}.ur{font-family:'Noto Nastaliq Urdu',serif;font-size:6vh;direction:rtl;text-align:center;line-height:1.6}
.price{text-align:right;font-size:11vh;font-weight:800;font-variant-numeric:tabular-nums;letter-spacing:-.02em}.price small{font-size:3.4vh;color:#94a3b8;font-weight:600}
.flash{animation:f 1.2s ease-in-out 6}@keyframes f{50%{background:#334155}}
footer{padding:2vh 4vw 3vh}.offer{font-size:4vh;background:#facc15;color:#111;border-radius:1.6vh;padding:1.4vh 2vw;margin-top:1.2vh;font-weight:700}
@media (max-width:700px){.row{grid-template-columns:1fr 1fr}.ur{display:none}}
</style></head><body>
<header><span style="display:flex;align-items:center;gap:2vw">${logoTag(verify(token)!.board, "height:7vh;background:#fff;border-radius:1vh;padding:.4vh")}<b id="name">${esc(d.name)}</b></span><span id="clock"></span></header>
<main id="rows"></main>
<footer id="offers"></footer>
<script>
const token=${JSON.stringify(token)};let last=null;
function fmt(n){return n.toLocaleString('en-PK',{minimumFractionDigits:2,maximumFractionDigits:2})}
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c])}
function draw(d){const key=JSON.stringify(d.prices.map(p=>[p.product,p.price]));
document.getElementById('rows').innerHTML=d.prices.map(p=>'<div class="row'+(last&&last!==key?' flash':'')+'" style="--c:'+p.color+'"><div class="en">'+esc(p.en)+'</div><div class="ur" lang="ur">'+esc(p.ur)+'</div><div class="price"><small>Rs / L </small>'+fmt(p.price)+'</div></div>').join('');
document.getElementById('offers').innerHTML=d.offers.map(o=>'<div class="offer">🎁 '+esc(o)+'</div>').join('');last=key}
async function poll(){try{const r=await fetch('/board/'+token+'/data',{cache:'no-store'});if(r.ok)draw(await r.json())}catch(e){}}
function tick(){document.getElementById('clock').textContent=new Date().toLocaleString('en-PK',{weekday:'short',day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'})}
draw(${JSON.stringify(d)});tick();setInterval(poll,20000);setInterval(tick,15000);
</script></body></html>`;
}
