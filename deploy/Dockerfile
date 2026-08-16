# The night-shift agent as a container. Pure-stdlib Python + kubectl; no pip deps.
FROM python:3.12-slim

# kubectl (matched to the demo cluster's minor version; skew of +/-1 is fine)
ARG KUBECTL_VERSION=v1.36.1
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
 && curl -fsSLo /usr/local/bin/kubectl \
      "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
 && chmod +x /usr/local/bin/kubectl \
 && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY nightshift_agent.py /app/
COPY deploy/entrypoint.sh /app/
RUN chmod +x /app/entrypoint.sh && mkdir -p /root/.kube

ENTRYPOINT ["/app/entrypoint.sh"]
