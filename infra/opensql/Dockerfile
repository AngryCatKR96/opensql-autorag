# OpenSQL for PostgreSQL (Tmax) single node image for the AutoRAG demo stack.
#
# The distribution tarball and the license XML are licensed Tmax artifacts and
# are not vendored in this repository. Stage them first:
#
#   ./infra/opensql/stage-artifacts.sh \
#       ~/Downloads/Tmax_OpenSQL_3.17.8.7_rockylinux9.7_buildtime20260720.tar.gz \
#       ~/Downloads/<your-license>.xml
#
# The shipped binaries are x86_64 only, so build and run this image as
# linux/amd64 (emulated on Apple Silicon).

ARG OPENSQL_PACKAGE=Tmax_OpenSQL_3.17.8.7_rockylinux9.7_buildtime20260720.tar.gz

# The platform is pinned in the Dockerfile rather than left to the caller: the
# distribution is x86_64 only, and the legacy docker builder ignores the
# platform requested by compose.
FROM --platform=linux/amd64 rockylinux/rockylinux:9 AS build

ARG OPENSQL_PACKAGE
ENV OPENSQL_INSTALL_HOME=/opt/opensql-install \
    OPENSQL_HOME=/opt/opensql \
    PG_HOME=/opt/opensql/pgsql \
    PG_DATA_DIR=/opt/opensql/data

RUN dnf -y install gcc make libicu findutils && dnf clean all && rm -rf /var/cache/dnf

COPY dist/${OPENSQL_PACKAGE} /tmp/opensql.tar.gz
RUN mkdir -p "$OPENSQL_INSTALL_HOME" \
    && tar xzf /tmp/opensql.tar.gz -C "$OPENSQL_INSTALL_HOME" --strip-components=1 \
    && rm -f /tmp/opensql.tar.gz

# PostgreSQL 17.8 server and client, installed by the vendor script.
RUN cd "$OPENSQL_INSTALL_HOME/scripts" && ./install.sh postgresql

# `opensql` reports package and license information.
RUN cd "$OPENSQL_INSTALL_HOME/scripts" && ./install.sh opensql

# pgvector 0.8.1. Built directly rather than through install.sh so OPTFLAGS can
# be cleared: the default -march=native is unsafe under x86_64 emulation.
RUN cd "$OPENSQL_INSTALL_HOME/pgvector" \
    && make OPTFLAGS="" USE_PGXS=1 PG_CONFIG="$PG_HOME/bin/pg_config" \
    && make install USE_PGXS=1 PG_CONFIG="$PG_HOME/bin/pg_config"

# opensql_license enforces the Tmax license from shared_preload_libraries.
# o2 ships prebuilt .so files, so use the copy path of the vendor installer; its
# make path expects C sources that the distribution does not contain.
RUN cd "$(find "$OPENSQL_INSTALL_HOME" -maxdepth 1 -type d -name 'o2-dist-*')" \
    && mv Makefile Makefile.unused \
    && PATH="$PG_HOME/bin:$PATH" bash ./install.sh opensql_license

FROM --platform=linux/amd64 rockylinux/rockylinux:9

ENV OPENSQL_HOME=/opt/opensql \
    PG_HOME=/opt/opensql/pgsql \
    PGDATA=/opt/opensql/data \
    OPENSQL_LICENSE_PATH=/opt/opensql/license/license.xml \
    PATH=/opt/opensql/pgsql/bin:/opt/opensql/bin:$PATH

RUN dnf -y install libicu && dnf clean all && rm -rf /var/cache/dnf \
    && groupadd -g 1000 opensql \
    && useradd -u 1000 -g opensql -m -d /home/opensql opensql

COPY --from=build /opt/opensql /opt/opensql
COPY --from=build /etc/ld.so.conf.d/opensql.conf /etc/ld.so.conf.d/opensql.conf
RUN ldconfig

COPY entrypoint.sh /usr/local/bin/opensql-entrypoint.sh
RUN chmod 755 /usr/local/bin/opensql-entrypoint.sh \
    && mkdir -p "$PGDATA" "$OPENSQL_HOME/license" /docker-entrypoint-initdb.d \
    && chmod 750 "$PGDATA" \
    && chown -R opensql:opensql /opt/opensql

USER opensql
WORKDIR /home/opensql
EXPOSE 5432

ENTRYPOINT ["/usr/local/bin/opensql-entrypoint.sh"]
CMD ["postgres"]
