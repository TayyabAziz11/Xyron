import { AppShell } from '@/components/layout/AppShell'
import { UIModeProvider } from '@/contexts/UIModeContext'

export default function AppLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <UIModeProvider>
      <AppShell>{children}</AppShell>
    </UIModeProvider>
  )
}
