# P4-D API and isolated rule worker image. Rule assets are supplied separately as read-only inputs.
ARG PYTHON_IMAGE=python:3.14-slim@sha256:35caba83af2fe6da9a5d922f9b0156f21d1246a48278c58a08bd253303ba42e8
FROM ${PYTHON_IMAGE} AS wheels
WORKDIR /source
COPY apps/online-server/requirements.lock /requirements.lock
RUN python -m pip install --no-cache-dir setuptools==80.9.0 \
 && python -m pip wheel --no-cache-dir --wheel-dir /wheels -r /requirements.lock
COPY packages/engine-provider packages/engine-provider
COPY packages/battle-protocol packages/battle-protocol
COPY packages/battle-core packages/battle-core
COPY apps/online-server apps/online-server
RUN python -m pip wheel --no-deps --no-build-isolation --wheel-dir /wheels \
    ./packages/engine-provider ./packages/battle-protocol ./packages/battle-core ./apps/online-server

FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PTCG_ONLINE_V2_BIND=0.0.0.0 PTCG_ONLINE_V2_PORT=8784
COPY --from=wheels /wheels /wheels
RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels ptcg-online-server==0.6.0.dev1 \
 && rm -rf /wheels \
 && useradd --uid 10001 --create-home api
USER 10001
WORKDIR /home/api
EXPOSE 8784
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
 CMD python -c "import os,urllib.request; r=urllib.request.Request('http://127.0.0.1:8784/healthz',headers={'Host':os.environ['PTCG_ONLINE_V2_PUBLIC_ORIGIN'].split('://',1)[1]}); urllib.request.urlopen(r,timeout=2)"
ENTRYPOINT ["python", "-m", "ptcg_online_server"]
