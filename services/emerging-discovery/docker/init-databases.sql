\getenv service_password EMERGING_DISCOVERY_POSTGRES_PASSWORD
\getenv trend_password TREND_INTELLIGENCE_POSTGRES_PASSWORD

CREATE ROLE emerging_discovery
    LOGIN
    NOSUPERUSER
    NOCREATEDB
    NOCREATEROLE
    NOINHERIT
    PASSWORD :'service_password';
CREATE DATABASE emerging_discovery OWNER emerging_discovery;
CREATE DATABASE emerging_discovery_test OWNER emerging_discovery;

CREATE ROLE trend_intelligence
    LOGIN
    NOSUPERUSER
    NOCREATEDB
    NOCREATEROLE
    NOINHERIT
    PASSWORD :'trend_password';
CREATE DATABASE trend_intelligence OWNER trend_intelligence;
CREATE DATABASE trend_intelligence_test OWNER trend_intelligence;

REVOKE CONNECT ON DATABASE postgres FROM PUBLIC;
GRANT CONNECT ON DATABASE postgres TO analytics_admin;
