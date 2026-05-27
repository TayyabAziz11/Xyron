import { cn } from '@/lib/utils'

type SpinnerSize = 'sm' | 'md' | 'lg'

interface LoadingSpinnerProps {
  size?: SpinnerSize
  className?: string
}

const sizeClasses: Record<SpinnerSize, string> = {
  sm: 'h-3.5 w-3.5 border-[1.5px]',
  md: 'h-5 w-5 border-2',
  lg: 'h-8 w-8 border-2',
}

export function LoadingSpinner({ size = 'md', className }: LoadingSpinnerProps) {
  return (
    <span
      className={cn(
        'inline-block animate-spin rounded-full border-brand border-t-transparent',
        sizeClasses[size],
        className,
      )}
      aria-label="Loading"
    />
  )
}
