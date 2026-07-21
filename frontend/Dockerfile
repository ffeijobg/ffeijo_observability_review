# ════════════════════════════════════════════════
# STAGE 1 — Builder
# Rule: build artifacts only. Nothing here ships.
# ════════════════════════════════════════════════
FROM alpine:3.19 AS builder
WORKDIR /build
RUN apk add --no-cache curl
COPY html/ ./html/
# Security gate: abort if secrets accidentally committed
RUN ! find /build -type f \( -name '*.env' -o -name '*.key' \
       -o -name '*.pem' -o -name 'id_rsa*' \) | grep -q .
 
# ════════════════════════════════════════════════
# STAGE 2 — Final hardened image
# Verify: docker pull nginx:<tag> && trivy image nginx:<tag> | grep -i alpine
# ════════════════════════════════════════════════
FROM nginx:1.28.3-alpine-slim AS final
 
# Layer 1: System patch AND package manager removal
# Explicitly delete apk binary — attacker cannot install tools without it.
# Keep /lib/apk/db/ — Trivy reads this for CVE scanning.
RUN apk upgrade --no-cache && \
    rm -rf /var/cache/apk/* /tmp/* \
           /sbin/apk /etc/apk /usr/share/apk
 
# Layer 2: User hardening
# -H = no home dir  -D = no password  -s = nologin shell
RUN deluser nginx 2>/dev/null; \
    addgroup -g 10001 -S appgroup && \
    adduser  -u 10001 -S appuser -G appgroup -H -D -s /sbin/nologin
 
# Layer 3: Writable temp dirs owned by UID 10001
# nginx writes pid, cache, and client body temp to these paths
RUN mkdir -p /tmp/nginx/client_temp /tmp/nginx/proxy_temp \
             /tmp/nginx/fastcgi_temp /var/cache/nginx && \
    chown -R 10001:10001 /tmp/nginx /var/cache/nginx && \
    chmod 750 /tmp/nginx /var/cache/nginx
 
# Layer 4: Config hardening
# --chmod=440: owner+group read-only, world has no access
COPY --chown=root:appgroup --chmod=440 nginx.conf /etc/nginx/nginx.conf
RUN rm -f /etc/nginx/conf.d/default.conf
 
# Layer 5: Content deployment from builder stage
COPY --from=builder --chown=root:appgroup --chmod=550 \
     /build/html /usr/share/nginx/html
 
# Layer 6: Strip SUID/SGID bits from all binaries
RUN find / -perm /6000 -type f 2>/dev/null | \
    xargs chmod a-s 2>/dev/null; true
 
# Layer 7: Drop to non-root UID
USER 10001:10001
EXPOSE 8080
 
# Layer 8: Health check via wget
# nginx.conf defines no /health location — probe '/' (the static index page)
# instead, matching the readiness/liveness probes in k8s/05-frontend.yaml.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD wget -qO- http://localhost:8080/ || exit 1
 
CMD ["nginx", "-g", "daemon off;"]
