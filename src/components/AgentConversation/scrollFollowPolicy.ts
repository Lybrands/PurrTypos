export interface ScrollFollowState {
  userDetached: boolean
  leftBottomAfterDetach: boolean
}

export function createScrollFollowState(): ScrollFollowState {
  return {
    userDetached: false,
    leftBottomAfterDetach: false,
  }
}

/** Stop following synchronously on user intent, before the scroll position moves. */
export function detachScrollFollow(
  state: ScrollFollowState,
  isAtBottom: boolean,
): ScrollFollowState {
  if (state.userDetached && (isAtBottom || state.leftBottomAfterDetach)) {
    return state
  }
  return {
    userDetached: true,
    leftBottomAfterDetach: !isAtBottom,
  }
}

/**
 * An at-bottom callback fired before the user's wheel movement must not undo
 * their intent. Following resumes only after the viewport left the bottom and
 * then genuinely returned to it.
 */
export function observeScrollBottom(
  state: ScrollFollowState,
  isAtBottom: boolean,
): ScrollFollowState {
  if (!state.userDetached) return state
  if (!isAtBottom) {
    if (state.leftBottomAfterDetach) return state
    return { ...state, leftBottomAfterDetach: true }
  }
  return state.leftBottomAfterDetach ? createScrollFollowState() : state
}
