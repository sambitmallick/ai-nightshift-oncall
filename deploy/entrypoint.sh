#!/bin/sh
# Build a kubeconfig from the pod's mounted ServiceAccount so plain `kubectl`
# works in-cluster. tokenFile (not an embedded token) so kubectl re-reads the
# auto-rotated projected token. Then run the agent.
set -e
SA=/var/run/secrets/kubernetes.io/serviceaccount
cat > /root/.kube/config <<EOF
apiVersion: v1
kind: Config
clusters:
- name: incluster
  cluster:
    server: https://kubernetes.default.svc
    certificate-authority: ${SA}/ca.crt
contexts:
- name: incluster
  context:
    cluster: incluster
    user: sa
    namespace: default
current-context: incluster
users:
- name: sa
  user:
    tokenFile: ${SA}/token
EOF
exec python /app/nightshift_agent.py "$@"
