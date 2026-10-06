-- A read-only login for the pension chat's PBMS reads, to replace `sa`.
--
-- The backend only ever runs the named SELECT statements in
-- src/pension_chat/pbms/queries.py (each checked read-only at import), over
-- ApplicationIntent=ReadOnly connections. This login lets it do exactly that,
-- and nothing else: no writes, no DDL, no other databases.
--
-- Run as a sysadmin (the client's DBA) on newncgsql. Replace the password with
-- one from the client's secret store; put the same value in the Kubernetes
-- Secret as PBMS_SQL_PASSWORD. Change [PbsMeabDev] if production uses another
-- database.

USE [master];
GO
CREATE LOGIN [pension_chat_reader]
    WITH PASSWORD = N'REPLACE-with-a-strong-password',
         DEFAULT_DATABASE = [PbsMeabDev],
         CHECK_POLICY = ON,
         CHECK_EXPIRATION = OFF;
GO

USE [PbsMeabDev];
GO
CREATE USER [pension_chat_reader] FOR LOGIN [pension_chat_reader];
GO
-- Read everything in this database, write nothing.
ALTER ROLE [db_datareader] ADD MEMBER [pension_chat_reader];
ALTER ROLE [db_denydatawriter] ADD MEMBER [pension_chat_reader];
GO
-- Optional, stricter: instead of db_datareader, grant SELECT only on the
-- tables the app reads (see the FROM clauses in pbms/queries.py), e.g.
--   GRANT SELECT ON dbo.T_Person_Info TO [pension_chat_reader];
--
-- Verify (as the new login):
--   SELECT TOP 1 * FROM dbo.T_Person_Info;          -- works
--   UPDATE dbo.T_Person_Info SET ... WHERE 1 = 0;    -- fails: permission denied
