// Typed client for the Golf Tracer API. `schema.ts` is generated from the FastAPI OpenAPI
// schema (`pnpm gen:api-client`); never edit it by hand. No DOM or framework imports: the
// React Native app uses this package too.
import createClient, { type Client } from "openapi-fetch";

import type { components, paths } from "./schema";

export type { components, paths } from "./schema";

type S = components["schemas"];
export type Clip = S["ClipOut"];
export type ClipDetail = S["ClipDetail"];
export type ClipDetection = S["ClipDetection"];
export type ClipStatus = S["ClipOut"]["status"];
export type ClipCreated = S["ClipCreated"];
export type ClipPage = S["ClipPage"];
export type UploadTarget = S["UploadTarget"];
export type TracerRecord = S["TracerOut"];
export type TracerDoc = S["TracerDoc"];
export type TracerStyleDoc = S["TracerStyle"];
export type ManualTracerRequest = S["ManualTracerRequest"];
export type RenderLink = S["RenderLink"];
export type BulkAction = S["BulkRequest"]["action"];
export type BulkResponse = S["BulkResponse"];
export type Profile = S["ProfileOut"];
export type ProfileUpdate = S["ProfileUpdate"];
export type ErrorBody = S["ErrorBody"];

export const CLIP_STATUSES: readonly ClipStatus[] = [
  "uploading",
  "queued",
  "processing",
  "review",
  "needs_input",
  "approved",
  "rendering",
  "failed",
] as const;

export type ApiClient = Client<paths>;

export interface ApiClientOptions {
  baseUrl: string;
  /** Returns the current Supabase access token (refreshing it if needed), or null when signed out. */
  getAccessToken: () => Promise<string | null>;
  fetch?: typeof globalThis.fetch;
}

export function createApiClient(opts: ApiClientOptions): ApiClient {
  const client = createClient<paths>({ baseUrl: opts.baseUrl.replace(/\/$/, ""), fetch: opts.fetch });
  client.use({
    async onRequest({ request }) {
      const token = await opts.getAccessToken();
      if (token) request.headers.set("Authorization", `Bearer ${token}`);
      return request;
    },
  });
  return client;
}

/** An API error with the server's `{error: {code, message}}` envelope. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown;
  readonly retryAfter: number | null;

  constructor(status: number, code: string, message: string, details?: unknown, retryAfter: number | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
    this.retryAfter = retryAfter;
  }
}

interface FetchResult<T> {
  data?: T;
  error?: unknown;
  response: Response;
}

/** Await an openapi-fetch call and return its data, or throw an ApiError. */
export async function unwrap<T>(call: Promise<FetchResult<T>>): Promise<T> {
  let result: FetchResult<T>;
  try {
    result = await call;
  } catch (e) {
    throw new ApiError(0, "network_error", "Couldn't reach the server. Check your connection.", e);
  }
  const { data, error, response } = result;
  if (response.ok) return data as T;
  const body = (error ?? {}) as { error?: { code?: string; message?: string; details?: unknown } };
  const retry = response.headers.get("Retry-After");
  throw new ApiError(
    response.status,
    body.error?.code ?? `http_${response.status}`,
    body.error?.message ?? response.statusText ?? "Request failed",
    body.error?.details,
    retry ? Number(retry) : null,
  );
}

/** Statuses where the clip has finished processing and has (or needs) a tracer. */
export function isReviewable(status: ClipStatus): boolean {
  return status === "review" || status === "needs_input";
}

export function isBusy(status: ClipStatus): boolean {
  return status === "uploading" || status === "queued" || status === "processing" || status === "rendering";
}
export * from "./rules";
