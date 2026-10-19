import { PageHeader } from "../components/ui";
import { Checklist } from "./Compliance";

/** Salesman's daily checks with big buttons. */
export default function ChecklistPage() {
  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <PageHeader title="Daily checks · روزانہ چیک" subtitle="Tap OK when done. Take a photo where asked." />
      <Checklist />
    </div>
  );
}
