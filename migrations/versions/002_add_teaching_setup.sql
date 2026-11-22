-- Persist the teaching setup selected before a trainee enters a session.
ALTER TABLE session ADD COLUMN IF NOT EXISTS subject VARCHAR(100);
ALTER TABLE session ADD COLUMN IF NOT EXISTS topic VARCHAR(200);
ALTER TABLE session ADD COLUMN IF NOT EXISTS session_mode VARCHAR(20) NOT NULL DEFAULT 'practice';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'session_session_mode_check'
    ) THEN
        ALTER TABLE session
            ADD CONSTRAINT session_session_mode_check
            CHECK (session_mode IN ('practice', 'assessment'));
    END IF;
END $$;
