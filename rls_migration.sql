-- ─────────────────────────────────────────────────────────────────────────────
-- RLS (Row Level Security) Migration for Medical Content Bot
-- Run this ONCE in your Supabase SQL Editor (not in the bot itself).
--
-- Why RLS matters here:
--   Supabase exposes all tables through an auto-generated REST API endpoint
--   (https://<project>.supabase.co/rest/v1/<table>). Without RLS, anyone
--   who knows your anon key can read the full `users` table — leaking user IDs,
--   statuses, and premium info.
--
--   The bot connects via direct Postgres (service role / postgres superuser),
--   which bypasses RLS by design. So the bot code does NOT need to change.
--   These policies only block unauthorized REST API access.
-- ─────────────────────────────────────────────────────────────────────────────

-- Step 1: Enable RLS on every table
ALTER TABLE users              ENABLE ROW LEVEL SECURITY;
ALTER TABLE folders            ENABLE ROW LEVEL SECURITY;
ALTER TABLE files              ENABLE ROW LEVEL SECURITY;
ALTER TABLE current_caption    ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_folder_approval ENABLE ROW LEVEL SECURITY;

-- Step 2: Drop any previously created policies (idempotent re-run safety)
DROP POLICY IF EXISTS "bot_service_role_users"               ON users;
DROP POLICY IF EXISTS "bot_service_role_folders"             ON folders;
DROP POLICY IF EXISTS "bot_service_role_files"               ON files;
DROP POLICY IF EXISTS "bot_service_role_current_caption"     ON current_caption;
DROP POLICY IF EXISTS "bot_service_role_user_folder_approval" ON user_folder_approval;

DROP POLICY IF EXISTS "deny_anon_users"               ON users;
DROP POLICY IF EXISTS "deny_anon_folders"             ON folders;
DROP POLICY IF EXISTS "deny_anon_files"               ON files;
DROP POLICY IF EXISTS "deny_anon_current_caption"     ON current_caption;
DROP POLICY IF EXISTS "deny_anon_user_folder_approval" ON user_folder_approval;

-- Step 3: Allow the service_role full access (the bot uses this implicitly)
--   service_role always bypasses RLS in Supabase, but being explicit is good practice.
CREATE POLICY "bot_service_role_users"
    ON users TO service_role USING (true) WITH CHECK (true);

CREATE POLICY "bot_service_role_folders"
    ON folders TO service_role USING (true) WITH CHECK (true);

CREATE POLICY "bot_service_role_files"
    ON files TO service_role USING (true) WITH CHECK (true);

CREATE POLICY "bot_service_role_current_caption"
    ON current_caption TO service_role USING (true) WITH CHECK (true);

CREATE POLICY "bot_service_role_user_folder_approval"
    ON user_folder_approval TO service_role USING (true) WITH CHECK (true);

-- Step 4: Block all REST API access for anonymous users (most important step)
--   With RLS enabled and no anon policy, anon access is already blocked.
--   These explicit DENY policies make the intent clear and survive future
--   Supabase policy UI changes.
CREATE POLICY "deny_anon_users"
    ON users FOR ALL TO anon USING (false);

CREATE POLICY "deny_anon_folders"
    ON folders FOR ALL TO anon USING (false);

CREATE POLICY "deny_anon_files"
    ON files FOR ALL TO anon USING (false);

CREATE POLICY "deny_anon_current_caption"
    ON current_caption FOR ALL TO anon USING (false);

CREATE POLICY "deny_anon_user_folder_approval"
    ON user_folder_approval FOR ALL TO anon USING (false);

-- Step 5: Block all REST API access for authenticated users too
--   (No Telegram user should be able to query the DB directly.)
DROP POLICY IF EXISTS "deny_authenticated_users"               ON users;
DROP POLICY IF EXISTS "deny_authenticated_folders"             ON folders;
DROP POLICY IF EXISTS "deny_authenticated_files"               ON files;
DROP POLICY IF EXISTS "deny_authenticated_current_caption"     ON current_caption;
DROP POLICY IF EXISTS "deny_authenticated_user_folder_approval" ON user_folder_approval;

CREATE POLICY "deny_authenticated_users"
    ON users FOR ALL TO authenticated USING (false);

CREATE POLICY "deny_authenticated_folders"
    ON folders FOR ALL TO authenticated USING (false);

CREATE POLICY "deny_authenticated_files"
    ON files FOR ALL TO authenticated USING (false);

CREATE POLICY "deny_authenticated_current_caption"
    ON current_caption FOR ALL TO authenticated USING (false);

CREATE POLICY "deny_authenticated_user_folder_approval"
    ON user_folder_approval FOR ALL TO authenticated USING (false);

-- ─────────────────────────────────────────────────────────────────────────────
-- Verification: run these to confirm RLS is active on every table
-- ─────────────────────────────────────────────────────────────────────────────
-- SELECT tablename, rowsecurity FROM pg_tables
-- WHERE schemaname = 'public'
-- ORDER BY tablename;
