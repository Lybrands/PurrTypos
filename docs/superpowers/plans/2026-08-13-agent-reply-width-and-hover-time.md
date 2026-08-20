# Agent Reply Width and Hover Time Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make assistant output fill the available message width while showing time as hover-only `HH:mm` metadata.

**Architecture:** Keep the fix in the shared Agent conversation UI so book and screenplay consumers inherit it. Stretch assistant children at the message container, preserve the footer's explicit side alignment, and separate persistent model metadata from hover-only time and actions.

**Tech Stack:** React 18, TypeScript, Sass, Node test runner, Vite SSR tests, Chrome layout verification.

## Global Constraints

- Assistant reply content, execution logs, and status content fill the available assistant-message width.
- Assistant footer remains left-aligned; user footer remains right-aligned.
- The active latest assistant message hides model and time until the run ends; completed assistant messages keep the model visible.
- User and assistant times display only local `HH:mm` and appear only on message hover or keyboard focus.
- Assistant footer order is model, actions, then time; only the latest assistant message keeps its actions visible without hover.
- No persistence, protocol, or business-component changes.
- Preserve unrelated worktree changes and do not commit without explicit authorization.

---

### Task 1: Shared reply width and hover metadata

**Files:**
- Modify: `src/components/AgentConversation/Panel.behavior.test.mjs`
- Modify: `src/components/AgentConversation/messageMetadata.ts`
- Modify: `src/components/AgentConversation/MessageFooter.tsx`
- Modify: `src/components/AgentConversation/MessageFooter.scss`
- Modify: `src/components/AgentConversation/ConversationViewport/index.scss`

**Interfaces:**
- `formatAgentMessageTime(value?: string): string` always returns local `HH:mm` for valid timestamps.
- `.agent-message-footer__persistent` contains always-visible model metadata.
- `.agent-message-footer__hover` contains time plus message actions and is revealed by message hover/focus.
- `.agent-conversation__message.is-assistant` stretches ordinary children; `.agent-message-footer.is-assistant` keeps its explicit left alignment.

- [x] **Step 1: Write failing regression tests**

Extend the rendered-footer test to assert that an older timestamp formats without a date, assistant model metadata is in `.agent-message-footer__persistent`, and both user/assistant time elements are in `.agent-message-footer__hover`. Use the already recorded live-page width ratio of about `0.48` as the failing CSS behavior baseline.

- [x] **Step 2: Run focused tests and confirm failure**

Run:

```bash
node --test src/components/AgentConversation/Panel.behavior.test.mjs
```

Expected: FAIL because old timestamps include a date and time is inside the persistent metadata block. The live-page CSS baseline independently fails because the assistant body occupies only about 48% of its message width.

- [x] **Step 3: Implement the minimal shared fix**

Change assistant alignment to `stretch`; simplify the time formatter to only `HH:mm`; render the assistant model in the persistent region and render time plus actions in the hover region. Preserve action ordering and directional footer alignment.

- [x] **Step 4: Run focused tests and type checking**

Run:

```bash
node --test src/components/AgentConversation/Panel.behavior.test.mjs
npm run typecheck
```

Expected: both commands exit 0.

### Task 2: Product-page and regression verification

**Files:**
- Verify only: book workspace and screenplay project page at `http://localhost:5173/`

**Interfaces:**
- The assistant body width equals the assistant article width within browser subpixel tolerance.
- Time is hidden before hover/focus and visible after hover/focus.
- Model remains visible before hover.

- [x] **Step 1: Verify book and screenplay pages**

Measure desktop layout in both products and assert reply width ratio is approximately `1`, the footer model is visible, and hover content changes from hidden to visible.

- [x] **Step 2: Verify the 700px breakpoint**

Set a temporary 700px viewport and assert no horizontal overflow, then reset the viewport override.

- [x] **Step 3: Run full relevant verification**

Run:

```bash
npm run check:purr-components
npm run test:unit
git diff --check
```

Expected: all commands exit 0.

### Task 4: Delay assistant metadata until the reply ends

