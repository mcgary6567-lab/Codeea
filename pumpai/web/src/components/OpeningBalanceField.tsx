import { Field } from "./ui";

/**
 * Opening balance with its direction. "Due" = the party owes us from before (goes to their baqaya).
 * "Advance" = cash they paid us up front: it sits as a minus balance and every khata/wholesale sale is cut from it
 * until it is used up, after which the account runs on credit as usual.
 */
export function OpeningBalanceField({ amount, kind, onChange, label = "Opening balance (Rs)" }: { amount: string | number; kind: "due" | "advance"; onChange: (amount: string, kind: "due" | "advance") => void; label?: string }) {
  return (
    <div className="sm:col-span-2">
      <Field label={label}>
        <div className="flex flex-col gap-2 sm:flex-row">
          <input className="input sm:w-44" type="number" min={0} step="0.01" value={amount} onChange={(e) => onChange(e.target.value, kind)} />
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
    </div>
  );
}
