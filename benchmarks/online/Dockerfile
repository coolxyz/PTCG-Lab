# An observer and load generator around a pinned production image, never a LAN replacement.
ARG API_IMAGE=ptcg-lab-online-api:p6e
FROM ${API_IMAGE}
COPY benchmarks/online /bench
COPY packages/online-client/src/ptcg_online_client /bench/ptcg_online_client
ENV PYTHONPATH=/bench
WORKDIR /bench
ENTRYPOINT ["python"]
