"""
AI Operator — Integrations module.

Integration adapters wrap external services and provide a clean, consistent
interface for skills and agents. Each adapter handles:
  - Authentication / credential loading
  - API calls with retry and error handling
  - Result normalization to standard Python types

Available adapters:
  GmailAdapter     — email read/send/search
  LinkedInAdapter  — post creation and publishing
  OdooAdapter      — ERP queries and writes
  WhatsAppAdapter  — message read/send via MCP
  InstagramAdapter — post publishing via Graph API

Adding a new integration:
  1. Create a module in this directory
  2. Implement a class with a consistent interface
  3. Load credentials from .secrets/ (never hardcode)
  4. Handle auth errors gracefully — raise IntegrationError
"""
