import { Empty } from "antd";

interface ChatEmptyStateProps {
  hasBook: boolean;
  hasSessions: boolean;
}

export default function ChatEmptyState({
  hasBook,
  hasSessions,
}: ChatEmptyStateProps) {
  const description = !hasBook
    ? "请先选择书籍"
    : hasSessions
      ? "开始与 AI 对话"
      : "开始与 AI 对话，请从上方 + 新建对话";

  return <Empty image={false} description={description} className="chat-empty" />;
}
