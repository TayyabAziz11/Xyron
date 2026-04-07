"""
AI Operator — Workflows module.

Workflows are multi-step processes that span multiple agent interactions.
Each workflow has a lifecycle: queued → active → completed | failed.

In v1, workflow state is tracked via the command service.
Future iterations will add persistence and step-level tracking.

Workflow types:
  email_draft_and_send    — draft → review → send
  linkedin_post_pipeline  — draft → approve → publish
  odoo_invoice_flow       — query → generate → approve → post
  daily_reporting_cycle   — collect → summarize → distribute
"""