**Files:**
- Modify: `src/components/AgentConversation/messageVisibility.test.ts`
- Modify: `src/components/AgentConversation/messageVisibility.ts`
- Modify: `src/components/AgentConversation/Panel.behavior.test.mjs`
- Modify: `src/components/AgentConversation/MessageFooter.tsx`
- Modify: `src/components/AgentConversation/ConversationViewport/index.tsx`

**Interfaces:**
- `assistantMessageMetadataVisible({ isLast, loading }): boolean` hides metadata only when the assistant is the current last message and the conversation is running.
- `AgentMessageFooterProps.metadataVisible?: boolean` defaults to `true`; when false, model and time are omitted while actions keep their existing visibility behavior.

- [x] **Step 1: Write failing state and rendered-footer tests**

Assert that the latest assistant metadata is hidden while `loading` is true, historical metadata stays visible, and completed latest metadata becomes visible. Render `AgentMessageFooter` with `metadataVisible: false` and assert that model and time are absent while the action region remains.

- [x] **Step 2: Run focused tests and confirm failure**

Run:

```bash
node --test src/components/AgentConversation/messageVisibility.test.ts
node --test src/components/AgentConversation/Panel.behavior.test.mjs
```

Expected: FAIL because the state helper and `metadataVisible` footer behavior do not exist.

- [x] **Step 3: Implement the minimal shared state**

Add the state helper, pass its result from `ConversationViewport`, and gate only model/time rendering inside `MessageFooter`. Do not change action visibility.

- [x] **Step 4: Run focused tests and type checking**

Run:

```bash
node --test src/components/AgentConversation/messageVisibility.test.ts
node --test src/components/AgentConversation/Panel.behavior.test.mjs
npm run typecheck
```

Expected: all commands exit 0.

- [x] **Step 5: Verify active and completed replies in both products**

On book and screenplay pages, confirm the active latest assistant reply has no model or time, then confirm completed replies show the model and reveal `HH:mm` only on hover/focus. Historical messages and action behavior remain unchanged.

- [x] **Step 6: Run full relevant verification**

Run:

```bash
npm run check:purr-components
npm run test:unit
git diff --check
```

Expected: all commands exit 0.

### Task 3: Latest assistant action visibility and footer ordering

**Files:**
- Modify: `src/components/AgentConversation/Panel.behavior.test.mjs`
- Modify: `src/components/AgentConversation/MessageFooter.tsx`
- Modify: `src/components/AgentConversation/MessageFooter.scss`
- Modify: `src/components/AgentConversation/ConversationViewport/index.tsx`

**Interfaces:**
- `AgentMessageFooterProps.actionsPersistent?: boolean` keeps only its action region visible without hover.
- Assistant footer DOM order is persistent model, actions, then hover-only time.
- `ConversationViewport` marks the last assistant-role message as persistent; later user messages do not hide that assistant's actions.

- [x] **Step 1: Write the failing footer regression test**

Render latest and historical assistant footers. Assert both place the action region before the time element, only the latest action region has `is-persistent`, and both time elements retain `08:43` text outside the persistent model region.

- [x] **Step 2: Run focused tests and confirm failure**

Run:

```bash
node --test src/components/AgentConversation/Panel.behavior.test.mjs
```

Expected: FAIL because the footer does not accept `actionsPersistent` and currently renders assistant time before actions inside one hover region.

- [x] **Step 3: Implement the minimal shared behavior**

Add the optional footer prop, render model/actions/time as separate ordered regions, reveal time and historical actions on message hover/focus, and calculate the latest assistant index from the end of `messages` before passing `actionsPersistent`.

- [x] **Step 4: Run focused tests and type checking**

Run:

```bash
node --test src/components/AgentConversation/Panel.behavior.test.mjs
npm run typecheck
```

Expected: both commands exit 0.

- [x] **Step 5: Verify both product pages**

On the book and screenplay pages, confirm the latest assistant actions are visible before hover, time is hidden before hover and appears to the right of the actions after hover, historical actions remain hover/focus only, and reply width stays full.

- [x] **Step 6: Run full relevant verification**

Run:

```bash
npm run check:purr-components
npm run test:unit
git diff --check
```

Expected: all commands exit 0.
