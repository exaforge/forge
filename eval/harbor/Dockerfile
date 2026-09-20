# syntax=docker/dockerfile:1
# Build from repository root; this image never receives authentication material.
FROM rust:1.94.0-bookworm AS build
ARG TARGETARCH
ARG CARGO_BUILD_JOBS=2
ENV CARGO_BUILD_JOBS=${CARGO_BUILD_JOBS}
RUN apt-get update -qq && apt-get install -y --no-install-recommends \
    ca-certificates curl unzip pkg-config libssl-dev clang lld cmake \
    && rm -rf /var/lib/apt/lists/*
RUN case "$TARGETARCH" in \
      arm64) proto_arch=aarch_64; proto_sha=6427349140e01f06e049e707a58709a4f221ae73ab9a0425bc4a00c8d0e1ab32 ;; \
      amd64) proto_arch=x86_64; proto_sha=3e866620c5be27664f3d2fa2d656b5f3e09b5152b42f1bedbf427b333e90021a ;; \
      *) exit 1 ;; esac \
    && curl -fsSL "https://github.com/protocolbuffers/protobuf/releases/download/v29.3/protoc-29.3-linux-${proto_arch}.zip" -o /tmp/protoc.zip \
    && echo "$proto_sha  /tmp/protoc.zip" | sha256sum --check --status \
    && unzip -q /tmp/protoc.zip -d /opt/protoc && rm /tmp/protoc.zip
ENV PROTOC=/opt/protoc/bin/protoc
# The repository's ARM target-cpu=neoverse-v2 is unsuitable for general Apple Silicon VMs.
ENV RUSTFLAGS="-C target-cpu=generic -C force-unwind-tables=yes -C linker=clang -C link-arg=-fuse-ld=lld"
WORKDIR /src
COPY Cargo.toml Cargo.lock rust-toolchain.toml ./
COPY .cargo/ .cargo/
COPY crates/ crates/
COPY prod/ prod/
COPY third_party/ third_party/
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    --mount=type=cache,target=/src/target \
    cargo build --locked --release -p xai-grok-pager-bin \
    && mkdir /out && cp target/release/forge /out/forge \
    && sha256sum /out/forge > /out/forge.sha256
FROM scratch AS artifact
COPY --from=build /out/ /
