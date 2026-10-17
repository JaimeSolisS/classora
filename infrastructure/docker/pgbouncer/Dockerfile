# PgBouncer for the data stack (architecture §9.1, _docs/adr/0003-database-connection-topology.md).
# Build context: this directory. Debian's own pgbouncer package on a pinned slim
# base (trixie, the same Debian release as python:3.14-slim); no third-party image.
# No credential is baked in: entrypoint.sh writes the auth file at container start.
FROM debian:trixie-20261005-slim

# postgresql-client provides psql for the Compose healthcheck (a real query
# through PgBouncer to PostgreSQL).
RUN apt-get update \
    && apt-get install -y --no-install-recommends "pgbouncer=1.24.*" postgresql-client \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/pgbouncer/userlist.txt \
    && install -d -o postgres -g postgres -m 0700 /run/pgbouncer

COPY pgbouncer.ini /etc/pgbouncer/pgbouncer.ini
# entrypoint.sh is committed executable (git mode 100755); COPY keeps the mode.
COPY entrypoint.sh /usr/local/bin/pgbouncer-entrypoint

# PgBouncer refuses to run as root; the package's postgres system user runs it.
USER postgres

EXPOSE 6432

ENTRYPOINT ["pgbouncer-entrypoint"]
CMD ["pgbouncer", "/etc/pgbouncer/pgbouncer.ini"]
