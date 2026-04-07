"""
AI Operator — Approvals module.

Handles the human-in-the-loop approval pipeline:
  - ApprovalRequest: create approval request files
  - ApprovalMonitor: detect and process approve/reject decisions

The approval flow:
  1. An agent determines an action needs human sign-off
  2. ApprovalRequest creates a markdown file in Pending_Approval/
  3. A human moves the file to Approved/ or Rejected/
  4. ApprovalMonitor detects the decision and updates state
  5. Approved actions proceed to execution
"""
