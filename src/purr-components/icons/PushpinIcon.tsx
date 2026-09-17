import { createPurrIcon } from './PurrIcon'

/**
 * 图钉（pushpin）：置顶 / 固定动作。
 *
 * 结构：大圆顶钉头 + 底缘翼线 + 短针杆，整体 45° 斜置（针尖指向左下），
 * 与主流产品的「置顶」形态一致。头部相对针杆放大，保证 16px 下仍可辨认；
 * PushpinIcon 为可执行的动作形态，PinnedIcon 头部实心，表示已固定/已置顶。
 */

const pushpinBase = (
  <g transform="rotate(45 12 12)">
    <path d="M6.25 10.5h11.5" />
    <path d="M12 10.5v8.5" />
  </g>
)

export const PushpinIcon = createPurrIcon('PushpinIcon', <>
  <g transform="rotate(45 12 12)">
    <path d="M6.75 10.5V8.25a5.25 5.25 0 0 1 10.5 0v2.25" />
  </g>
  {pushpinBase}
</>)

export const PinnedIcon = createPurrIcon('PinnedIcon', <>
  <g transform="rotate(45 12 12)">
    <path
      d="M6.75 10.5V8.25a5.25 5.25 0 0 1 10.5 0v2.25Z"
      fill="currentColor"
      stroke="none"
    />
  </g>
  {pushpinBase}
</>)
