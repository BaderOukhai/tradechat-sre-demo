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
    }

    post {
        always { cleanWs() }  // no leftovers (or secrets) on a shared agent
    }
}
