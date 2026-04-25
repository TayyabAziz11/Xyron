# Contributing to Xyron

First of all — thank you for helping out! This guide will walk you through everything you need to contribute, even if you've never done it before. We'll go step by step.

---

## The Big Picture (What Happens When You Contribute)

Think of the project like a shared Google Doc, but for code:

1. You make your own copy of the project ("fork" or "clone")
2. You make changes on your own separate "draft" ("branch")
3. You send those changes for review ("Pull Request")
4. The owner reviews it, gives feedback if needed, and merges it in

That's the full loop. Let's walk through each step.

---

## Step 1 — Get the Code on Your Machine

If you already have access to the repo directly (you've been added as a collaborator), just clone it:

```bash
git clone https://github.com/YOUR_USERNAME/Xyron.git
cd Xyron
```

If you **don't** have direct access, fork it first:
1. Go to the repository on GitHub
2. Click the **Fork** button (top-right corner)
3. Then clone **your fork**:

```bash
git clone https://github.com/YOUR_GITHUB_USERNAME/Xyron.git
cd Xyron
```

---

## Step 2 — Set Up the Project Locally

Follow the setup steps in [README.md](./README.md) to get the backend and web dashboard running on your machine. Make sure everything works before you start changing anything.

Quick check — confirm you can:
- Start the backend: `cd backend && python3 -m uvicorn api.main:app --reload --port 8000`
- Start the web: `cd web && npm run dev`
- Visit http://localhost:3001 and see the dashboard

---

## Step 3 — Create a Branch for Your Work

Never work directly on the `main` branch. Instead, create a new branch with a short descriptive name.

```bash
git checkout main           # make sure you start from main
git pull origin main        # grab the latest version
git checkout -b your-branch-name
```

**Good branch names:**
```
fix/volume-control-bug
feat/dark-mode-toggle
docs/update-readme
refactor/intent-router-cleanup
```

Use `fix/` for bug fixes, `feat/` for new features, `docs/` for documentation, `refactor/` for code cleanup.

---

## Step 4 — Make Your Changes

Now do your work. Edit files, fix bugs, add features — whatever your contribution is.

A few things to keep in mind:

- **Test it locally** before submitting. Make sure the backend starts and the UI works.
- **Keep changes focused.** One PR = one thing. Don't fix a bug AND add a feature in the same PR — split them up.
- **Don't commit your `.env` file.** It has your private API keys. It should already be in `.gitignore` but double-check.

---

## Step 5 — Save and Push Your Changes

First, check what you've changed:

```bash
git status
```

Stage your changes (add the files you want to include):

```bash
git add path/to/the/file.py
# Or add everything changed (be careful with this):
git add .
```

Write a short commit message that explains **what** you changed and **why**:

```bash
git commit -m "fix: volume slider now shows correct level after set action"
```

Then push your branch to GitHub:

```bash
git push origin your-branch-name
```

---

## Step 6 — Open a Pull Request (PR)

1. Go to the repository on GitHub
2. You'll usually see a yellow banner saying **"Compare & pull request"** — click it
3. If you don't see the banner, go to the **Pull Requests** tab → click **New pull request** → select your branch

Fill in the PR form:

**Title:** Short summary of what you did  
`fix: volume control now reads back actual Windows level`

**Description:** A bit more detail — what was the problem, what did you change, and how can the reviewer test it.

```
## What changed
- Fixed `exec_validator.py` to use PowerShell WMI read-back instead of amixer
- Volume set to 50% now confirms the actual Windows volume is within 5% of the target

## How to test
1. Start the backend and web dashboard
2. Say "set volume to 40"
3. Check that the spoken response says the correct value
```

Then click **Create Pull Request**.

---

## Step 7 — What Happens Next

After you open the PR:

1. **I'll review it** — I might leave comments asking questions or suggesting small changes
2. **You can update the PR** by just pushing more commits to the same branch — the PR updates automatically
3. **Once it looks good, I'll merge it** into `main`
4. Your contribution is now part of Xyron

---

## Keeping Your Branch Up to Date

If `main` gets new commits while you're working, you'll want to pull them in so your branch doesn't fall behind:

```bash
git checkout main
git pull origin main
git checkout your-branch-name
git merge main
```

Fix any conflicts that come up, then push again.

---

## What Can You Contribute?

Not sure where to start? Here are good areas:

| Area | Examples |
|---|---|
| Bug fixes | A command that routes to the wrong tool, a UI element that breaks |
| New voice commands | Adding a new intent pattern or tool handler |
| UI improvements | Better layout, clearer labels, new page |
| Documentation | Clearer instructions, missing setup steps |
| Tests | Adding test cases for routing, tools, or API endpoints |

If you're not sure whether your idea fits, just open a GitHub Issue and ask first — it's better to discuss before spending time on it.

---

## Questions?

Open a [GitHub Issue](../../issues) and I'll get back to you. Thanks for contributing!
