"""
AI Operator — Skills layer.

Skills are reusable, single-purpose modules that agents delegate to.
Each skill does exactly one thing well.

Skill categories:
  email/         — email drafting, sending, summarization
  linkedin/      — post drafting, publishing, monitoring
  reporting/     — summaries, briefings, status reports
  summarization/ — generic text summarization utilities

Legacy skills (gold/, silver/, platinum/) are preserved for backwards
compatibility with the existing watcher/scheduler infrastructure.

Adding a new skill:
  1. Create a module under the appropriate category directory
  2. Implement a single public function (or class with a clear interface)
  3. Register a caller in the relevant agent
  4. Add a brief docstring explaining inputs and outputs
"""
