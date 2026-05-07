'use client'

// Film-grain noise overlay via SVG filter — zero JS, zero re-renders.
export default function NoiseOverlay() {
  return (
    <>
      <svg className="fixed" style={{ width: 0, height: 0, position: 'absolute' }}>
        <defs>
          <filter id="takeover-noise" x="0%" y="0%" width="100%" height="100%">
            <feTurbulence
              type="fractalNoise"
              baseFrequency="0.65"
              numOctaves="3"
              stitchTiles="stitch"
              result="noise"
            />
            <feColorMatrix type="saturate" values="0" in="noise" result="grayNoise" />
            <feBlend in="SourceGraphic" in2="grayNoise" mode="multiply" result="blend" />
            <feComponentTransfer in="blend">
              <feFuncA type="linear" slope="0.06" />
            </feComponentTransfer>
          </filter>
        </defs>
      </svg>
      <div
        className="fixed inset-0 z-[57] pointer-events-none"
        style={{ filter: 'url(#takeover-noise)', opacity: 0.45, mixBlendMode: 'overlay' }}
      />
    </>
  )
}
