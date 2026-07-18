# SLO-as-Code (Sloth)

SLOs for the demo tenant app are defined declaratively here as `PrometheusServiceLevel`
CRDs. Sloth's admission controller/operator reads these and generates:

- Recording rules for the SLI over multiple windows (5m, 1h, 6h, 3d, ...)
- Multi-window, multi-burn-rate alerting rules (fast-burn "page" + slow-burn "ticket")
- Error-budget-remaining as a queryable metric, for a burn-rate dashboard in Grafana

## Install Sloth (one-time, not part of this repo's ArgoCD app)

```bash
kubectl apply -f https://raw.githubusercontent.com/slok/sloth/main/deploy/kubernetes/sloth-crds.yaml
helm repo add sloth https://slok.github.io/sloth
helm install sloth sloth/sloth -n observability-platform
```

## Adding a new tenant's SLO

Copy `demo-app-availability.slo.yaml`, change `service`/`platform.tenant`, and adjust
the PromQL to that tenant's metric names. This is the mechanism referenced in
`docs/onboarding.md` -- a new team doesn't wait on a platform engineer to hand-write
burn-rate math, they copy a template.
