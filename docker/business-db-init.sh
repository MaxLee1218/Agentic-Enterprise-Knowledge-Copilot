#!/bin/sh
set -eu

: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${BUSINESS_READONLY_USER:?BUSINESS_READONLY_USER is required}"
: "${BUSINESS_READONLY_PASSWORD:?BUSINESS_READONLY_PASSWORD is required}"

psql \
  --set=ON_ERROR_STOP=1 \
  --set=admin_user="$POSTGRES_USER" \
  --set=database_name="$POSTGRES_DB" \
  --set=reader_user="$BUSINESS_READONLY_USER" \
  --set=reader_password="$BUSINESS_READONLY_PASSWORD" \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" <<'SQL'
SELECT format(
    'CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT',
    :'reader_user',
    :'reader_password'
)
WHERE NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = :'reader_user'
) \gexec

SELECT format(
    'ALTER ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT',
    :'reader_user',
    :'reader_password'
) \gexec

SELECT format('ALTER ROLE %I SET default_transaction_read_only = on', :'reader_user') \gexec

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE TEMPORARY ON DATABASE :"database_name" FROM PUBLIC;

GRANT CONNECT ON DATABASE :"database_name" TO :"reader_user";
GRANT USAGE ON SCHEMA public TO :"reader_user";
GRANT SELECT ON ALL TABLES IN SCHEMA public TO :"reader_user";

ALTER DEFAULT PRIVILEGES FOR ROLE :"admin_user" IN SCHEMA public
    GRANT SELECT ON TABLES TO :"reader_user";
SQL
