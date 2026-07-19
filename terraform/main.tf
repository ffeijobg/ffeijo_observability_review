terraform {
  required_version = ">= 1.7.0"
}

# ── Cluster lifecycle ──────────────────────────────────────────────────────
# No official HashiCorp/kind provider exists (kind has no daemon or REST
# API, only a CLI wrapping Docker); community Terraform providers wrap the
# same CLI underneath. This uses an explicit null_resource + local-exec, so
# `docker ps` / `kind get clusters` show exactly what Terraform did -- no
# hidden abstraction.
resource "null_resource" "kind_cluster" {
  triggers = {
    config_sha = filesha256(local.kind_config_path)
    name       = var.cluster_name
  }

  provisioner "local-exec" {
    command = "kind create cluster --name ${var.cluster_name} --config ${local.kind_config_path}"
  }

  provisioner "local-exec" {
    when    = destroy
    command = "kind delete cluster --name ${self.triggers.name}"
  }
}

resource "null_resource" "export_kubeconfig" {
  depends_on = [null_resource.kind_cluster]

  provisioner "local-exec" {
    command = "kind export kubeconfig --name ${var.cluster_name}"
  }
}

# ── ArgoCD ──────────────────────────────────────────────────────────────────
# Installed from the pinned upstream manifest via outbound HTTPS -- this is
# the only "pull," never a push: ArgoCD polls the git remote itself
# (default every 3m) from inside the cluster. Nothing external initiates a
# connection into this host, so this is compatible with a box that CI/CD
# tooling cannot reach -- ArgoCD here plays the role of "the one thing
# already running in-cluster that's allowed to reach out," not an inbound
# integration point.
#
# server.insecure=true (patched into argocd-cmd-params-cm below) is
# deliberate, not a security shortcut taken carelessly: confirmed live that
# kubectl port-forward's SPDY tunnel is unreliable specifically with
# argocd-server's default TLS-terminating/protocol-autodetecting listener --
# repeated "connection reset by peer" mid-request, reproduced identically
# across a browser tab, a plain CLI login, and a --grpc-web CLI login, while
# every plain-HTTP forward to other Services (Grafana, Prometheus) on this
# same cluster stayed stable for 100+ minutes under the same conditions.
# Since nothing external ever reaches this host anyway (this repo's whole
# premise), ArgoCD's self-signed TLS here provides no real security benefit
# to trade away -- it was only adding tunnel fragility. Access is still
# gated by the admin password either way.
resource "null_resource" "install_argocd" {
  depends_on = [null_resource.export_kubeconfig]

  triggers = {
    cluster_id     = null_resource.kind_cluster.id
    argocd_version = var.argocd_version
  }

  provisioner "local-exec" {
    # Single-line, chained with && rather than a multi-line heredoc --
    # this repo has been edited from a Windows checkout, and a stray \r
    # embedded in a local-exec heredoc silently breaks line continuations
    # (see ffeijo_DCGM_review/README.md's "CRLF line endings" note). A
    # one-line command has no embedded newline for that to hide in.
    command = "kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f - && kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/${var.argocd_version}/manifests/install.yaml && kubectl -n argocd rollout status deployment/argocd-server --timeout=180s && kubectl -n argocd rollout status deployment/argocd-repo-server --timeout=180s && kubectl -n argocd patch configmap argocd-cmd-params-cm --type merge -p '{\"data\":{\"server.insecure\":\"true\"}}' && kubectl -n argocd rollout restart deployment argocd-server && kubectl -n argocd rollout status deployment/argocd-server --timeout=180s"
  }
}

# ── Bootstrap the app-of-apps ────────────────────────────────────────────────
# One kubectl apply of the root Application is enough -- ArgoCD's own
# automated sync (prune + selfHeal, already set on every child Application)
# takes it from there. This does NOT run `argocd app sync`: that needs a CLI
# login (admin secret) that doesn't exist until argocd-server has finished
# coming up, and automated sync makes it unnecessary for first bootstrap.
# Use `argocd app sync <name>` by hand later for the walkthrough steps the
# training plan calls out explicitly.
resource "null_resource" "bootstrap_apps" {
  depends_on = [null_resource.install_argocd]

  triggers = {
    cluster_id  = null_resource.kind_cluster.id
    app_of_apps = filesha256("${local.repo_root}/argocd/app-of-apps.yaml")
  }

  provisioner "local-exec" {
    command = "kubectl apply -f ${local.repo_root}/argocd/app-of-apps.yaml"
  }
}
