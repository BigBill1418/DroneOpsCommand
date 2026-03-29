# --- Build stage ---
FROM rust:1.85-bookworm AS builder
WORKDIR /app
COPY Cargo.toml Cargo.lock* ./
COPY src/ src/
RUN cargo build --release

# --- Runtime stage ---
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl && rm -rf /var/lib/apt/lists/*
RUN useradd -r -s /usr/sbin/nologin parser
COPY --from=builder /app/target/release/flight-parser /usr/local/bin/flight-parser
ENV PARSER_PORT=8100
EXPOSE 8100
USER parser
CMD ["flight-parser"]
