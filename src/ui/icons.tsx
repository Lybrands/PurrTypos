import React from 'react'
import {
  AimOutlined as AntAimOutlined,
  AlignLeftOutlined as AntAlignLeftOutlined,
  ArrowLeftOutlined as AntArrowLeftOutlined,
  ArrowRightOutlined as AntArrowRightOutlined,
  ArrowUpOutlined as AntArrowUpOutlined,
  BookOutlined as AntBookOutlined,
  BorderlessTableOutlined as AntBorderlessTableOutlined,
  BulbOutlined as AntBulbOutlined,
  CheckCircleOutlined as AntCheckCircleOutlined,
  CheckOutlined as AntCheckOutlined,
  CheckSquareOutlined as AntCheckSquareOutlined,
  ClockCircleOutlined as AntClockCircleOutlined,
  CloseCircleOutlined as AntCloseCircleOutlined,
  CloseOutlined as AntCloseOutlined,
  CommentOutlined as AntCommentOutlined,
  CompassOutlined as AntCompassOutlined,
  CopyOutlined as AntCopyOutlined,
  DashboardOutlined as AntDashboardOutlined,
  DeleteOutlined as AntDeleteOutlined,
  DoubleLeftOutlined as AntDoubleLeftOutlined,
  DoubleRightOutlined as AntDoubleRightOutlined,
  DownOutlined as AntDownOutlined,
  EditOutlined as AntEditOutlined,
  EllipsisOutlined as AntEllipsisOutlined,
  ExclamationCircleOutlined as AntExclamationCircleOutlined,
  ExportOutlined as AntExportOutlined,
  EyeOutlined as AntEyeOutlined,
  FileAddOutlined as AntFileAddOutlined,
  FileSearchOutlined as AntFileSearchOutlined,
  FileTextOutlined as AntFileTextOutlined,
  FireOutlined as AntFireOutlined,
  FontSizeOutlined as AntFontSizeOutlined,
  FullscreenExitOutlined as AntFullscreenExitOutlined,
  FullscreenOutlined as AntFullscreenOutlined,
  GlobalOutlined as AntGlobalOutlined,
  HighlightOutlined as AntHighlightOutlined,
  HistoryOutlined as AntHistoryOutlined,
  HomeOutlined as AntHomeOutlined,
  ImportOutlined as AntImportOutlined,
  InboxOutlined as AntInboxOutlined,
  LinkOutlined as AntLinkOutlined,
  LoadingOutlined as AntLoadingOutlined,
  MenuFoldOutlined as AntMenuFoldOutlined,
  MessageOutlined as AntMessageOutlined,
  MoonOutlined as AntMoonOutlined,
  OrderedListOutlined as AntOrderedListOutlined,
  PaperClipOutlined as AntPaperClipOutlined,
  PauseCircleOutlined as AntPauseCircleOutlined,
  PlusOutlined as AntPlusOutlined,
  ProfileOutlined as AntProfileOutlined,
  PushpinFilled as AntPushpinFilled,
  PushpinOutlined as AntPushpinOutlined,
  ReadOutlined as AntReadOutlined,
  RedoOutlined as AntRedoOutlined,
  ReloadOutlined as AntReloadOutlined,
  RightOutlined as AntRightOutlined,
  RobotOutlined as AntRobotOutlined,
  RollbackOutlined as AntRollbackOutlined,
  SaveOutlined as AntSaveOutlined,
  SearchOutlined as AntSearchOutlined,
  SettingOutlined as AntSettingOutlined,
  StarOutlined as AntStarOutlined,
  SunOutlined as AntSunOutlined,
  TeamOutlined as AntTeamOutlined,
  ThunderboltOutlined as AntThunderboltOutlined,
  UndoOutlined as AntUndoOutlined,
  UnorderedListOutlined as AntUnorderedListOutlined,
  UpOutlined as AntUpOutlined,
  UserOutlined as AntUserOutlined,
  VerticalAlignBottomOutlined as AntVerticalAlignBottomOutlined,
  VideoCameraOutlined as AntVideoCameraOutlined,
} from '@ant-design/icons'

type AntIconComponent = typeof AntAimOutlined

export interface PurrIconProps extends React.ComponentPropsWithoutRef<AntIconComponent> {
  spin?: boolean
}

export interface PanelToggleIconProps extends React.HTMLAttributes<HTMLSpanElement> {
  /** 面板停靠在哪一侧。 */
  side: 'left' | 'right'
  /** 本次点击会执行的动作。 */
  action: 'collapse' | 'expand'
}

/**
 * 图标供应方适配层。
 *
 * 业务代码只从 `src/ui` 导入图标；这里统一注入组件库类名和动画，
 * 避免业务样式依赖 @ant-design/icons 的内部 DOM 类名。
 */
