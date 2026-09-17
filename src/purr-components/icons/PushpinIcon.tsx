import { createPurrIcon } from './PurrIcon'

/**
 * 图钉（pushpin）：置顶 / 固定动作。
 *
 * 结构：圆顶钉头 + 底缘翼线 + 针杆；PushpinIcon 为可执行的动作形态，
 * PinnedIcon 头部实心，表示已固定/已置顶的状态。
 */

const pushpinBase = <>
  <path d="M6.75 9.75h10.5" />
  <path d="M12 9.75v10.5" />
</>

export const PushpinIcon = createPurrIcon('PushpinIcon', <>
  <path d="M7.25 9.75V7.75a4.75 4.75 0 0 1 9.5 0v2" />
  {pushpinBase}
</>)

export const PinnedIcon = createPurrIcon('PinnedIcon', <>
  <path
    d="M7.25 9.75V7.75a4.75 4.75 0 0 1 9.5 0v2Z"
    fill="currentColor"
    stroke="none"
  />
  {pushpinBase}
</>)
