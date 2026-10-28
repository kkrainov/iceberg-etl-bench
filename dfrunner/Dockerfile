# syntax=docker/dockerfile:1
# dfrunner image: pinned Rust builder + pinned slim runtime (both from versions.lock.yaml,
# passed by `m0 env up --engine df` through compose build args). Multi-arch: the pins are
# multi-platform index digests, so the same file builds natively on arm64 (Phase C).
ARG BUILDER_IMAGE=rust:1.95.0-trixie
ARG RUNTIME_IMAGE=debian:trixie-slim

FROM ${BUILDER_IMAGE} AS builder
ARG TARGETARCH
# Build variant (step 4c): df (engines df, df-cs), jo (df-jo, df-jo-cs: the
# apache/datafusion#24456 backport, patches/) or sail (df-sail, df-sail-cs: cargo feature
# sail-join-reorder). patches/prepare.sh sets up the source tree for each.
ARG DFRUNNER_VARIANT=df
WORKDIR /src
COPY Cargo.toml Cargo.lock build.rs ./
COPY patches ./patches
COPY src ./src
# Cache mounts keep the cargo registry, git checkouts and the target dir between builds
# (incremental rebuilds relink only); one target dir per variant. --locked: Cargo.lock
# (patches/jo.Cargo.lock for jo) is the source of truth.
RUN --mount=type=cache,id=dfrunner-registry,target=/usr/local/cargo/registry \
    --mount=type=cache,id=dfrunner-git,target=/usr/local/cargo/git \
    --mount=type=cache,id=dfrunner-target-${DFRUNNER_VARIANT}-${TARGETARCH},target=/src/target \
    detail=$(sh patches/prepare.sh "$DFRUNNER_VARIANT") \
    && features=$(if [ "$DFRUNNER_VARIANT" = sail ]; then echo "--features sail-join-reorder"; fi) \
    && DFRUNNER_VARIANT="$DFRUNNER_VARIANT" DFRUNNER_VARIANT_DETAIL="$detail" \
       cargo build --release --locked $features \
    && cp target/release/dfrunner /usr/local/bin/dfrunner

FROM ${RUNTIME_IMAGE}
# TLS roots for S3/Glue over https (Phase C); taken from the builder, no apt in the runtime.
COPY --from=builder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=builder /usr/local/bin/dfrunner /usr/local/bin/dfrunner
USER 65532:65532
EXPOSE 9000
ENTRYPOINT ["/usr/local/bin/dfrunner"]
CMD ["single"]
