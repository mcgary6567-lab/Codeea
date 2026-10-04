import { useApi } from "../lib/api";
import { Loading, PageHeader } from "../components/ui";
import { pkr } from "../lib/format";
import { LedgerList } from "./Staff";

/** A staff member's own account: advances, shortages and salary. */
export default function MyAccount() {
  const { data } = useApi<any>("/me/account");
  if (!data) return <Loading />;
  return (
    <div className="space-y-4">
      <PageHeader title="My account" subtitle="Advances, cash shortages and salary" />
      <div className="flex flex-wrap gap-3">
        <div className="rounded-2xl bg-amber-50 px-5 py-4 ring-1 ring-amber-200"><div className="text-sm text-amber-800">To adjust from salary · <span lang="ur" className="font-urdu">تنخواہ سے کٹے گا</span></div><div className="text-3xl font-bold tabular-nums">{pkr(data.balance)}</div></div>
        {data.user.salary ? <div className="rounded-2xl bg-slate-50 px-5 py-4 ring-1 ring-slate-200"><div className="text-sm text-slate-600">Monthly salary</div><div className="text-3xl font-bold tabular-nums">{pkr(data.user.salary)}</div></div> : null}
      </div>
      <div className="card p-3"><LedgerList lines={data.lines} /></div>
    </div>
  );
}
