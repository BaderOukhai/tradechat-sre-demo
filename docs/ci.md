# CI/CD

Every push to `main` and every pull request runs the same four stages, defined twice on purpose:
`.github/workflows/ci.yml` (GitHub Actions, the one that actually runs for this repo) and the
`Jenkinsfile` (declarative Jenkins pipeline, same stages, for teams on Jenkins).

| Stage | What | Why |
|---|---|---|
| Lint & test | `ruff check`, `ruff format --check`, `pytest` for chat-api, archiver, ops-agent | fast feedback first, exact dependency versions from `uv.lock` (`--frozen`) |
| Lint infra | `helm lint` + render the bad-release values, `shellcheck`, `terraform fmt/validate`, ruff on the load generator | infrastructure is code too: it breaks the same way |
| Build | `docker build` per service, tagged with the commit SHA | an image is traceable to the exact commit it was built from |
| Security scan | Trivy on each image, fails on **fixable CRITICAL** vulnerabilities | security is part of "done"; unfixable CVEs are tracked, not allowed to block every build |

Build/scan only runs if the tests pass (`needs: test`), and the workflow has read-only permissions.

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
