-- IT Helpdesk demo schema (fake data only). Idempotent: drops and recreates the helpdesk schema.
DROP SCHEMA IF EXISTS helpdesk CASCADE;
CREATE SCHEMA helpdesk;
SET LOCAL search_path = helpdesk;  -- LOCAL: safe through transaction poolers

CREATE TABLE departments (
    id          serial PRIMARY KEY,
    name        text NOT NULL UNIQUE,
    location    text NOT NULL
);

CREATE TABLE requesters (
    id             serial PRIMARY KEY,
    full_name      text NOT NULL,
    email          text NOT NULL UNIQUE CHECK (email LIKE '%@example.com'),
    department_id  int  NOT NULL REFERENCES departments(id),
    job_title      text NOT NULL,
    is_vip         boolean NOT NULL DEFAULT false,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE agents (
    id          serial PRIMARY KEY,
    full_name   text NOT NULL,
    email       text NOT NULL UNIQUE CHECK (email LIKE '%@example.com'),
    team        text NOT NULL,          -- Service Desk, Identity & Access, Endpoint, Network, Messaging
    tier        smallint NOT NULL CHECK (tier BETWEEN 1 AND 3),
    active      boolean NOT NULL DEFAULT true
);

CREATE TABLE categories (
    id          serial PRIMARY KEY,
    code        text NOT NULL UNIQUE,
    name        text NOT NULL,
    team        text NOT NULL
);

CREATE TABLE priorities (
    id                    serial PRIMARY KEY,
    code                  text NOT NULL UNIQUE CHECK (code IN ('P1','P2','P3','P4')),
    name                  text NOT NULL,
    sla_response_minutes  int  NOT NULL CHECK (sla_response_minutes > 0),
    sla_resolve_minutes   int  NOT NULL CHECK (sla_resolve_minutes > 0)
);

CREATE TABLE statuses (
    id          serial PRIMARY KEY,
    code        text NOT NULL UNIQUE CHECK (code IN ('new','open','in_progress','pending','resolved','closed')),
    name        text NOT NULL,
    is_terminal boolean NOT NULL,
    sort_order  smallint NOT NULL
);

CREATE TABLE tickets (
    id                 serial PRIMARY KEY,
    ref                text GENERATED ALWAYS AS ('INC' || lpad(id::text, 6, '0')) STORED UNIQUE,
    subject            text NOT NULL CHECK (length(subject) BETWEEN 3 AND 200),
    description        text NOT NULL,
    requester_id       int  NOT NULL REFERENCES requesters(id),
    assignee_agent_id  int  REFERENCES agents(id),
    category_id        int  NOT NULL REFERENCES categories(id),
    priority_id        int  NOT NULL REFERENCES priorities(id),
    status_id          int  NOT NULL REFERENCES statuses(id),
    channel            text NOT NULL DEFAULT 'portal' CHECK (channel IN ('portal','email','phone','chat','walk_up')),
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    first_response_at  timestamptz,
    resolved_at        timestamptz,
    closed_at          timestamptz,
    sla_response_due   timestamptz NOT NULL,
    sla_resolve_due    timestamptz NOT NULL,
    CHECK (first_response_at IS NULL OR first_response_at >= created_at),
    CHECK (resolved_at IS NULL OR resolved_at >= created_at)
);
CREATE INDEX tickets_created_idx  ON tickets (created_at);
CREATE INDEX tickets_status_idx   ON tickets (status_id);
CREATE INDEX tickets_priority_idx ON tickets (priority_id);
CREATE INDEX tickets_fts_idx ON tickets USING gin (to_tsvector('english', subject || ' ' || description));

CREATE TABLE ticket_comments (
    id            serial PRIMARY KEY,
    ticket_id     int  NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    author_type   text NOT NULL CHECK (author_type IN ('agent','requester','system')),
    author_agent_id     int REFERENCES agents(id),
    author_requester_id int REFERENCES requesters(id),
    body          text NOT NULL,
    is_internal   boolean NOT NULL DEFAULT false,
    created_at    timestamptz NOT NULL
);
CREATE INDEX ticket_comments_ticket_idx ON ticket_comments (ticket_id, created_at);

-- SLA breach is computed, never stored: response breached if first response came after the due time
-- (or no response yet and the due time has passed); same for resolution.
CREATE VIEW ticket_sla AS
SELECT t.id AS ticket_id,
       (COALESCE(t.first_response_at, now()) > t.sla_response_due) AS response_breached,
       (COALESCE(t.resolved_at,      now()) > t.sla_resolve_due)  AS resolve_breached,
       (COALESCE(t.first_response_at, now()) > t.sla_response_due
        OR COALESCE(t.resolved_at,    now()) > t.sla_resolve_due)  AS sla_breached
FROM tickets t;

-- Denormalised read view used by the MCP server.
CREATE VIEW ticket_overview AS
SELECT t.id, t.ref, t.subject, t.description, t.channel,
       s.code AS status, p.code AS priority, c.code AS category, c.name AS category_name,
       r.full_name AS requester, r.email AS requester_email, d.name AS department,
       a.full_name AS assignee, a.email AS assignee_email, a.team AS assignee_team,
       t.created_at, t.updated_at, t.first_response_at, t.resolved_at, t.closed_at,
       t.sla_response_due, t.sla_resolve_due,
       sla.response_breached, sla.resolve_breached, sla.sla_breached
FROM tickets t
JOIN statuses s    ON s.id = t.status_id
JOIN priorities p  ON p.id = t.priority_id
JOIN categories c  ON c.id = t.category_id
JOIN requesters r  ON r.id = t.requester_id
JOIN departments d ON d.id = r.department_id
LEFT JOIN agents a ON a.id = t.assignee_agent_id
JOIN ticket_sla sla ON sla.ticket_id = t.id;

-- Reference data
INSERT INTO priorities (code, name, sla_response_minutes, sla_resolve_minutes) VALUES
 ('P1','Critical', 15,   240),
 ('P2','High',     60,   480),
 ('P3','Medium',   240,  1440*3),
 ('P4','Low',      480,  1440*5);

INSERT INTO statuses (code, name, is_terminal, sort_order) VALUES
 ('new','New',false,1), ('open','Open',false,2), ('in_progress','In progress',false,3),
 ('pending','Pending (awaiting user/third party)',false,4), ('resolved','Resolved',true,5), ('closed','Closed',true,6);

INSERT INTO categories (code, name, team) VALUES
 ('password_reset','Password reset / account unlock','Identity & Access'),
 ('mfa','Multi-factor authentication','Identity & Access'),
 ('sso','Single sign-on (SAML/OIDC)','Identity & Access'),
 ('access_request','Entra ID / application access request','Identity & Access'),
 ('vpn','VPN / remote access','Network'),
 ('network','Wi-Fi / network connectivity','Network'),
 ('hardware','Laptop / desktop hardware','Endpoint'),
 ('software','Software install / licensing','Endpoint'),
 ('printer','Printing','Endpoint'),
 ('email','Outlook / Exchange Online','Messaging'),
 ('teams','Microsoft Teams','Messaging');
