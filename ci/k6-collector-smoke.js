// Minimal k6 smoke test: confirms the OTel Collector's OTLP/HTTP endpoint is
// up and accepting connections in CI (full trace-generating load lives in
// ffeijo_3tier_scaling_stack once the demo app is instrumented and deployed
// alongside this platform in a non-CI environment).
import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
  vus: 2,
  duration: "10s",
  thresholds: {
    http_req_failed: ["rate<0.05"],
  },
};

export default function () {
  // OTLP/HTTP requires a real protobuf/JSON OTLP body to fully succeed;
  // this smoke test just confirms the port is listening and responds
  // (4xx on an empty POST is expected and fine -- we're checking liveness,
  // not exercising the full pipeline). Full pipeline validation happens
  // in the chaos game-day (chaos/gameday-runbook.md) against a real cluster.
  const res = http.post("http://localhost:4318/v1/traces", "{}", {
    headers: { "Content-Type": "application/json" },
  });
  check(res, {
    "collector responded (not connection-refused)": (r) => r.status !== 0,
  });
  sleep(1);
}
