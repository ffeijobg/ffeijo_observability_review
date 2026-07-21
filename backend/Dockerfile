# ════════════════════════════════════════
# STAGE 1 — Builder: install dependencies
# ════════════════════════════════════════
FROM python:3.12-slim AS builder
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /build
# Copy requirements FIRST — Docker caches this layer.
# pip install only re-runs when requirements.txt changes.
COPY requirements.txt .
# Install to a target dir — only this dir goes to final image.
# Leaves pip, setuptools, and wheel behind in the builder.
RUN pip install --no-cache-dir --target=/build/packages -r requirements.txt
COPY app.py .
# Security gate
RUN ! find /build -name '*.env' -o -name '*.key' | grep -q .

# ════════════════════════════════════════
# STAGE 2 — Hardened runtime
# ════════════════════════════════════════
FROM python:3.12-slim AS final

# Layer 1: System patch AND removal of packages with unpatched CVEs
RUN apt-get update && apt-get upgrade -y --no-install-recommends && \
    apt-get purge -y --auto-remove curl wget gcc g++ make git && \
    rm -rf /var/lib/apt/lists/* && \
    # perl-base and ncurses have fix_deferred CVEs with no Debian patch.
    # Neither is used at runtime in a Python HTTP server.
    dpkg --force-remove-essential --purge perl-base 2>/dev/null; \
    dpkg --purge --force-depends \
        libncursesw6 libtinfo6 libreadline8 \
        ncurses-base ncurses-bin 2>/dev/null; true

# Layer 2: User hardening
RUN groupadd --gid 10002 appgroup && \
    useradd --uid 10002 --gid appgroup --no-create-home \
            --shell /usr/sbin/nologin appuser
WORKDIR /app

# Layer 3: Copy only the installed packages (no pip)
COPY --from=builder --chown=root:appgroup --chmod=550 \
     /build/packages /app/packages
COPY --from=builder --chown=root:appgroup --chmod=440 \
     /build/app.py /app/app.py

# Layer 4: Remove pip from final image entirely
RUN pip uninstall pip setuptools wheel -y 2>/dev/null; true

# Layer 5: SUID removal
RUN find / -perm /6000 -type f 2>/dev/null | xargs chmod a-s 2>/dev/null; true

# Layer 6: Runtime environment
ENV PYTHONPATH=/app/packages \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=5000
USER 10002:10002
EXPOSE 5000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health')" || exit 1
CMD ["python3", "/app/app.py"]
