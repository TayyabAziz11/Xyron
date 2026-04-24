'use client'

import Link from 'next/link'
import { motion } from 'framer-motion'
import { useState, useEffect } from 'react'
import type { Variants } from 'framer-motion'
import {
  Terminal,
  Zap,
  CheckCircle,
  GitBranch,
  Activity,
  Puzzle,
  Mic,
  Mail,
  Linkedin,
  MessageCircle,
  Database,
  ArrowRight,
} from 'lucide-react'

// ── Fade-up animation variants ─────────────────────────────────────────────
const fadeUp: Variants = {
  hidden: { opacity: 0, y: 20 },
  visible: (i: number) => ({
    opacity: 1,
    y: 0,
    transition: { duration: 0.4, ease: [0.25, 0.1, 0.25, 1], delay: i * 0.1 },
  }),
}

const stagger: Variants = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.08 } },
}

const cardVariant: Variants = {
  hidden: { opacity: 0, y: 16 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.35, ease: [0.25, 0.1, 0.25, 1] } },
}

// ── Navbar ─────────────────────────────────────────────────────────────────
function Navbar() {
  const [scrolled, setScrolled] = useState(false)

  useEffect(() => {
    const handleScroll = () => setScrolled(window.scrollY > 10)
    window.addEventListener('scroll', handleScroll)
    return () => window.removeEventListener('scroll', handleScroll)
  }, [])

  return (
    <header
      className={`fixed top-0 left-0 right-0 z-50 transition-all duration-200 ${
        scrolled
          ? 'border-b border-surface-border bg-surface-base/90 backdrop-blur-md'
          : 'bg-transparent'
      }`}
    >
      <div className="max-w-6xl mx-auto px-6 flex items-center justify-between h-16">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand">
            <span className="text-sm font-bold text-white">AI</span>
          </div>
          <span className="text-sm font-semibold text-text-primary">Xyron</span>
        </div>
        <Link
          href="/app/dashboard"
          className="inline-flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:bg-brand-light transition-colors"
        >
          Open Dashboard
          <ArrowRight className="h-3.5 w-3.5" />
        </Link>
      </div>
    </header>
  )
}

