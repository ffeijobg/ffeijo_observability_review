variable "cluster_name" {
  description = "kind cluster name. Distinct from the observability-platform k8s namespace deployed inside it."
  default     = "obs-platform"
}

variable "argocd_version" {
  description = "Pinned ArgoCD install manifest version. Unpinned (:stable) would silently change what gets installed on re-apply."
  default     = "v2.13.2"
}

# The repoURL every argocd/apps/*.yaml points at is hardcoded in those
# manifests (https://github.com/ffeijobg/ffeijo_observability_review.git),
# not templated from a variable here -- same reasoning as kind-config.yaml
# hardcoding its own cluster name: one file to check, no templating
# indirection. If this repo lives at a different remote, update repoURL in
# argocd/app-of-apps.yaml and argocd/apps/*.yaml directly before applying.

locals {
  kind_config_path = "${path.module}/kind-config.yaml"
  repo_root         = abspath("${path.module}/..")
}
