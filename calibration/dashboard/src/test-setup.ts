import '@testing-library/jest-dom/vitest'
import { vi } from 'vitest'

/*
 * Give Recharts a real container size.
 *
 * jsdom reports every element as 0x0. Recharts' ResponsiveContainer measures
 * its parent via ResizeObserver and refuses to draw at zero, so without this
 * every chart test would assert against an empty SVG — passing or failing for
 * reasons unrelated to the chart. The tests would look like they cover the
 * charts while covering nothing.
 *
 * Three things are needed together: the layout properties, a
 * getBoundingClientRect that agrees with them, and a ResizeObserver that
 * actually delivers a sized entry to its callback.
 */

const WIDTH = 800
const HEIGHT = 400

for (const [prop, value] of [
  ['offsetWidth', WIDTH],
  ['offsetHeight', HEIGHT],
  ['clientWidth', WIDTH],
  ['clientHeight', HEIGHT],
] as const) {
  Object.defineProperty(HTMLElement.prototype, prop, {
    configurable: true,
    value,
  })
}

HTMLElement.prototype.getBoundingClientRect = function () {
  return {
    width: WIDTH,
    height: HEIGHT,
    top: 0,
    left: 0,
    right: WIDTH,
    bottom: HEIGHT,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  } as DOMRect
}

class SizedResizeObserver {
  constructor(private callback: ResizeObserverCallback) {}

  observe(target: Element) {
    // Deliver a size immediately. A no-op observe leaves ResponsiveContainer
    // waiting forever at 0x0, which is the failure this stub exists to fix.
    this.callback(
      [
        {
          target,
          contentRect: { width: WIDTH, height: HEIGHT } as DOMRectReadOnly,
        } as ResizeObserverEntry,
      ],
      this as unknown as ResizeObserver,
    )
  }

  unobserve() {}
  disconnect() {}
}

globalThis.ResizeObserver = SizedResizeObserver as unknown as typeof ResizeObserver

/*
 * Recharts logs a console warning on any zero-size render. If the stub above
 * ever stops working, that warning is the only signal — so it is promoted to
 * a hard failure rather than left as noise scrolling past in the output.
 */
const originalWarn = console.warn
console.warn = (...args: unknown[]) => {
  const text = args.map(String).join(' ')
  if (text.includes('width(0)') || text.includes('height(0)')) {
    throw new Error(
      'Recharts rendered at zero size — the container stub in test-setup.ts ' +
        'is no longer working, and the chart tests are asserting against an ' +
        'empty SVG.',
    )
  }
  originalWarn(...(args as []))
}

vi.spyOn(console, 'error').mockImplementation((...args: unknown[]) => {
  const text = args.map(String).join(' ')
  if (text.includes('width(0)') || text.includes('height(0)')) {
    throw new Error(
      'Recharts rendered at zero size — see test-setup.ts. The chart tests ' +
        'are not testing charts.',
    )
  }
  originalWarn(...(args as []))
})