// ── Hero ───────────────────────────────────────────────────────────────────
function Hero() {
  return (
    <section className="relative pt-32 pb-24 px-6 overflow-hidden">
      {/* Background glow */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 flex items-center justify-center"
      >
        <div className="h-[600px] w-[600px] rounded-full bg-brand/10 blur-[120px]" />
      </div>

      <div className="max-w-6xl mx-auto text-center relative">
        <motion.div
          custom={0}
          initial="hidden"
          animate="visible"
          variants={fadeUp}
          className="inline-flex items-center gap-2 rounded-full border border-brand/30 bg-brand/10 px-4 py-1.5 text-xs font-medium text-brand-light mb-8"
        >
          AI-Powered · Voice-Ready · Approval-Safe
        </motion.div>

        <motion.h1
          custom={1}
          initial="hidden"
          animate="visible"
          variants={fadeUp}
          className="text-5xl sm:text-6xl lg:text-7xl font-bold tracking-tight leading-[1.05] mb-6"
        >
          <span className="bg-gradient-to-r from-white to-text-secondary bg-clip-text text-transparent">
            Your Xyron.
          </span>
          <br />
          <span className="bg-gradient-to-r from-brand-light to-white bg-clip-text text-transparent">
            Working while you think.
          </span>
        </motion.h1>

        <motion.p
          custom={2}
          initial="hidden"
          animate="visible"
          variants={fadeUp}
          className="text-lg text-text-secondary max-w-2xl mx-auto mb-10 leading-relaxed"
        >
          Give natural commands. Xyron drafts emails, posts content, manages approvals,
          and runs your workflows — with a human-in-the-loop at every critical step.
        </motion.p>

        <motion.div
          custom={3}
          initial="hidden"
          animate="visible"
          variants={fadeUp}
          className="flex flex-col sm:flex-row items-center justify-center gap-4"
        >
          <Link
            href="/app/dashboard"
            className="inline-flex items-center gap-2 rounded-xl bg-brand px-6 py-3 text-sm font-semibold text-white hover:bg-brand-light transition-colors"
          >
            Open Dashboard
            <ArrowRight className="h-4 w-4" />
          </Link>
          <a
            href="https://github.com"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-2 rounded-xl border border-surface-border bg-surface-raised px-6 py-3 text-sm font-medium text-text-secondary hover:text-text-primary hover:bg-surface-overlay transition-colors"
          >
            View on GitHub
          </a>
        </motion.div>

        <motion.p
          custom={4}
          initial="hidden"
          animate="visible"
          variants={fadeUp}
          className="mt-5 text-xs text-text-muted"
        >
          Push-to-talk voice commands · coming soon
        </motion.p>
      </div>
    </section>
  )
}

// ── How it works ───────────────────────────────────────────────────────────
function HowItWorks() {
  const steps = [
    { icon: Terminal, label: 'Type or speak a command' },
    { icon: Zap, label: 'AI routes to the right agent' },
    { icon: CheckCircle, label: 'Review & approve. Done.' },
  ]

  return (
    <section className="py-16 px-6 border-y border-surface-border bg-surface-raised/40">
      <div className="max-w-6xl mx-auto">
        <div className="flex flex-col sm:flex-row items-center justify-center gap-4 sm:gap-0">
          {steps.map(({ icon: Icon, label }, i) => (
            <div key={label} className="flex items-center gap-4">
              <div className="flex flex-col items-center gap-3">
                <div className="flex h-12 w-12 items-center justify-center rounded-xl border border-brand/30 bg-brand/10">
                  <Icon className="h-5 w-5 text-brand-light" />
                </div>
                <p className="text-sm text-text-secondary text-center max-w-[120px]">{label}</p>
              </div>
              {i < steps.length - 1 && (
                <div className="hidden sm:flex items-center mx-6 text-text-muted text-xl font-light">
                  →
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}

// ── Capabilities grid ──────────────────────────────────────────────────────
function Capabilities() {
  const cards = [
    {
      icon: Terminal,
      title: 'Command Center',
      description: 'Type or speak. AI handles the rest.',
    },
    {
      icon: CheckCircle,
      title: 'Approval System',
      description: "Nothing risky happens without your sign-off.",
    },
    {
      icon: Zap,
      title: 'Workflow Engine',
      description: 'Multi-step agent workflows, tracked.',
    },
    {
      icon: Activity,
      title: 'Activity Logs',
      description: 'Full audit trail of every action.',
    },
    {
      icon: Puzzle,
      title: 'Integrations',
      description: 'Gmail, LinkedIn, WhatsApp, Odoo, Instagram.',
    },
    {
      icon: Mic,
      title: 'Voice Ready',
      description: 'Push-to-talk commands. Wake word coming soon.',
    },
  ]

  return (
    <section className="py-24 px-6">
      <div className="max-w-6xl mx-auto">
        <div className="text-center mb-16">
          <h2 className="text-3xl font-bold text-text-primary mb-4">
            Everything you need to automate work
          </h2>
          <p className="text-text-secondary max-w-xl mx-auto">
            Xyron brings together all the tools your AI needs to act — and all the controls
            you need to stay in charge.
          </p>
        </div>

        <motion.div
          variants={stagger}
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: '-60px' }}
          className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
        >
          {cards.map(({ icon: Icon, title, description }) => (
            <motion.div
              key={title}
              variants={cardVariant}
              className="group rounded-xl border border-surface-border bg-surface-raised p-6 hover:border-brand/40 transition-colors duration-200"
            >
              <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-lg bg-brand/10 border border-brand/20">
                <Icon className="h-5 w-5 text-brand-light" />
              </div>
              <h3 className="text-sm font-semibold text-text-primary mb-1.5">{title}</h3>
              <p className="text-sm text-text-muted leading-relaxed">{description}</p>
            </motion.div>
          ))}
        </motion.div>
      </div>
    </section>
  )
}

// ── Integrations ───────────────────────────────────────────────────────────
function Integrations() {
  const integrations = [
    { icon: Mail, name: 'Gmail', color: 'text-red-400' },
    { icon: Linkedin, name: 'LinkedIn', color: 'text-blue-400' },
    { icon: MessageCircle, name: 'WhatsApp', color: 'text-green-400' },
    { icon: Database, name: 'Odoo', color: 'text-purple-400' },
    { icon: Activity, name: 'Instagram', color: 'text-pink-400' },
    { icon: Zap, name: 'OpenAI', color: 'text-brand-light' },
  ]

  return (
    <section className="py-24 px-6 border-y border-surface-border bg-surface-raised/30">
      <div className="max-w-6xl mx-auto text-center">
        <h2 className="text-2xl font-bold text-text-primary mb-3">Works with your tools</h2>
        <p className="text-text-muted mb-10 text-sm">
          Native integrations with the platforms you already use.
        </p>
        <motion.div
          variants={stagger}
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true }}
          className="flex flex-wrap items-center justify-center gap-3"
        >
          {integrations.map(({ icon: Icon, name, color }) => (
            <motion.div
              key={name}
              variants={cardVariant}
              className="flex items-center gap-2 rounded-full border border-surface-border bg-surface-raised px-4 py-2 text-sm text-text-secondary"
            >
              <Icon className={`h-4 w-4 ${color}`} />
              {name}
            </motion.div>
          ))}
        </motion.div>
      </div>
    </section>
  )
}

// ── Use cases ──────────────────────────────────────────────────────────────
function UseCases() {
  const cases = [
    { emoji: 'Draft and send emails', agent: 'Email Agent' },
    { emoji: 'Create LinkedIn posts', agent: 'LinkedIn Agent' },
    { emoji: 'Review and approve agent actions', agent: 'Approval System' },
    { emoji: 'Monitor activity and audit trails', agent: 'Activity Log' },
    { emoji: 'Run business workflows', agent: 'Workflow Engine' },
    { emoji: 'Query your integrations', agent: 'Integration Status' },
  ]

  return (
    <section className="py-24 px-6">
      <div className="max-w-4xl mx-auto">
        <div className="text-center mb-16">
          <h2 className="text-3xl font-bold text-text-primary mb-4">
            What Xyron can do for you
          </h2>
        </div>
        <motion.ul
          variants={stagger}
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true }}
          className="space-y-3"
        >
          {cases.map(({ emoji, agent }) => (
            <motion.li
              key={agent}
              variants={cardVariant}
              className="flex items-center justify-between rounded-xl border border-surface-border bg-surface-raised px-5 py-4"
            >
              <span className="text-sm text-text-secondary">{emoji}</span>
              <span className="text-xs font-medium text-brand-light border border-brand/20 bg-brand/10 rounded-full px-3 py-1">
                {agent}
              </span>
            </motion.li>
          ))}
        </motion.ul>
      </div>
    </section>
  )
}

// ── Final CTA ──────────────────────────────────────────────────────────────
function FinalCTA() {
  return (
    <section className="py-32 px-6 relative overflow-hidden">
      {/* Animated gradient blob */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 flex items-center justify-center"
      >
        <div className="h-[400px] w-[400px] rounded-full bg-brand/15 blur-[100px] animate-pulse-slow" />
      </div>

      <div className="max-w-2xl mx-auto text-center relative">
        <motion.h2
          initial={{ opacity: 0, y: 16 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.4 }}
          className="text-4xl font-bold text-text-primary mb-6"
        >
          Ready to automate your work?
        </motion.h2>
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.4, delay: 0.1 }}
        >
          <Link
            href="/app/dashboard"
            className="inline-flex items-center gap-2 rounded-xl bg-brand px-8 py-4 text-base font-semibold text-white hover:bg-brand-light transition-colors"
          >
            Open Xyron
            <ArrowRight className="h-5 w-5" />
          </Link>
        </motion.div>
      </div>
    </section>
  )
}

