import { getAccessToken } from "../auth";

export const API_BASE: string = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";
export const MOCK_MODE: boolean = import.meta.env.VITE_MOCK === "1";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;
  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

type Query = Record<string, string | number | boolean | undefined | null>;

export interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  query?: Query;
  body?: unknown;
  signal?: AbortSignal;
}

function buildQuery(query?: Query): string {
  if (!query) return "";
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null || v === "") continue;
    params.set(k, String(v));
  }
  const s = params.toString();
  return s ? `?${s}` : "";
}

export const OFFLINE_MESSAGE = "Non riesco a contattare il server. Controlla che `make dev-real` sia acceso e riprova.";

function detailText(detail: unknown): string | null {
  if (detail && typeof detail === "object" && "detail" in detail) {
    const d = (detail as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) {
      return d
        .map((e) => (e && typeof e === "object" && "msg" in e ? String((e as { msg: unknown }).msg) : String(e)))
        .join("; ");
    }
  }
  return typeof detail === "string" && detail.length < 300 ? detail : null;
}

/** Plain-Italian message for an HTTP error; the raw detail (often English) stays as context. */
export function messageFrom(status: number, detail: unknown): string {
  const raw = detailText(detail);
  const extra = raw ? ` (dettaglio: ${raw})` : "";
  switch (true) {
    case status === 401:
      return "La sessione è scaduta o manca l'accesso. Accedi di nuovo e riprova.";
    case status === 403:
      return "Questo account non è autorizzato a usare Radar.";
    case status === 404:
      return `Non trovo questo elemento: forse è stato rimosso. Ricarica la pagina${extra}.`;
    case status === 409:
      return `Non si può modificare in questo stato${extra}.`;
    case status === 422:
      return raw ? `Controlla i dati inseriti: ${raw}` : "Controlla i dati inseriti e riprova.";
    case status === 501:
      return `Questa funzione non è disponibile in questa installazione${extra}.`;
    case status === 502 || status === 504:
      return `Il servizio esterno (modello linguistico o sito dell'annuncio) non ha risposto. Riprova tra qualche minuto${extra}.`;
    case status >= 500:
      return `Il server ha avuto un problema (errore ${status}). Riprova tra poco; se continua, guarda il terminale dove gira il server.`;
    default:
      return `La richiesta non è andata a buon fine (errore ${status})${extra}.`;
  }
}

/** Fetch wrapper for the Radar API. Paths start with `/api/...`. */
export async function apiRequest<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const method = opts.method ?? "GET";

  if (MOCK_MODE) {
    const { handleMock } = await import("../mocks/handler");
    const res = await handleMock(method, path, opts.query ?? {}, opts.body);
    if (res.status >= 400) throw new ApiError(res.status, messageFrom(res.status, res.body), res.body);
    return res.body as T;
  }

  const headers: Record<string, string> = { Accept: "application/json" };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  const token = await getAccessToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}${buildQuery(opts.query)}`, {
      method,
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: opts.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(0, OFFLINE_MESSAGE);
  }

  if (response.status === 204) return undefined as T;
  const text = await response.text();
  let data: unknown = undefined;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!response.ok) throw new ApiError(response.status, messageFrom(response.status, data), data);
  return data as T;
}

export const api = {
  get: <T>(path: string, query?: Query, signal?: AbortSignal) => apiRequest<T>(path, { query, signal }),
  post: <T>(path: string, body?: unknown) => apiRequest<T>(path, { method: "POST", body: body ?? {} }),
  patch: <T>(path: string, body: unknown) => apiRequest<T>(path, { method: "PATCH", body }),
  put: <T>(path: string, body: unknown) => apiRequest<T>(path, { method: "PUT", body }),
  del: <T = void>(path: string) => apiRequest<T>(path, { method: "DELETE" }),
};
