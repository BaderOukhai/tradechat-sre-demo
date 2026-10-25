# Infrastructure as Code for the whole local environment: the kind cluster and both Helm releases.
# Equivalent to "make cluster images monitoring deploy", but declarative: Terraform compares this
# file with its state and only does what is missing. Use one or the other, not both at once
# (same cluster name and host ports).

terraform {
  required_version = ">= 1.9"
  required_providers {
    kind = {
      source  = "tehcyx/kind"
      version = "~> 0.13"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 3.3"
    }
  }
  # Local state file (terraform.tfstate, git-ignored). In a team: a remote backend with locking
  # (GCS bucket or S3 + DynamoDB) so two people can't apply at the same time.
}

variable "cluster_name" {
  type    = string
  default = "tradechat"
}

variable "app_version" {
  description = "Tag of the chat-api and archiver images to load into the cluster."
  type        = string
  default     = "1.0.1"
}

resource "kind_cluster" "this" {
  name           = var.cluster_name
  wait_for_ready = true

  kind_config {
    kind        = "Cluster"
    api_version = "kind.x-k8s.io/v1alpha4"

    node {
      role = "control-plane"
      # Same mappings as deploy/kind-config.yaml: NodePorts reachable on localhost.
      extra_port_mappings {
        container_port = 30080 # chat-api
        host_port      = 8000
      }
      extra_port_mappings {
        container_port = 30300 # Grafana
        host_port      = 3000
      }
      extra_port_mappings {
        container_port = 30090 # Prometheus
        host_port      = 9090
      }
      extra_port_mappings {
        container_port = 30093 # Alertmanager
        host_port      = 9093
      }
    }
    node {
      role = "worker"
    }
    node {
      role = "worker"
    }
  }
}

# Building images is CI's job, not Terraform's: this only loads locally built images into the
# nodes (no registry in this demo). Re-runs when the cluster or the version changes.
resource "terraform_data" "load_images" {
  triggers_replace = [kind_cluster.this.id, var.app_version]

  provisioner "local-exec" {
    working_dir = "${path.module}/.."
    command     = "docker compose build && kind load docker-image tradechat/chat-api:${var.app_version} tradechat/archiver:${var.app_version} --name ${var.cluster_name}"
  }
}

provider "helm" {
  kubernetes = {
    host                   = kind_cluster.this.endpoint
    client_certificate     = kind_cluster.this.client_certificate
    client_key             = kind_cluster.this.client_key
    cluster_ca_certificate = kind_cluster.this.cluster_ca_certificate
  }
}

resource "helm_release" "monitoring" {
  name             = "monitoring"
  repository       = "https://prometheus-community.github.io/helm-charts"
  chart            = "kube-prometheus-stack"
  version          = "92.13.1" # pinned, same as the Makefile
  namespace        = "monitoring"
  create_namespace = true
  values           = [file("${path.module}/../deploy/monitoring/values.yaml")]
  timeout          = 600
}

resource "helm_release" "tradechat" {
  name      = "tradechat"
  chart     = "${path.module}/../deploy/helm/tradechat"
  namespace = "default"
  timeout   = 300

  # The chart creates ServiceMonitors and PrometheusRules: their CRDs come with the monitoring
  # release. And the pods need the images already loaded in the nodes.
  depends_on = [helm_release.monitoring, terraform_data.load_images]
}

output "kubeconfig_context" {
  value = "kind-${var.cluster_name}"
}

output "urls" {
  value = {
    chat_api     = "http://localhost:8000"
    grafana      = "http://localhost:3000/d/messaging-slo"
    prometheus   = "http://localhost:9090"
    alertmanager = "http://localhost:9093"
  }
}
