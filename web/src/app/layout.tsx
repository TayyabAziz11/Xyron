import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'Xyron — Voice Driven AI OS',
  description: 'Xyron — Voice Driven AI OS. Automate your work with a voice-first AI assistant.',
  icons: {
    icon: [
      { url: '/icon-32.png', sizes: '32x32', type: 'image/png' },
      { url: '/icon-96.png', sizes: '96x96', type: 'image/png' },
      { url: '/icon-192.png', sizes: '192x192', type: 'image/png' },
    ],
    apple: '/apple-icon.png',
    shortcut: '/icon-32.png',
  },
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en">
      <body>
        {children}
      </body>
    </html>
  )
}
