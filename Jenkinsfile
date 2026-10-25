// Declarative Jenkins pipeline: the same stages as .github/workflows/ci.yml.
// Agent requirements: docker, uv, helm, shellcheck, terraform (see docs/ci.md).
pipeline {
    agent any

    options {
        timestamps()
        timeout(time: 20, unit: 'MINUTES')  // a hung build must not block the agent forever
        disableConcurrentBuilds()
    }

    stages {
        stage('Secret scan') {
            steps {
                // Full history: a secret deleted in a later commit is still leaked.
                sh 'docker run --rm -v "$PWD:/repo" zricethezav/gitleaks:v8.30.1 git /repo --redact --no-banner'
            }
        }

        stage('Lint & test') {
            parallel {
                stage('chat-api') {
                    steps { dir('apps/chat-api') { sh 'uv sync --frozen && uv run ruff check . && uv run ruff format --check . && uv run pytest -q' } }
                }
                stage('archiver') {
                    steps { dir('apps/archiver') { sh 'uv sync --frozen && uv run ruff check . && uv run ruff format --check . && uv run pytest -q' } }
                }
                stage('ops-agent') {
                    steps { dir('ops-agent') { sh 'uv sync --frozen && uv run ruff check . && uv run ruff format --check . && uv run pytest -q' } }
                }
            }
        }

        stage('Lint infra') {
            steps {
                sh 'helm lint deploy/helm/tradechat'
                sh 'shellcheck scripts/*.sh'
                sh 'uvx ruff check --line-length 100 loadgen'
                sh 'terraform -chdir=terraform fmt -check'
                sh 'terraform -chdir=terraform init -backend=false && terraform -chdir=terraform validate'
            }
        }

        stage('Build images') {
            steps {
                sh 'docker build -t tradechat/chat-api:${GIT_COMMIT} apps/chat-api'
                sh 'docker build -t tradechat/archiver:${GIT_COMMIT} apps/archiver'
            }
        }

        stage('Security scan') {
            steps {
                // Fail on fixable CRITICAL vulnerabilities (same policy as GitHub Actions).
                sh '''
                    for app in chat-api archiver; do
                      docker run --rm -v /var/run/docker.sock:/var/run/docker.sock aquasec/trivy:0.75.0 \
                        image --severity CRITICAL --ignore-unfixed --exit-code 1 --no-progress \
                        tradechat/$app:${GIT_COMMIT}
                    done
                '''
            }
        }

        stage('Deploy + load test') {
            // Throwaway kind cluster: deploy this commit with Helm, run 20 simulated traders,
            // reject the build if the SLO is missed or a message is not archived.
            steps {
                sh 'kind create cluster --name ci-${BUILD_NUMBER} --config deploy/kind-config.yaml --wait 2m'
                sh 'kind load docker-image tradechat/chat-api:${GIT_COMMIT} tradechat/archiver:${GIT_COMMIT} --name ci-${BUILD_NUMBER}'
                sh 'helm upgrade --install tradechat deploy/helm/tradechat --wait --timeout 3m --set monitoring.enabled=false --set chatApi.image.tag=${GIT_COMMIT} --set archiver.image.tag=${GIT_COMMIT}'
                sh 'uv run loadgen/loadgen.py --duration 30 --json-out load.json'
                sh './scripts/e2e-check.sh load.json'
            }
            post {
                always { sh 'kind delete cluster --name ci-${BUILD_NUMBER} || true' }
            }
        }
    }

    post {
        always { cleanWs() }  // no leftovers (or secrets) on a shared agent
    }
}