function createPurrIcon(
  Icon: AntIconComponent,
  displayName: string,
) {
  const Component = React.forwardRef<HTMLSpanElement, PurrIconProps>(
    ({ spin, className, ...props }, ref) => (
      <Icon
        {...props}
        ref={ref}
        className={['purr-icon', spin && 'purr-icon--spin', className].filter(Boolean).join(' ')}
        aria-hidden={props['aria-label'] ? undefined : true}
      />
    ),
  )
  Component.displayName = displayName
  return Component
}

export const AimOutlined = createPurrIcon(AntAimOutlined, 'AimOutlined')
export const AlignLeftOutlined = createPurrIcon(AntAlignLeftOutlined, 'AlignLeftOutlined')
export const ArrowLeftOutlined = createPurrIcon(AntArrowLeftOutlined, 'ArrowLeftOutlined')
export const ArrowRightOutlined = createPurrIcon(AntArrowRightOutlined, 'ArrowRightOutlined')
export const ArrowUpOutlined = createPurrIcon(AntArrowUpOutlined, 'ArrowUpOutlined')
export const BookOutlined = createPurrIcon(AntBookOutlined, 'BookOutlined')
export const BorderlessTableOutlined = createPurrIcon(AntBorderlessTableOutlined, 'BorderlessTableOutlined')
export const BulbOutlined = createPurrIcon(AntBulbOutlined, 'BulbOutlined')
export const CheckCircleOutlined = createPurrIcon(AntCheckCircleOutlined, 'CheckCircleOutlined')
export const CheckOutlined = createPurrIcon(AntCheckOutlined, 'CheckOutlined')
export const CheckSquareOutlined = createPurrIcon(AntCheckSquareOutlined, 'CheckSquareOutlined')
export const ClockCircleOutlined = createPurrIcon(AntClockCircleOutlined, 'ClockCircleOutlined')
export const CloseCircleOutlined = createPurrIcon(AntCloseCircleOutlined, 'CloseCircleOutlined')
export const CloseOutlined = createPurrIcon(AntCloseOutlined, 'CloseOutlined')
export const CommentOutlined = createPurrIcon(AntCommentOutlined, 'CommentOutlined')
export const CompassOutlined = createPurrIcon(AntCompassOutlined, 'CompassOutlined')
export const CopyOutlined = createPurrIcon(AntCopyOutlined, 'CopyOutlined')
export const DashboardOutlined = createPurrIcon(AntDashboardOutlined, 'DashboardOutlined')
export const DeleteOutlined = createPurrIcon(AntDeleteOutlined, 'DeleteOutlined')
export const DoubleLeftOutlined = createPurrIcon(AntDoubleLeftOutlined, 'DoubleLeftOutlined')
export const DoubleRightOutlined = createPurrIcon(AntDoubleRightOutlined, 'DoubleRightOutlined')
export const DownOutlined = createPurrIcon(AntDownOutlined, 'DownOutlined')
export const EditOutlined = createPurrIcon(AntEditOutlined, 'EditOutlined')
export const EllipsisOutlined = createPurrIcon(AntEllipsisOutlined, 'EllipsisOutlined')
export const ExclamationCircleOutlined = createPurrIcon(AntExclamationCircleOutlined, 'ExclamationCircleOutlined')
export const ExportOutlined = createPurrIcon(AntExportOutlined, 'ExportOutlined')
export const EyeOutlined = createPurrIcon(AntEyeOutlined, 'EyeOutlined')
export const FileAddOutlined = createPurrIcon(AntFileAddOutlined, 'FileAddOutlined')
export const FileSearchOutlined = createPurrIcon(AntFileSearchOutlined, 'FileSearchOutlined')
export const FileTextOutlined = createPurrIcon(AntFileTextOutlined, 'FileTextOutlined')
export const FireOutlined = createPurrIcon(AntFireOutlined, 'FireOutlined')
export const FontSizeOutlined = createPurrIcon(AntFontSizeOutlined, 'FontSizeOutlined')
export const FullscreenExitOutlined = createPurrIcon(AntFullscreenExitOutlined, 'FullscreenExitOutlined')
export const FullscreenOutlined = createPurrIcon(AntFullscreenOutlined, 'FullscreenOutlined')
export const GlobalOutlined = createPurrIcon(AntGlobalOutlined, 'GlobalOutlined')
export const HighlightOutlined = createPurrIcon(AntHighlightOutlined, 'HighlightOutlined')
export const HistoryOutlined = createPurrIcon(AntHistoryOutlined, 'HistoryOutlined')
export const HomeOutlined = createPurrIcon(AntHomeOutlined, 'HomeOutlined')
export const ImportOutlined = createPurrIcon(AntImportOutlined, 'ImportOutlined')
export const InboxOutlined = createPurrIcon(AntInboxOutlined, 'InboxOutlined')
export const LinkOutlined = createPurrIcon(AntLinkOutlined, 'LinkOutlined')
export const LoadingOutlined = createPurrIcon(AntLoadingOutlined, 'LoadingOutlined')
export const MenuFoldOutlined = createPurrIcon(AntMenuFoldOutlined, 'MenuFoldOutlined')
export const MessageOutlined = createPurrIcon(AntMessageOutlined, 'MessageOutlined')
export const MoonOutlined = createPurrIcon(AntMoonOutlined, 'MoonOutlined')
export const OrderedListOutlined = createPurrIcon(AntOrderedListOutlined, 'OrderedListOutlined')
export const PaperClipOutlined = createPurrIcon(AntPaperClipOutlined, 'PaperClipOutlined')
export const PauseCircleOutlined = createPurrIcon(AntPauseCircleOutlined, 'PauseCircleOutlined')
export const PlusOutlined = createPurrIcon(AntPlusOutlined, 'PlusOutlined')
export const ProfileOutlined = createPurrIcon(AntProfileOutlined, 'ProfileOutlined')
export const PushpinFilled = createPurrIcon(AntPushpinFilled, 'PushpinFilled')
export const PushpinOutlined = createPurrIcon(AntPushpinOutlined, 'PushpinOutlined')
export const ReadOutlined = createPurrIcon(AntReadOutlined, 'ReadOutlined')
export const RedoOutlined = createPurrIcon(AntRedoOutlined, 'RedoOutlined')
export const ReloadOutlined = createPurrIcon(AntReloadOutlined, 'ReloadOutlined')
export const RightOutlined = createPurrIcon(AntRightOutlined, 'RightOutlined')
export const RobotOutlined = createPurrIcon(AntRobotOutlined, 'RobotOutlined')
export const RollbackOutlined = createPurrIcon(AntRollbackOutlined, 'RollbackOutlined')
export const SaveOutlined = createPurrIcon(AntSaveOutlined, 'SaveOutlined')
export const SearchOutlined = createPurrIcon(AntSearchOutlined, 'SearchOutlined')
export const SettingOutlined = createPurrIcon(AntSettingOutlined, 'SettingOutlined')
export const StarOutlined = createPurrIcon(AntStarOutlined, 'StarOutlined')
export const SunOutlined = createPurrIcon(AntSunOutlined, 'SunOutlined')
export const TeamOutlined = createPurrIcon(AntTeamOutlined, 'TeamOutlined')
export const ThunderboltOutlined = createPurrIcon(AntThunderboltOutlined, 'ThunderboltOutlined')
export const UndoOutlined = createPurrIcon(AntUndoOutlined, 'UndoOutlined')
export const UnorderedListOutlined = createPurrIcon(AntUnorderedListOutlined, 'UnorderedListOutlined')
export const UpOutlined = createPurrIcon(AntUpOutlined, 'UpOutlined')
export const UserOutlined = createPurrIcon(AntUserOutlined, 'UserOutlined')
export const VerticalAlignBottomOutlined = createPurrIcon(AntVerticalAlignBottomOutlined, 'VerticalAlignBottomOutlined')
export const VideoCameraOutlined = createPurrIcon(AntVideoCameraOutlined, 'VideoCameraOutlined')

