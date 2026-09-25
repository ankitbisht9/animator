/**
 * Minimal JSON-RPC 2.0 dispatcher. Transport-agnostic: takes one serialized
 * request (a single line of NDJSON on stdio) and returns one serialized response.
 */

export const RPC_ERRORS = {
  PARSE_ERROR: -32700,
  INVALID_REQUEST: -32600,
  METHOD_NOT_FOUND: -32601,
  INVALID_PARAMS: -32602,
  INTERNAL_ERROR: -32603,
  /** The engine rejected the input (e.g. scene failed validation where that is fatal). */
  ENGINE_ERROR: -32000,
} as const;

export class RpcError extends Error {
  constructor(
    readonly code: number,
    message: string,
    readonly data?: unknown,
  ) {
    super(message);
  }
}

export type Handler = (params: unknown) => unknown;

type RpcId = string | number | null;

interface RpcResponse {
  jsonrpc: "2.0";
  id: RpcId;
  result?: unknown;
  error?: { code: number; message: string; data?: unknown };
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

export function createDispatcher(handlers: Record<string, Handler>) {
  return function dispatch(line: string): string | null {
    let id: RpcId = null;
    let response: RpcResponse;
    try {
      let msg: unknown;
      try {
        msg = JSON.parse(line);
      } catch {
        throw new RpcError(RPC_ERRORS.PARSE_ERROR, "Parse error");
      }
      if (!isRecord(msg) || msg.jsonrpc !== "2.0" || typeof msg.method !== "string") {
        throw new RpcError(RPC_ERRORS.INVALID_REQUEST, "Invalid request");
      }
      const rawId = msg.id;
      if (rawId === undefined) {
        // Notification: run it, send nothing back.
        handlers[msg.method]?.(msg.params ?? {});
        return null;
      }
      if (typeof rawId === "string" || typeof rawId === "number" || rawId === null) id = rawId;
      const handler = Object.hasOwn(handlers, msg.method) ? handlers[msg.method] : undefined;
      if (!handler) throw new RpcError(RPC_ERRORS.METHOD_NOT_FOUND, `Method not found: ${msg.method}`);
      response = { jsonrpc: "2.0", id, result: handler(msg.params ?? {}) };
    } catch (err) {
      if (err instanceof RpcError) {
        response = { jsonrpc: "2.0", id, error: { code: err.code, message: err.message, data: err.data } };
      } else {
        const message = err instanceof Error ? err.message : String(err);
        response = { jsonrpc: "2.0", id, error: { code: RPC_ERRORS.INTERNAL_ERROR, message } };
      }
    }
    return JSON.stringify(response);
  };
}
