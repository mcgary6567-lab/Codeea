/** Forecourt operations API, split by domain into sub-routers mounted on one shared router.
 * The route surface and mount path are unchanged — index.ts still does `api.use(operations)`. */
import { Router } from "express";
import { stationRoutes } from "./operationsStations.js";
import { priceRoutes } from "./operationsPrices.js";
import { saleRoutes } from "./operationsSales.js";
import { shiftRoutes } from "./operationsShifts.js";
import { stockRoutes } from "./operationsStock.js";

export const operations = Router();
operations.use(stationRoutes);
operations.use(priceRoutes);
operations.use(saleRoutes);
operations.use(shiftRoutes);
operations.use(stockRoutes);

// Price helpers other modules call directly (automation jobs, the WhatsApp approval decider).
export { applyPrices, applyDuePrices, decidePriceRequest } from "./operationsPrices.js";
