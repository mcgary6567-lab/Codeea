import { File, Paths } from 'expo-file-system';
import * as Sharing from 'expo-sharing';
import { api } from '@/api/endpoints';
import type { TradeQuery } from '@/api/types';
import { toIsoDay } from './format';

/**
 * Premium: GET /trades/export.csv with the current filters, saved to the cache
 * directory and handed to the system share sheet. Errors (402 etc.) propagate as ApiError.
 */
export async function exportTradesCsv(filters: TradeQuery): Promise<void> {
  const csv = await api.tradesCsv(filters);
  const file = new File(Paths.cache, `hillsignal-trades-${toIsoDay(new Date())}.csv`);
  if (file.exists) file.delete();
  file.create();
  file.write(csv);
  if (!(await Sharing.isAvailableAsync())) throw new Error('Sharing is not available on this device.');
  await Sharing.shareAsync(file.uri, { mimeType: 'text/csv', dialogTitle: 'Export trades', UTI: 'public.comma-separated-values-text' });
}
