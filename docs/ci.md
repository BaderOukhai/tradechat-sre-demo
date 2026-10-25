# CI/CD

Every push to `main` and every pull request runs the same stages, defined twice on purpose:
`.github/workflows/ci.yml` (GitHub Actions, the one that actually runs for this repo) and the
`Jenkinsfile` (declarative Jenkins pipeline, same stages, for teams on Jenkins).

| Stage | What | Why |
|---|---|---|
| Secret scan | gitleaks on the **full history** (also a pre-commit hook: `make hooks`) | a secret deleted in a later commit is still leaked; verified that it catches a kubeconfig client key |
| Lint & test | `ruff check`, `ruff format --check`, `pytest` for chat-api, archiver, ops-agent | fast feedback first, exact dependency versions from `uv.lock` (`--frozen`) |
| Lint infra | `helm lint` + render the bad-release values, `shellcheck`, `terraform fmt/validate`, ruff on the load generator | infrastructure is code too: it breaks the same way |
| Build | `docker build` per service, tagged with the commit SHA | an image is traceable to the exact commit it was built from |
| Security scan | Trivy on each image, fails on **fixable CRITICAL** vulnerabilities | security is part of "done"; unfixable CVEs are tracked, not allowed to block every build |
| Deploy + load test | throwaway 3-node kind cluster, Helm install of the SHA-tagged images, 20 simulated traders for 30 s, `scripts/e2e-check.sh` | the pipeline tests the **deployment**, not only the code: rejected if p99 ≥ 200 ms, any HTTP error, or a message not archived |

Every job writes one line to the run page (`GITHUB_STEP_SUMMARY`): opening a run shows the
verdict — tests, secrets, scans, and the SLO/compliance table of the deployed commit — without
reading logs.

Build/scan only runs if the secret scan and the tests pass, the deployment test only on scanned
images, and the workflow has read-only permissions. Third-party actions and downloaded binaries
are pinned (commit SHA, checksum).

## GitHub Actions vs Jenkins vs Cloud Build

| | GitHub Actions | Jenkins | GCP Cloud Build |
|---|---|---|---|
| Pipeline file | `.github/workflows/*.yml` | `Jenkinsfile` (Groovy DSL) | `cloudbuild.yaml` |
| Unit of work | job → steps | stage → steps | step (each one a container) |
| Runs on | GitHub-hosted or self-hosted runners | agents you operate | Google-managed workers |
| Parallelism | `strategy.matrix`, parallel jobs | `parallel { }` stages | `waitFor` between steps |
| Reuse | marketplace actions | plugins + shared libraries | builder images |
| Who operates it | GitHub | **you** (controller, agents, plugins, upgrades) | Google |

Same concepts everywhere: a versioned pipeline file next to the code, triggered by Git events,
running isolated steps, producing an artefact (the image) and gating it (tests, scan).
Jenkins gives full control (on-premises, air-gapped environments, custom agents) at the price of
operating it yourself — a real consideration for banks that keep build infrastructure in-house.

## Running the Jenkinsfile locally (optional)

```bash
docker run -d --name jenkins -p 8080:8080 -v jenkins_home:/var/jenkins_home \
  -v /var/run/docker.sock:/var/run/docker.sock jenkins/jenkins:lts-jdk21
docker exec jenkins cat /var/jenkins_home/secrets/initialAdminPassword
```

Open `http://localhost:8080`, install the suggested plugins, create a *Pipeline* job pointing to
this repository (*Pipeline script from SCM*). The agent needs `docker`, `uv`, `helm`, `shellcheck`
and `terraform` on its `PATH` (in practice: a custom agent image with these tools).
