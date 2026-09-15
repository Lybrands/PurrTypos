import React from "react";
import {
  ChevronRightIcon,
  PurrButton,
  PurrDrawer,
  PurrPopover,
  RobotIcon,
  type PurrPopoverProps,
} from "@/purr-components";
import { services } from "../../../services";
import type { AiAgentDelegation, AiSubAgentConversation } from "../../../types";
import {
  loadCompleteAgentRunSnapshot,
  replayAgentRunSnapshotAsync,
} from "../../../agent-runtime/runSnapshotHydration";
import type { AiSubAgentActivity } from "../../../agent-runtime/contracts";
import ViewportBlock from "../../ViewportBlock";
import ConversationViewport from "../ConversationViewport";
import {
  buildSubAgentConversationMessages,
  collapseSubAgentDelegations,
} from "./presentation";

const STATUS_LABELS: Record<AiAgentDelegation["status"], string> = {
  queued: "等待中",
  claimed: "已认领",
  running: "运行中",
  done: "已完成",
  failed: "失败",
  canceled: "已取消",
};

function isActiveStatus(status: AiAgentDelegation["status"]): boolean {
  return status === "queued" || status === "claimed" || status === "running";
}

const rejectReadOnlyApproval = async () => ({
  success: false,
  error: "只读对话不能处理审批",
});

interface DelegationStatusProps {
  items: AiAgentDelegation[];
  activities?: AiSubAgentActivity[];
  variant?: "timeline" | "overview";
  placement?: PurrPopoverProps["placement"];
}

export default function DelegationStatus({
  items,
  activities = [],
  variant = "timeline",
  placement = "topLeft",
}: DelegationStatusProps) {
  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  const [overviewOpen, setOverviewOpen] = React.useState(false);
  const [loadedByRunId, setLoadedByRunId] = React.useState<
    ReadonlyMap<string, AiSubAgentActivity>
  >(new Map());
  const [conversation, setConversation] = React.useState<AiSubAgentConversation>();
  const [loading, setLoading] = React.useState(false);
  const [loadError, setLoadError] = React.useState("");
  const byId = React.useMemo(
    () => new Map(activities.map((activity) => [activity.delegationId, activity])),
    [activities],
  );
  const liveByRunId = React.useMemo(
    () => new Map(activities.map((activity) => [activity.runId, activity])),
    [activities],
  );
  const collapsedItems = React.useMemo(
    () => collapseSubAgentDelegations(items),
    [items],
  );
  const selected = collapsedItems.find((item) => item.delegationId === selectedId);

  const openAgent = React.useCallback((delegationId: string) => {
    setOverviewOpen(false);
    setSelectedId(delegationId);
  }, []);

  React.useEffect(() => {
    if (!selected) {
      setLoadedByRunId(new Map());
      setConversation(undefined);
      setLoadError("");
      return;
    }
    let current = true;
    setLoading(true);
    setLoadError("");
    setConversation(undefined);
    void services.ai.getSubAgentConversation({ runId: selected.runId })
    .then(async (conversationResult) => {
      if (!conversationResult.success || !conversationResult.data) {
        throw new Error(conversationResult.error || "无法读取子 Agent 对话");
      }
      const loaded = await Promise.all(conversationResult.data.turns.map(async (turn) => {
        const snapshot = await loadCompleteAgentRunSnapshot(turn.runId, {
          getRunSnapshot: (input) => services.ai.getAgentRunSnapshot(input),
          isCurrent: () => current,
        }).catch(() => undefined);
        if (!snapshot || !current) return null;
        const message = await replayAgentRunSnapshotAsync({
          snapshot,
          prompt: turn.prompt,
          turnId: `sub-agent:${turn.runId}`,
        }, () => current);
        return [turn.runId, {
          delegationId: selected.delegationId,
          runId: turn.runId,
          agentName: selected.agentName,
          agentTitle: selected.agentTitle,
          objective: turn.prompt,
          status: turn.status,
          message,
        }] as const;
      }));
      if (!current) return;
      setConversation(conversationResult.data);
      const nextLoaded = new Map<string, AiSubAgentActivity>();
      for (const item of loaded) {
        if (item) nextLoaded.set(item[0], item[1]);
      }
      setLoadedByRunId(nextLoaded);
    }).catch((error) => {
      if (current) setLoadError(
        error instanceof Error ? error.message : "无法读取子 Agent 输出",
      );
    }).finally(() => {
      if (current) setLoading(false);
    });
    return () => { current = false; };
  }, [selected?.delegationId, selected?.runId, selected?.status]);

  const rows = (
    <div className="work-log__subagent-list">
      {collapsedItems.map((item) => {
        const row = <SubAgentRow item={item} onOpen={openAgent} />;
        return variant === "timeline" ? (
          <ViewportBlock key={item.delegationId} id={`delegation:${item.delegationId}`}>
            {row}
          </ViewportBlock>
        ) : React.cloneElement(row, { key: item.delegationId });
      })}
    </div>
  );

  return (
    <div className={variant === "overview" ? "subagent-overview" : "work-log__subagents"}>
      {variant === "overview" ? (
        <PurrPopover
          trigger="click"
          placement={placement}
          open={overviewOpen}
          onOpenChange={setOverviewOpen}
          content={(
            <div className="subagent-overview__popover">
              <div className="subagent-overview__title">当前子 Agent</div>
              {rows}
            </div>
          )}
        >
          <PurrButton
            type="text"
            size="small"
            className="subagent-overview__trigger"
            icon={<RobotIcon size={14} />}
          >
            子 Agent {collapsedItems.length}
          </PurrButton>
        </PurrPopover>
      ) : rows}
      <PurrDrawer
        open={Boolean(selected)}
        title={selected ? selected.agentTitle || selected.agentName : "子 Agent"}
        onClose={() => setSelectedId(null)}
        width={560}
        destroyOnHidden
        className="subagent-drawer"
      >
        {selected ? (
          <SubAgentDetails
            item={selected}
            messagesByRunId={new Map([
              ...[...loadedByRunId].map(([runId, activity]) => [runId, activity.message] as const),
              ...[...liveByRunId].map(([runId, activity]) => [runId, activity.message] as const),
              ...(byId.get(selected.delegationId)?.runId === selected.runId
                ? [[selected.runId, byId.get(selected.delegationId)!.message] as const]
                : []),
            ])}
            loading={loading}
            loadError={loadError}
            conversation={conversation?.selectedRunId === selected.runId ? conversation : undefined}
          />
        ) : null}
      </PurrDrawer>
    </div>
  );
}

