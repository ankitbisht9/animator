/** Engine build version. Bumped when compile/evaluate output may change. */
export const ENGINE_VERSION = "0.1.0";

/** Scene DSL version understood by this engine. */
export const DSL_VERSION = "1.0";

/**
 * Engine worker JSON-RPC protocol version (semver). Python refuses to talk to a
 * worker whose major version differs from the one its generated contracts expect.
 */
export const PROTOCOL_VERSION = "1.0.0";

/** Fixed logical canvas. Export scales it to any resolution through the SVG viewBox. */
export const CANVAS = { width: 1600, height: 900 } as const;
