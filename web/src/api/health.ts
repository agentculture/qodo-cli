import { ROUTES, getJson } from "./client";
import type { Health } from "./types";

/** `GET /health`: public, so it answers even where no credential reached the API. */
export const getHealth = (signal?: AbortSignal) => getJson<Health>(ROUTES.health.path, signal);
