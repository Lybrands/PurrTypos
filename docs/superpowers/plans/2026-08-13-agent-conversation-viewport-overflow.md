# Shared Agent Conversation Viewport Overflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep every shared Agent conversation message within its panel in both the book workspace and screenplay project page.

**Architecture:** Preserve Virtuoso as the scroll owner, but remove horizontal padding from its scroller because Virtuoso's absolutely positioned `width: 100%` viewport resolves against the scroller padding box. Put the same horizontal spacing on the Virtuoso list instead, so virtualized content receives the visual inset without increasing the internal viewport's right edge.

**Tech Stack:** React 18, TypeScript, Sass, react-virtuoso 4.18.3, browser box-model verification.

## Global Constraints

- Fix the shared `AgentConversation` component so book and screenplay consumers inherit the correction.
- Add no dependency and no consumer-specific override.
- Preserve the current 48px left inset when the turn index is present, 22px right inset on desktop, and 16px horizontal inset on narrow screens.
- Do not modify or commit unrelated user changes already present in the worktree.
- Do not create a Git commit unless the user explicitly asks for one.

---

### Task 1: Move horizontal spacing inside the virtualized list

**Files:**
- Modify: `src/components/AgentConversation/ConversationViewport/index.scss`
- Test: live `http://localhost:5173/` book workspace and screenplay project page

**Interfaces:**
- Consumes: Virtuoso's existing `.agent-conversation` scroller and `.agent-conversation__turns` list classes.
- Produces: a layout where `.agent-conversation__turns.getBoundingClientRect().right <= .agent-conversation.getBoundingClientRect().right` in every shared panel.

- [x] **Step 1: Record the failing browser assertion**

```js
const scroller = document.querySelector('.agent-conversation').getBoundingClientRect()
const turns = document.querySelector('.agent-conversation__turns').getBoundingClientRect()
console.assert(turns.right <= scroller.right)
```

Expected before the fix: FAIL because the current turn list ends 48px beyond the scroller on the open book workspace.

- [x] **Step 2: Implement the minimal shared CSS correction**

```scss
.agent-conversation {
  padding-block: 18px;
}

.agent-conversation__turns {
  padding-inline: 22px;
}

.agent-conversation-shell:has(.chat-turn-index) .agent-conversation__turns {
  padding-left: 48px;
}

@media (max-width: 720px) {
  .agent-conversation__turns {
    padding-inline: 16px;
  }
}
```

- [x] **Step 3: Verify the browser assertion turns green**

Reload both shared-panel consumers and assert that the turn list and every visible message stay within the scroller's right edge, with `scrollWidth === clientWidth` on the scroller.

- [x] **Step 4: Run static and focused regression checks**

Run:

```bash
npm run typecheck
node --experimental-strip-types --test src/components/AgentConversation/viewportSession.test.ts src/components/AgentConversation/scrollFollowPolicy.test.ts
git diff --check
```

Expected: all commands exit 0.
