import { createPurrIcon } from './PurrIcon'

/**
 * 图钉（pushpin）：置顶 / 固定动作。
 *
 * 经典工字形图钉轮廓（Material push_pin 形态）：圆头、收腰、
 * 外扩平底缘，针杆从底缘垂下；整体轻微倾斜 15° 传达「钉入」感。
 * PushpinIcon 为可执行的动作形态，PinnedIcon 整体实心，
 * 表示已固定/已置顶的状态。
 */

const pushpinBody = <>
  {/* 头 + 收腰 + 底缘（一体轮廓） */}
  <path d="M9.75 5.75a2.25 2.25 0 0 1 4.5 0v2.5l1.9 1.9a1 1 0 0 1-.7 1.7H8.55a1 1 0 0 1-.7-1.7l1.9-1.9Z" />
  {/* 针杆 */}
  <path d="M12 11.85v7.4" />
</>

export const PushpinIcon = createPurrIcon('PushpinIcon', <>
  <g transform="rotate(15 12 12)">
    {pushpinBody}
  </g>
</>)

export const PinnedIcon = createPurrIcon('PinnedIcon', <>
  <g transform="rotate(15 12 12)">
    <path
      d="M9.75 5.75a2.25 2.25 0 0 1 4.5 0v2.5l1.9 1.9a1 1 0 0 1-.7 1.7H8.55a1 1 0 0 1-.7-1.7l1.9-1.9Z"
      fill="currentColor"
      stroke="none"
    />
    <path d="M12 11.85v7.4" />
  </g>
</>)
