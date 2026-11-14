-- Read-only role for the MCP server. Run as the database owner/superuser after schema.sql.
-- The password is set by db/seed.py from HELPDESK_RO_PASSWORD (psql variable :ro_password if run by hand).
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'helpdesk_ro') THEN
    CREATE ROLE helpdesk_ro LOGIN;
  END IF;
END $$;
ALTER ROLE helpdesk_ro SET default_transaction_read_only = on;
ALTER ROLE helpdesk_ro SET statement_timeout = '5s';
ALTER ROLE helpdesk_ro SET search_path = helpdesk;
GRANT USAGE ON SCHEMA helpdesk TO helpdesk_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA helpdesk TO helpdesk_ro;

-- Optional least-privilege write role, only used when ENABLE_WRITES=true.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'helpdesk_rw') THEN
    CREATE ROLE helpdesk_rw LOGIN;
  END IF;
END $$;
ALTER ROLE helpdesk_rw SET statement_timeout = '5s';
ALTER ROLE helpdesk_rw SET search_path = helpdesk;
GRANT USAGE ON SCHEMA helpdesk TO helpdesk_rw;
GRANT SELECT ON ALL TABLES IN SCHEMA helpdesk TO helpdesk_rw;
GRANT INSERT, UPDATE ON helpdesk.tickets, helpdesk.ticket_comments TO helpdesk_rw;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA helpdesk TO helpdesk_rw;
