# Agent Message Footer and Composer Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give book and screenplay conversations one consistent message footer, directional alignment, hover-only actions, and a unified conversation/composer surface.

**Architecture:** Keep message data in the existing shared `AgentConversationMessage` contract. Add one shared footer renderer that consumes persisted time/model metadata and product-injected actions; keep book-only favorite behavior at the extension boundary while rendering it in the shared action slot. Let the shared panel main area own the surface background, with the Virtuoso scroller and composer inheriting it.

**Tech Stack:** React 18, TypeScript, Sass, react-virtuoso, Node test runner, Vite SSR tests, browser box-model verification.

## Global Constraints

- User messages and their footer stay right-aligned.
- Assistant replies and their footer stay left-aligned.
- Time and assistant model metadata remain visible; action buttons render visually only on message hover or keyboard focus.
- Favorite, copy, and edit actions use one footer action region; attachments contain artifacts only.
- Book and screenplay consumers inherit the fix from shared components.
- The main conversation column owns one background; the composer has no independent background.
- Preserve unrelated worktree changes and do not commit without explicit user authorization.

---

### Task 1: Shared directional message footer

**Files:**
- Create: `src/components/AgentConversation/MessageFooter.tsx`
- Create: `src/components/AgentConversation/MessageFooter.scss`
- Create: `src/components/AgentConversation/messageMetadata.ts`
- Modify: `src/components/AgentConversation/ConversationViewport/index.tsx`
- Modify: `src/components/AgentConversation/ConversationViewport/index.scss`
- Modify: `src/components/AgentConversation/Panel.tsx`
- Modify: `src/components/AgentConversation/Panel.behavior.test.mjs`
- Modify: `src/components/AgentConversation/UserMessageBody.tsx`
- Delete: `src/components/AgentConversation/UserMessageMeta.tsx`
- Delete: `src/components/AgentConversation/UserMessageMeta.scss`

**Interfaces:**
- `MessageFooter` consumes `side`, `sentAt`, optional `model`, model label aliases, and action nodes.
- `ConversationViewport` consumes `modelLabels` and optional `afterAssistantMessageActions`.
- The footer emits `.agent-message-footer`, `.agent-message-footer__metadata`, and `.agent-message-footer__actions`.

- [x] **Step 1: Add a failing rendered-panel test**

Assert rendered user/assistant footers have directional classes, visible time/model metadata with configured-name fallback, and grouped action regions.

- [x] **Step 2: Run tests and confirm the expected failure**

Run:

```bash
node --test src/components/AgentConversation/Panel.behavior.test.mjs
```

Expected: FAIL because the shared footer and assistant action slot do not exist.

- [x] **Step 3: Implement the minimal footer**

Render actions before metadata on user messages so the right-side time remains anchored, and after metadata on assistant messages so the left-side model/time remains anchored. Use `display: none` until the owning message is hovered or focused.

- [x] **Step 4: Re-run focused tests**

Expected: the command exits 0.

### Task 2: Supply assistant metadata and product actions

**Files:**
- Modify: `src/components/AgentConversation/extensions.ts`
- Modify: `src/Workspace/AiPanel/BookConversationExtensions.tsx`
- Modify: `src/Workspace/AiPanel/hooks/useChatSubmit.ts`
- Modify: `src/Workspace/AiPanel/utils.ts`

**Interfaces:**
- `renderAssistantActions(message, index)` supplies product actions without placing them in artifact content.
- Live and persisted book assistant messages expose `sentAt` and `model` through the existing message contract.

- [x] **Step 1: Move favorite into the shared action slot**

Keep setting-diff cards in `renderAssistantAttachment`; return the favorite button from `renderAssistantActions` with the shared action-button class.

- [x] **Step 2: Complete assistant metadata projection**

Set live assistant `sentAt` to the turn creation timestamp and `model` to the effective requested model. Set persisted book assistant `sentAt` from the existing conversation creation time.

- [x] **Step 3: Verify book and screenplay rendered output**

Confirm configured nicknames win over API model names, while unknown historical model names remain readable.

### Task 3: Unified composer surface and final verification

**Files:**
- Modify: `src/components/AgentConversation/Panel.scss`
- Modify: `src/components/AgentConversation/Composer/index.scss`
- Modify: `src/components/AgentConversation/ConversationViewport/index.scss`

**Interfaces:**
- `.agent-conversation-panel__main` owns `var(--bg-surface)` and a 16px vertical gap.
- Conversation and composer children do not establish competing opaque base colors.

- [x] **Step 1: Record the failing live-browser assertions**

Expected before the fix: conversation/composer gap is 0px, colors differ, assistant actions are split across two regions, and assistant metadata is absent.

- [x] **Step 2: Apply the minimum shared surface styles**

Give the main column one background and 16px gap; retain only the conversation top gradient and make the composer background transparent.

- [x] **Step 3: Verify interaction and layout in both products**

Check book and screenplay pages at desktop and 700px width: user messages/footer right, assistant messages/footer left, metadata visible, actions hidden before hover and grouped after hover, no horizontal overflow.

- [x] **Step 4: Run complete verification**

```bash
npm run typecheck
npm run check:purr-components
npm run test:unit
git diff --check
```

Expected: all commands exit 0.
