import { Field } from "./ui";
import { AccountPicker } from "./BankParts";

export const ADVANCE_METHODS = ["Cash", "Bank transfer", "Raast", "JazzCash", "Easypaisa", "Card"];
/** "" = the money came in before go-live (old register) — nothing moves in cash/bank, only the khata. */
export type AdvanceReceipt = { method: string; account_id: number | null };

/**
 * Opening balance with its direction. "Due" = the party owes us from before (goes to their baqaya).
 * "Advance" = cash they paid us up front: it sits as a minus balance and every khata/wholesale sale is cut from it
 * until it is used up. An advance received TODAY also names how it came (cash / bank / wallet) so the cash book,
 * the bank account and the ledger get the money; an old (go-live) advance only sets the khata.
 */
export function OpeningBalanceField({ amount, kind, onChange, label = "Opening balance (Rs)", receipt, onReceipt }: {
  amount: string | number; kind: "due" | "advance"; onChange: (amount: string, kind: "due" | "advance") => void; label?: string;
  receipt?: AdvanceReceipt; onReceipt?: (r: AdvanceReceipt) => void;
}) {
  const live = kind === "advance" && Number(amount) > 0 && onReceipt;
  return (
    <div className="sm:col-span-2 space-y-2">
      <Field label={label}>
        <div className="flex flex-col gap-2 sm:flex-row">
          <input className="input sm:w-44" type="number" inputMode="decimal" min={0} step="0.01" value={amount} onChange={(e) => onChange(e.target.value, kind)} />
          <div className="grid flex-1 grid-cols-2 gap-1 rounded-lg border border-slate-300 p-1">
            {([["due", "Unhein dena hai (due)", "ان کے ذمے"], ["advance", "Advance diya hua (credit)", "ایڈوانس جمع"]] as const).map(([k, l, u]) => (
              <button key={k} type="button" onClick={() => onChange(String(amount), k)}
                className={`rounded-md px-2 py-1.5 text-left text-xs font-medium leading-tight ${kind === k ? (k === "advance" ? "bg-emerald-600 text-white" : "bg-amber-500 text-white") : "text-slate-600 hover:bg-slate-50"}`}>
                {l}<br /><span lang="ur" dir="rtl" className="font-urdu text-[11px] opacity-90">{u}</span>
              </button>
            ))}
          </div>
        </div>
      </Field>
      <p className="-mt-1 text-xs text-slate-500">
        {kind === "advance"
          ? "Advance: customer ne pehle cash de diya. Har khata sale is se katti jayegi; khatam hone par aage ka hisaab due banega."
          : "Due: purana baqaya jo customer ne humein dena hai — seedha us ke khate mein chadhta hai."}
      </p>
      {live && receipt && (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50/60 p-3">
          <div className="label">Advance kaise mila? · <span lang="ur" dir="rtl" className="font-urdu">رقم کیسے ملی</span></div>
          <div className="flex flex-wrap gap-1.5">
            {[...ADVANCE_METHODS, ""].map((m) => (
              <button key={m || "old"} type="button" onClick={() => onReceipt({ method: m, account_id: m === "Cash" || m === "" ? null : receipt.account_id })}
                className={`rounded-full px-3 py-1.5 text-xs font-medium ${receipt.method === m ? "bg-emerald-600 text-white" : "bg-white text-slate-700 ring-1 ring-slate-300"}`}>
                {m || "Purana hisaab (go-live) — paisa pehle aa chuka"}
              </button>
            ))}
          </div>
          {receipt.method && receipt.method !== "Cash" && <div className="mt-2"><AccountPicker method={receipt.method} value={receipt.account_id} onChange={(id) => onReceipt({ ...receipt, account_id: id })} required /></div>}
          <p className="mt-2 text-xs text-slate-600">
            {receipt.method === "" ? "Sirf khate mein advance likha jayega (opening balances ke against). Cash/bank nahi hilta."
              : receipt.method === "Cash" ? "Aaj ka cash: cash book aur cashier handover mein receipt banegi; ledger: Dr Cash / Cr Khata."
              : `${receipt.method}: chune hue bank/wallet account mein jama hoga; ledger: Dr Bank / Cr Khata.`}
          </p>
        </div>
      )}
    </div>
  );
}