// ── Footer ─────────────────────────────────────────────────────────────────
function Footer() {
  return (
    <footer className="border-t border-surface-border bg-surface-raised/30 py-8 px-6">
      <div className="max-w-6xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-4">
        <span className="text-xs text-text-muted">Xyron — v0.1.0</span>
        <span className="text-xs text-text-muted">
          Built with Python + Next.js · Voice pipeline coming soon
        </span>
        <div className="flex items-center gap-4">
          <Link href="/app/dashboard" className="text-xs text-text-muted hover:text-text-secondary transition-colors">
            Dashboard
          </Link>
          <a
            href="https://github.com"
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-text-muted hover:text-text-secondary transition-colors"
          >
            GitHub
          </a>
          <a
            href="http://localhost:8000/docs"
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-text-muted hover:text-text-secondary transition-colors"
          >
            Docs
          </a>
        </div>
      </div>
    </footer>
  )
}

// ── Page ───────────────────────────────────────────────────────────────────
export default function HomePage() {
  return (
    <div className="min-h-screen bg-surface-base text-text-primary">
      <Navbar />
      <main>
        <Hero />
        <HowItWorks />
        <Capabilities />
        <Integrations />
        <UseCases />
        <FinalCTA />
      </main>
      <Footer />
    </div>
  )
}
