import { describe, expect, it } from "vitest";
import { handlers } from "../src/handlers";
import { createDispatcher, RPC_ERRORS } from "../src/rpc";

const dispatch = createDispatcher(handlers);
const call = (obj: unknown) => JSON.parse(dispatch(JSON.stringify(obj)) ?? "null");

describe("json-rpc dispatcher", () => {
  it("answers the version handshake", () => {
    const res = call({ jsonrpc: "2.0", id: 1, method: "version" });
    expect(res.id).toBe(1);
    expect(res.result.protocolVersion).toMatch(/^\d+\.\d+\.\d+$/);
  });

  it("reports unknown methods", () => {
    const res = call({ jsonrpc: "2.0", id: "x", method: "nope" });
    expect(res.error.code).toBe(RPC_ERRORS.METHOD_NOT_FOUND);
  });

  it("reports parse errors with a null id", () => {
    const res = JSON.parse(dispatch("{not json") ?? "null");
    expect(res.id).toBeNull();
    expect(res.error.code).toBe(RPC_ERRORS.PARSE_ERROR);
  });

  it("rejects malformed requests", () => {
    expect(call({ id: 3, method: "version" }).error.code).toBe(RPC_ERRORS.INVALID_REQUEST);
  });

  it("does not answer notifications", () => {
    expect(dispatch(JSON.stringify({ jsonrpc: "2.0", method: "version" }))).toBeNull();
  });

  it("does not dispatch to prototype members", () => {
    expect(call({ jsonrpc: "2.0", id: 4, method: "toString" }).error.code).toBe(RPC_ERRORS.METHOD_NOT_FOUND);
  });
});
