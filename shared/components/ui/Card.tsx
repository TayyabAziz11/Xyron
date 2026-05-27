import { cn } from '@/lib/utils'

interface CardProps {
  className?: string
  children: React.ReactNode
}

export function Card({ className, children }: CardProps) {
  return (
    <div
      className={cn(
        'bg-surface-raised border border-surface-border rounded-xl',
        className,
      )}
    >
      {children}
    </div>
  )
}

export function CardHeader({ className, children }: CardProps) {
  return (
    <div className={cn('flex items-center justify-between px-5 pt-5 pb-4', className)}>
      {children}
    </div>
  )
}

export function CardTitle({ className, children }: CardProps) {
  return (
    <h3 className={cn('text-sm font-semibold text-text-primary', className)}>{children}</h3>
  )
}

export function CardContent({ className, children }: CardProps) {
  return <div className={cn('px-5 pb-5', className)}>{children}</div>
}

export function CardFooter({ className, children }: CardProps) {
  return (
    <div
      className={cn(
        'px-5 py-3 border-t border-surface-border bg-surface-overlay/50 rounded-b-xl',
        className,
      )}
    >
      {children}
    </div>
  )
}
