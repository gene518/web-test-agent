FROM gcr.io/projectsigstore/cosign:v2.4.1@sha256:b03690aa52bfe94054187142fba24dc54137650682810633901767d8a3e15b31 AS cosign

FROM public.ecr.aws/docker/library/python:3.13-alpine@sha256:540c7d91f98ff6880174c40e99067bf5941eb54d818a7a5e094d188b196a934d

ENV DOCKER_CONFIG=/tmp/docker \
    HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1 \
    XDG_CACHE_HOME=/tmp/cache

RUN apk add --no-cache ca-certificates docker-cli docker-cli-compose
COPY --from=cosign /ko-app/cosign /usr/local/bin/cosign

WORKDIR /app
COPY deploy/updater/common.py deploy/updater/service.py deploy/updater/reconcile.py /app/

EXPOSE 8090
HEALTHCHECK --interval=15s --timeout=5s --retries=5 \
  CMD wget -q -T 3 -O /dev/null http://127.0.0.1:8090/health || exit 1

CMD ["python", "/app/service.py"]
