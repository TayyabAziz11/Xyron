---
id: "046"
title: "Homepage Routing Animations Full Rebuild"
stage: general
date: "2026-04-07"
surface: agent
model: claude-sonnet-4-6
feature: none
branch: 001-gold-tier-full
user: Personal AI Employee
command: general task
labels: ["routing", "homepage", "framer-motion", "animations", "nextjs", "backend"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - web/src/app/layout.tsx
  - web/src/app/page.tsx
  - web/src/app/app/layout.tsx
  - web/src/app/app/page.tsx
  - web/src/app/app/dashboard/page.tsx
  - web/src/app/app/command-center/page.tsx
  - web/src/app/app/approvals/page.tsx
  - web/src/app/app/activity/page.tsx
  - web/src/app/app/integrations/page.tsx
  - web/src/app/app/workflows/page.tsx
  - web/src/app/app/settings/page.tsx
  - web/src/components/layout/Sidebar.tsx
  - web/src/components/layout/Header.tsx
  - web/src/components/layout/PageTransition.tsx
  - web/src/components/ui/Button.tsx
  - web/src/components/dashboard/ActivityFeed.tsx
  - web/src/components/dashboard/IntegrationGrid.tsx
  - web/src/lib/types.ts
  - backend/api/schemas/command.py
  - backend/api/services/command_service.py
  - README.md
tests: []
---

## Prompt

Build the following in order for AI Operator:

1. Install framer-motion
2. Restructure routing: move all app pages under /app/* route segment
3. Create /app/layout.tsx with AppShell; root layout.tsx without AppShell
4. Build a full premium landing page at /
5. Update Sidebar navigation to /app/* paths + back-to-home link
6. Create PageTransition component
7. Add framer-motion animations (Button whileTap, Homepage stagger/fadeUp)
8. Update backend command schema with intent classification
9. Update README Running section with new routes
10. Run npx tsc --noEmit — fix all errors

(Full multi-step prompt as provided in session)

## Response

Completed all 10 steps:

- Installed framer-motion
- Created /app/* route segment with 7 pages (dashboard, command-center, approvals, activity, integrations, workflows, settings)
- /app/layout.tsx wraps all /app/* pages with AppShell
- Root layout.tsx is now bare html/body (no AppShell)
- / is now a full dark premium landing page with Navbar, Hero (framer-motion stagger), HowItWorks strip, Capabilities grid (whileInView), Integrations pill row, UseCases list, FinalCTA with animated blob, Footer
- All Sidebar navItems updated to /app/* hrefs; ArrowLeft "Back to home" link added
- Header pageTitles updated to /app/* keys
- PageTransition component created at web/src/components/layout/PageTransition.tsx
- All page files wrapped in PageTransition
- Button.tsx updated with motion.button + whileTap={{ scale: 0.97 }}
- Backend: CommandIntent model added to command.py; classify_intent() function added to command_service.py; submit() now auto-classifies intent
- TypeScript types.ts updated with CommandIntent interface
- Zero old-path hrefs remaining in active source
- TypeScript: EXIT 0 (no errors)
---
outcome: success
