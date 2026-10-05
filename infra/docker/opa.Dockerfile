# Immutable OPA image with the versioned policy bundle, used as the API sidecar on AWS.
#   docker build -f infra/docker/opa.Dockerfile -t <registry>/enterprise-context-opa:<tag> .
FROM openpolicyagent/opa:1.0.0
COPY policies /policies
USER 1000
ENTRYPOINT ["/opa"]
CMD ["run", "--server", "--addr=127.0.0.1:8181", "/policies"]
