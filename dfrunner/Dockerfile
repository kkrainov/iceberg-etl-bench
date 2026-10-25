# syntax=docker/dockerfile:1
# dfrunner image: pinned Rust builder + pinned slim runtime (both from versions.lock.yaml,
# passed by `m0 env up --engine df` through compose build args). Multi-arch: the pins are
# multi-platform index digests, so the same file builds natively on arm64 (Phase C).
ARG BUILDER_IMAGE=rust:1.95.0-trixie
ARG RUNTIME_IMAGE=debian:trixie-slim

FROM ${BUILDER_IMAGE} AS builder
ARG TARGETARCH
WORKDIR /src
COPY Cargo.toml Cargo.lock build.rs ./
COPY src ./src
# Cache mounts keep the cargo registry, git checkouts and the target dir between builds
# (incremental rebuilds relink only). --locked: Cargo.lock is the source of truth.
RUN --mount=type=cache,id=dfrunner-registry,target=/usr/local/cargo/registry \
    --mount=type=cache,id=dfrunner-git,target=/usr/local/cargo/git \
    --mount=type=cache,id=dfrunner-target-${TARGETARCH},target=/src/target \
    cargo build --release --locked \
    && cp target/release/dfrunner /usr/local/bin/dfrunner

FROM ${RUNTIME_IMAGE}
# TLS roots for S3/Glue over https (Phase C); taken from the builder, no apt in the runtime.
COPY --from=builder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=builder /usr/local/bin/dfrunner /usr/local/bin/dfrunner
USER 65532:65532
EXPOSE 9000
ENTRYPOINT ["/usr/local/bin/dfrunner"]
CMD ["single"]
