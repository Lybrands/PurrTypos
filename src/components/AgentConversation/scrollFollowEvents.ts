export interface ScrollFollowEventTarget {
  scrollTop: number
  getBoundingClientRect(): { right: number }
  addEventListener(type: string, listener: EventListener, options?: AddEventListenerOptions | boolean): void
  removeEventListener(type: string, listener: EventListener, options?: EventListenerOptions | boolean): void
}

export function bindScrollFollowIntent(
  target: ScrollFollowEventTarget,
  detach: (atBottom?: boolean) => void,
): () => void {
  let touchY: number | null = null
  let scrollbarPointerActive = false
  let scrollbarScrollTop = target.scrollTop
  const wheel = ((event: WheelEvent) => {
    if (event.deltaY < 0) detach()
  }) as EventListener
  const keydown = ((event: KeyboardEvent) => {
    if (
      ['ArrowUp', 'PageUp', 'Home'].includes(event.key)
      || (event.shiftKey && (event.key === ' ' || event.code === 'Space'))
    ) detach()
  }) as EventListener
  const touchstart = ((event: TouchEvent) => {
    touchY = event.touches[0]?.clientY ?? null
  }) as EventListener
  const touchmove = ((event: TouchEvent) => {
    const nextY = event.touches[0]?.clientY
    if (touchY != null && nextY != null && nextY > touchY) detach(false)
    touchY = nextY ?? touchY
  }) as EventListener
  const pointerdown = ((event: PointerEvent) => {
    const bounds = target.getBoundingClientRect()
    scrollbarPointerActive = event.clientX >= bounds.right - 16
    scrollbarScrollTop = target.scrollTop
  }) as EventListener
  const pointerend = () => { scrollbarPointerActive = false }
  const scroll = () => {
    if (scrollbarPointerActive && target.scrollTop < scrollbarScrollTop) {
      detach(false)
    }
    scrollbarScrollTop = target.scrollTop
  }
  const listeners: Array<[string, EventListener, AddEventListenerOptions | boolean | undefined]> = [
    ['wheel', wheel, { passive: true }],
    ['keydown', keydown, undefined],
    ['touchstart', touchstart, { passive: true }],
    ['touchmove', touchmove, { passive: true }],
    ['pointerdown', pointerdown, undefined],
    ['pointerup', pointerend, undefined],
    ['pointercancel', pointerend, undefined],
    ['scroll', scroll, { passive: true }],
  ]
  for (const [type, listener, options] of listeners) {
    target.addEventListener(type, listener, options)
  }
  return () => {
    for (const [type, listener] of listeners) {
      target.removeEventListener(type, listener)
    }
  }
}
