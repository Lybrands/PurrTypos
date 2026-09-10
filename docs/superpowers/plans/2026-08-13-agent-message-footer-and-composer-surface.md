# Agent Message Footer and Composer Surface Design


## Goal

 Give book and screenplay conversations one consistent message footer, directional alignment, hover-only actions, and a unified conversation/composer surface.

## Architecture

 Keep message data in the existing shared `AgentConversationMessage` contract. Add one shared footer renderer that consumes persisted time/model metadata and product-injected actions; keep book-only favorite behavior at the extension boundary while rendering it in the shared action slot. Let the shared panel main area own the surface background, with the Virtuoso scroller and composer inheriting it.


## Constraints

- User messages and their footer stay right-aligned.
- Assistant replies and their footer stay left-aligned.
- Time and assistant model metadata remain visible; action buttons render visually only on message hover or keyboard focus.
- Favorite, copy, and edit actions use one footer action region; attachments contain artifacts only.
- Book and screenplay consumers inherit the fix from shared components.
- The main conversation column owns one background; the composer has no independent background.
