def migrate(cr, version):
    cr.execute("ALTER TABLE claude_chat_session ADD COLUMN IF NOT EXISTS res_model VARCHAR")
    cr.execute("ALTER TABLE claude_chat_session ADD COLUMN IF NOT EXISTS res_id INTEGER")
    cr.execute("CREATE INDEX IF NOT EXISTS idx_claude_session_ctx ON claude_chat_session (res_model, res_id)")
