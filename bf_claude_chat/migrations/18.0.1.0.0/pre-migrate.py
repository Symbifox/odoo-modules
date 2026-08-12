"""Pre-migration: drop old v2.x tables and clean up stale registry references.

The old bf_claude_chat (v18.0.2.3.0) used direct Anthropic API calls with
models claude.chat.conversation and claude.chat.message (incompatible schema).
The new version (v18.0.1.0.0) uses claude.chat.session and claude.chat.message
with a different schema (session_id FK instead of conversation_id).
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    _logger.info("bf_claude_chat pre-migrate: cleaning up old v2.x schema")

    # Drop old tables (cascade removes FK constraints)
    cr.execute("DROP TABLE IF EXISTS claude_chat_message CASCADE")
    cr.execute("DROP TABLE IF EXISTS claude_chat_conversation CASCADE")

    # Clean up ir_model_data references for removed models/XML IDs
    cr.execute("""
        DELETE FROM ir_model_data
        WHERE module = 'bf_claude_chat'
          AND model IN (
              'ir.model', 'ir.model.fields', 'ir.model.access',
              'ir.rule', 'ir.actions.client', 'ir.ui.menu',
              'ir.ui.view', 'ir.actions.act_window',
              'res.partner', 'ir.model.constraint'
          )
    """)

    # Remove old model registrations
    cr.execute("""
        DELETE FROM ir_model
        WHERE model IN ('claude.chat.conversation', 'claude.chat.message')
    """)

    # Remove old access rules
    cr.execute("""
        DELETE FROM ir_model_access
        WHERE name LIKE '%claude.chat%'
    """)

    # Remove old ir_rule entries
    cr.execute("""
        DELETE FROM ir_rule
        WHERE name LIKE '%Claude%'
    """)

    _logger.info("bf_claude_chat pre-migrate: cleanup complete")
