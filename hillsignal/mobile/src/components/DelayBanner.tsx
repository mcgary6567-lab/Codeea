import { openPaywall } from '@/lib/navigation';
import { Notice } from './Notice';

/** "Free plan: trades delayed N days" banner, shown when meta.delayDays > 0. */
export function DelayBanner({ delayDays }: { delayDays: number | undefined }) {
  if (!delayDays || delayDays <= 0) return null;
  return (
    <Notice
      kind="warning"
      message={`Free plan: trades published in the last ${delayDays} day${delayDays === 1 ? '' : 's'} are hidden.`}
      actionLabel="Upgrade"
      onAction={() => openPaywall('pro')}
    />
  );
}
