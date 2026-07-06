-- 001_rls_policies.sql
-- Supabase Row-Level Security policies for all user-data tables.
--
-- IMPORTANT: This file is NOT run via Alembic (auth.uid() is Supabase-specific).
-- Apply it manually after all Alembic migrations have been run:
--
--   psql $DATABASE_URL -f backend/supabase/migrations/001_rls_policies.sql
--
-- Alternatively, paste into Supabase Studio → SQL Editor and run it there.
-- This is a one-time setup step that must be re-applied if the database is reset.

-- Enable RLS on all user-data tables
ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE bdd_files ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE verification_results ENABLE ROW LEVEL SECURITY;

-- sessions: users can only see/modify their own sessions
CREATE POLICY "sessions_user_isolation" ON sessions
  FOR ALL USING (auth.uid()::text = user_id);

-- bdd_files: users can only see/modify their own BDD files
CREATE POLICY "bdd_files_user_isolation" ON bdd_files
  FOR ALL USING (auth.uid()::text = user_id);

-- chat_messages: users can only see/modify their own chat history
CREATE POLICY "chat_messages_user_isolation" ON chat_messages
  FOR ALL USING (auth.uid()::text = user_id);

-- verification_results: users can only see/modify their own results
CREATE POLICY "verification_results_user_isolation" ON verification_results
  FOR ALL USING (auth.uid()::text = user_id);
