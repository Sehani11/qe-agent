-- =============================================================
-- Supabase Storage RLS policies for qe-agent-v2
-- =============================================================
-- IMPORTANT: This file is NOT applied via Alembic.
-- Apply manually via Supabase Dashboard → Storage → Policies, OR:
--   psql $DATABASE_URL -f backend/supabase/migrations/002_storage_bucket_policies.sql
--
-- Prerequisites: The bucket must already exist in Supabase Dashboard.
--   Supabase Studio → Storage → New Bucket → name "qe-agent", Public = OFF.
--   (If you set SUPABASE_BUCKET to a different name in backend/.env, change
--    the bucket_id literal below to match.)
--
-- Layout inside the bucket:
--   {user_id}/{folder}/{path}
--     - {user_id} is auth.uid() — first path segment, used for RLS isolation.
--     - {folder}  is one of: reports, feature-files, artifacts.
--     - {path}    is the rest (e.g. <session_id>/<filename>).
--
-- Note: the backend uses the SERVICE_ROLE_KEY which bypasses RLS, so uploads
-- work without these policies. They're required only if any browser/anon-key
-- client is added later that needs to read or write storage directly.
-- =============================================================

-- Enable RLS on storage.objects (Supabase does this by default, explicit for safety)
ALTER TABLE storage.objects ENABLE ROW LEVEL SECURITY;

-- ─── qe-agent bucket ───────────────────────────────────────────────────────
-- Path scoping: (storage.foldername(name))[1] returns the first path segment
-- — which is the user_id — and we compare it to auth.uid(). This enforces
-- that only the file owner can read, write, or delete their own files,
-- regardless of which subfolder (reports / feature-files / artifacts) the
-- file lives in.

CREATE POLICY "qe_agent_user_insert" ON storage.objects
  FOR INSERT WITH CHECK (
    bucket_id = 'qe-agent' AND
    auth.uid()::text = (storage.foldername(name))[1]
  );

CREATE POLICY "qe_agent_user_select" ON storage.objects
  FOR SELECT USING (
    bucket_id = 'qe-agent' AND
    auth.uid()::text = (storage.foldername(name))[1]
  );

CREATE POLICY "qe_agent_user_update" ON storage.objects
  FOR UPDATE USING (
    bucket_id = 'qe-agent' AND
    auth.uid()::text = (storage.foldername(name))[1]
  );

CREATE POLICY "qe_agent_user_delete" ON storage.objects
  FOR DELETE USING (
    bucket_id = 'qe-agent' AND
    auth.uid()::text = (storage.foldername(name))[1]
  );
