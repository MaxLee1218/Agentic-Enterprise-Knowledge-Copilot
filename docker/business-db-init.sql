-- Development/demo role boundary for the synthetic Supplier Quality PostgreSQL service only.
-- Production database identities and grants remain an enterprise deployment responsibility.

CREATE ROLE quality_readonly
    LOGIN
    PASSWORD 'quality_readonly_local_password'
    NOSUPERUSER
    NOCREATEDB
    NOCREATEROLE
    NOINHERIT;

GRANT USAGE ON SCHEMA public TO quality_readonly;

ALTER DEFAULT PRIVILEGES FOR ROLE quality_seed IN SCHEMA public
    GRANT SELECT ON TABLES TO quality_readonly;
