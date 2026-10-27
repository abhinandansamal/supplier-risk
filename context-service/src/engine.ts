/**
 * The World Model engine this service runs: the vendored Meterless reference, unchanged.
 *
 * This is the only module that imports from vendor/. CI runs the vendored conformance suite with
 * WORLD_MODEL_IMPL pointing at this file, so the suite checks exactly the module the wrapper loads.
 */
export * from "../vendor/world-model/reference/src/index.ts";
