// Generates real HTTP + DB traffic against the three-tier demo app
// (ffeijo_3tier_hardened_stack) for Week 2 of the training plan: producing
// spans for Tempo, log lines for Loki, and traffic for the Sloth-generated
// SLO burn-rate rules to actually have something to measure. Unlike
// k6-collector-smoke.js (which only checks the Collector's OTLP port is
// listening), this exercises the app's real endpoints end to end.
//
// The frontend's nodePort (30080) collides with argocd-server-nodeport.yaml's
// nodePort on this cluster (see terraform/kind-config.yaml) and isn't mapped
// to any host port anyway, so reach it the way every other Service on this
// platform is reached -- kubectl port-forward, not NodePort:
//   kubectl -n three-tier-app port-forward svc/frontend 8081:8080 &
import http from "k6/http";
import { check, sleep } from "k6";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8081";

export const options = {
  vus: 5,
  duration: "60s",
  thresholds: {
    http_req_failed: ["rate<0.05"],
  },
};

export default function () {
  const roll = Math.random();

  if (roll < 0.6) {
    // GET /data -> backend SELECT, the read path most traffic should exercise
    const res = http.get(`${BASE_URL}/api/data`);
    check(res, { "GET /api/data 200": (r) => r.status === 200 });
  } else if (roll < 0.9) {
    // GET /health -> SELECT 1, cheap traffic to keep the availability SLO fed
    const res = http.get(`${BASE_URL}/api/health`);
    check(res, { "GET /api/health 200": (r) => r.status === 200 });
  } else {
    // POST /data -> backend INSERT, the other DB code path.
    // key must be unique (UNIQUE constraint in app_data) or the backend
    // returns 409, so include vu/iter/timestamp.
    const body = JSON.stringify({
      key: `k6-${__VU}-${__ITER}-${Date.now()}`,
      value: "load-test",
    });
    const res = http.post(`${BASE_URL}/api/data`, body, {
      headers: { "Content-Type": "application/json" },
    });
    check(res, { "POST /api/data 201": (r) => r.status === 201 });
  }

  sleep(1);
}