/**
 * 面板收起 / 展开图标。
 *
 * 比单独的双箭头多表达两层信息：矩形代表面板，竖线代表停靠侧，
 * 箭头则只表达点击后的移动方向。用于工作台边栏，避免和“上一页”
 * 或“快退”一类导航操作混淆。
 */
export const PanelToggleIcon = React.forwardRef<HTMLSpanElement, PanelToggleIconProps>(
  ({ side, action, className, ...props }, ref) => {
    const direction =
      action === 'collapse'
        ? side
        : side === 'left' ? 'right' : 'left'
    const dividerX = side === 'left' ? 6.25 : 13.75
    const arrowPath = direction === 'left'
      ? 'M12.25 7.5 9.75 10l2.5 2.5'
      : 'M7.75 7.5 10.25 10l-2.5 2.5'
    const railPath = side === 'left'
      ? 'M4.25 3.25h2v13.5h-2a2 2 0 0 1-2-2v-9.5a2 2 0 0 1 2-2Z'
      : 'M13.75 3.25h2a2 2 0 0 1 2 2v9.5a2 2 0 0 1-2 2h-2V3.25Z'

    return (
      <span
        {...props}
        ref={ref}
        className={['purr-icon', 'purr-panel-toggle-icon', className].filter(Boolean).join(' ')}
        data-direction={direction}
        aria-hidden="true"
      >
        <svg viewBox="0 0 20 20" fill="none">
          <rect x="2.25" y="3.25" width="15.5" height="13.5" rx="2" stroke="currentColor" strokeWidth="1.35" />
          <path d={railPath} fill="currentColor" opacity="0.12" />
          <path d={`M${dividerX} 3.75v12.5`} stroke="currentColor" strokeWidth="1.2" opacity="0.72" />
          <path
            className="purr-panel-toggle-icon__arrow"
            d={arrowPath}
            stroke="currentColor"
            strokeWidth="1.45"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </span>
    )
  },
)
PanelToggleIcon.displayName = 'PanelToggleIcon'