export function SubAgentOverview(props: Omit<DelegationStatusProps, "variant">) {
  return <DelegationStatus {...props} variant="overview" />;
}

function SubAgentRow({ item, onOpen }: {
  item: AiAgentDelegation;
  onOpen(delegationId: string): void;
}) {
  return (
    <div className={`work-log__subagent work-log__subagent--${item.status}`}>
      <span className="work-log__subagent-kind">子 Agent ·</span>
      <span className="work-log__subagent-role">
        {item.agentTitle || item.agentName}
      </span>
      <span className="work-log__subagent-status" role="status">
        {STATUS_LABELS[item.status]}
      </span>
      <button
        type="button"
        className="work-log__subagent-view"
        onClick={() => onOpen(item.delegationId)}
        aria-label={`查看${item.agentTitle || item.agentName}的输出`}
      >
        <span>查看</span>
        <ChevronRightIcon size={12} aria-hidden="true" />
      </button>
    </div>
  );
}

function SubAgentDetails({ item, messagesByRunId, loading, loadError, conversation }: {
  item: AiAgentDelegation;
  messagesByRunId: ReadonlyMap<string, AiSubAgentActivity["message"]>;
  loading: boolean;
  loadError: string;
  conversation?: AiSubAgentConversation;
}) {
  const active = isActiveStatus(item.status);
  const messages = buildSubAgentConversationMessages({
    turns: conversation?.turns ?? [{
      runId: item.runId,
      prompt: item.objective,
      finalResponse: item.resultSummary || "",
      status: item.status,
    }],
    messagesByRunId,
    error: loadError || item.error,
  });

  return (
    <div className="subagent-drawer__conversation">
      <ConversationViewport
        sessionIdentity={`sub-agent:${item.runId}`}
        messages={messages}
        loading={loading || active}
        emptyTitle="子 Agent 对话"
        emptyDescription="该子 Agent 尚未收到任务。"
        onResolveToolApproval={rejectReadOnlyApproval}
      />
    </div>
  );
}
