FROM postgres:16-alpine

# Layer 1: Patch base packages AND add tini for PID 1 signal handling
RUN apk upgrade --no-cache && \
    apk add --no-cache tini

# Layer 2: Pre-create unix socket directory with correct ownership
# postgres writes .s.PGSQL.5432 and .s.PGSQL.5432.lock here.
# Must be owned by postgres:postgres before USER directive.
RUN mkdir -p /var/run/postgresql && \
    chown postgres:postgres /var/run/postgresql

# Layer 3: Init script — runs ONCE on first container start
# --chmod=440: root owns it (runtime user cannot overwrite)
# group-read: postgres user can read via group membership
COPY --chown=postgres:postgres --chmod=440 \
    ./init.sql /docker-entrypoint-initdb.d/01-init.sql

# Layer 4: SUID/SGID removal
RUN find / -perm /6000 -type f 2>/dev/null | xargs chmod a-s 2>/dev/null; true

ENV PGDATA=/var/lib/postgresql/data
ENV POSTGRES_HOST_AUTH_METHOD=scram-sha-256

USER postgres
EXPOSE 5432
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["postgres"]
