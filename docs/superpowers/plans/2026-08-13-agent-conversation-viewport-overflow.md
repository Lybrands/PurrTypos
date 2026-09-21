# Shared Agent Conversation Viewport Overflow Design


## Goal

 Keep every shared Agent conversation message within its panel in both the book workspace and screenplay project page.

## Architecture

 Preserve Virtuoso as the scroll owner, but remove horizontal padding from its scroller because Virtuoso's absolutely positioned `width: 100%` viewport resolves against the scroller padding box. Put the same horizontal spacing on the Virtuoso list instead, so virtualized content receives the visual inset without increasing the internal viewport's right edge.


## Constraints

- Fix the shared `AgentConversation` component so book and screenplay consumers inherit the correction.
- Add no dependency and no consumer-specific override.
- Preserve the current 48px left inset when the turn index is present, 22px right inset on desktop, and 16px horizontal inset on narrow screens.
- Do not create a Git commit unless the user explicitly asks for one.
