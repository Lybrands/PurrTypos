import React from "react";
import { Modal, Radio, Checkbox, Button } from "antd";

export interface ExportItem {
  id: string;
  title: string;
}

export interface ExportGroup {
  id: string;
  title: string;
  children: ExportItem[];
}

export interface ExportModalProps {
  open: boolean;
  onCancel: () => void;
  title?: string;
  /** 扁平列表（书籍或章节） */
  items?: ExportItem[];
  /** 分组列表（如卷 -> 章节），与 items 二选一 */
  groups?: ExportGroup[];
  selectedIds: string[];
  onSelectedIdsChange: (ids: string[]) => void;
  onConfirm: (
    selectedIds: string[],
    format: "md" | "txt",
    exportAsZip: boolean,
  ) => void | Promise<void>;
  confirmLoading?: boolean;
  /** 列表标题，如「选择书籍（可多选）」 */
  selectLabel?: string;
  emptyText?: string;
  /** 是否显示「导出到文件夹」的详细说明（书架用） */
  showFolderHint?: boolean;
}

export default function ExportModal({
  open,
  onCancel,
  title = "导出",
  items = [],
  groups = [],
  selectedIds,
  onSelectedIdsChange,
  onConfirm,
  confirmLoading = false,
  selectLabel = "选择（可多选）",
  emptyText = "暂无数据",
  showFolderHint = false,
}: ExportModalProps) {
  const [exportFormat, setExportFormat] = React.useState<"md" | "txt">("md");
  const [exportAsZip, setExportAsZip] = React.useState(false);

  const toggleItem = React.useCallback(
    (id: string) => {
      const set = new Set(selectedIds);
      if (set.has(id)) set.delete(id);
      else set.add(id);
      onSelectedIdsChange(Array.from(set));
    },
    [selectedIds, onSelectedIdsChange],
  );

  const toggleGroup = React.useCallback(
    (group: ExportGroup) => {
      const childIds = group.children.map((c) => c.id);
      const allSelected =
        childIds.length > 0 && childIds.every((id) => selectedIds.includes(id));
      const set = new Set(selectedIds);
      if (allSelected) childIds.forEach((id) => set.delete(id));
      else childIds.forEach((id) => set.add(id));
      onSelectedIdsChange(Array.from(set));
    },
    [selectedIds, onSelectedIdsChange],
  );

  const handleSelectAll = React.useCallback(() => {
    if (groups.length > 0) {
      const allIds = groups.flatMap((g) => g.children.map((c) => c.id));
      onSelectedIdsChange(allIds);
    } else {
      onSelectedIdsChange(items.map((i) => i.id));
    }
  }, [groups, items, onSelectedIdsChange]);

  const handleOk = React.useCallback(async () => {
    await onConfirm(selectedIds, exportFormat, exportAsZip);
  }, [selectedIds, exportFormat, exportAsZip, onConfirm]);

  const listMaxHeight = groups.length > 0 ? 320 : 280;

  return (
    <Modal
      title={title}
      open={open}
      onOk={handleOk}
      onCancel={onCancel}
      okText="导出"
      cancelText="取消"
      confirmLoading={confirmLoading}
      width={480}
    >
      <div style={{ marginBottom: 16 }}>
        <span style={{ marginRight: 8 }}>导出方式：</span>
        <Radio.Group
          value={exportAsZip}
          onChange={(e) => setExportAsZip(e.target.value)}
        >
          <Radio value={false}>导出到文件夹</Radio>
          <Radio value={true}>
            导出为压缩包{showFolderHint ? "" : " (.zip)"}
          </Radio>
        </Radio.Group>
      </div>
      <div style={{ marginBottom: 16 }}>
        <span style={{ marginRight: 8 }}>导出格式：</span>
        <Radio.Group
          value={exportFormat}
          onChange={(e) => setExportFormat(e.target.value)}
        >
          <Radio value="md">Markdown (.md)</Radio>
          <Radio value="txt">纯文本 (.txt)</Radio>
        </Radio.Group>
      </div>
      <div>
        <div style={{ marginBottom: 8 }}>
          <span>{selectLabel}</span>
          <Button type="link" size="small" onClick={handleSelectAll}>
            全选
          </Button>
        </div>
        <div
          style={{
            maxHeight: listMaxHeight,
            overflowY: "auto",
            border: "1px solid var(--border-light)",
            borderRadius: 6,
            padding: 8,
          }}
        >
          {groups.length > 0 ? (
            groups.map((group) => {
              const volSelected =
                group.children.length > 0 &&
                group.children.every((c) => selectedIds.includes(c.id));
              const volIndeterminate =
                group.children.some((c) => selectedIds.includes(c.id)) &&
                !volSelected;
              return (
                <div key={group.id} style={{ marginBottom: 8 }}>
                  <Checkbox
                    checked={volSelected}
                    indeterminate={volIndeterminate}
                    onChange={() => toggleGroup(group)}
                  >
                    <strong>{group.title}</strong>
                  </Checkbox>
                  <div style={{ marginLeft: 20, marginTop: 4 }}>
                    {group.children.map((ch) => (
                      <div key={ch.id} style={{ marginBottom: 2 }}>
                        <Checkbox
                          checked={selectedIds.includes(ch.id)}
                          onChange={() => toggleItem(ch.id)}
                        >
                          {ch.title}
                        </Checkbox>
                      </div>
                    ))}
                  </div>
                </div>
              );
            })
          ) : items.length === 0 ? (
            <span style={{ color: "var(--text-secondary)" }}>{emptyText}</span>
          ) : (
            items.map((item) => (
              <div key={item.id} style={{ marginBottom: 4 }}>
                <Checkbox
                  checked={selectedIds.includes(item.id)}
                  onChange={() => toggleItem(item.id)}
                >
                  {item.title}
                </Checkbox>
              </div>
            ))
          )}
        </div>
      </div>
    </Modal>
  );
}
