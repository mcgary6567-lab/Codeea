import pino from "pino";
import { config } from "./config.js";

export const log = pino({ level: config.NODE_ENV === "test" ? "silent" : "info" });
