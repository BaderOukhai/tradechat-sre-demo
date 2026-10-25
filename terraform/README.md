# Terraform — the whole local environment as code

`main.tf` declares the kind cluster (1 control-plane + 2 workers, same port mappings as
`deploy/kind-config.yaml`) and the two Helm releases (`monitoring`, `tradechat`).

```bash
make cluster-delete        # Terraform must own the cluster: remove the Makefile one first
cd terraform
terraform init             # download the providers (versions locked in .terraform.lock.hcl)
terraform plan             # what would change, without changing anything
terraform apply            # do it (asks for confirmation)
terraform destroy          # remove everything
```

## The four ideas to know

| Concept | What it means here |
|---|---|
| **Declarative** | `main.tf` describes the *target* (a cluster with these nodes, these releases). Terraform computes the steps. |
| **State** | `terraform.tfstate` records what Terraform created and the IDs of the real objects. Terraform compares *config ↔ state ↔ reality*. It is git-ignored: it can contain secrets (here, the cluster's client key). In a team: a remote backend (GCS, or S3 + DynamoDB) with locking. |
| **Plan / apply** | `plan` = the diff, reviewed like a pull request; `apply` = executes exactly that diff. In CI: plan on the PR, apply after merge. |
| **Drift** | Someone changes reality by hand: reality ≠ state. For the resources Terraform manages directly (the kind cluster, a release's chart version and values), the next `plan` shows it and `apply` puts it back. **But** tested: after `kubectl scale deploy chat-api --replicas=5`, `terraform plan` says *No changes* — a `helm_release` tracks the release, not the objects inside it. That layer is what Argo CD's `selfHeal` covers. |

Verified: `terraform apply` rebuilt the whole environment (cluster, images, monitoring, app) in
1 min 41 s.

Observed while building this: with the Makefile cluster already running, `terraform plan` proposed
to *create* it — Terraform only knows what is in its state. Adopting an existing object is
`terraform import`; here, deleting the Makefile cluster first is simpler.

## Why the image loading is a `local-exec`

Building and publishing images is the CI's job (`.github/workflows/ci.yml`); with a registry,
Terraform would only reference an image tag. Without a registry, images must be copied into the
kind nodes, so a `terraform_data` + `local-exec` runs `kind load`. Provisioners are a last resort
in Terraform: they are not declarative and Terraform can't detect their drift.
