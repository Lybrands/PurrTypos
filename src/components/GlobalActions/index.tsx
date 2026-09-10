import { SunIcon, MoonIcon, SettingsIcon } from '@/purr-components'
import { PurrButton } from '@/purr-components'
import { useTheme } from '../../contexts/ThemeContext'
import './index.scss'

export default function GlobalActions({ onOpenSettings }: { onOpenSettings: () => void }) {
  const { theme, toggleTheme } = useTheme()

  return (
    <div className="global-actions">
      <PurrButton
        type="text"
        size="small"
        icon={<SettingsIcon style={{ fontSize: 16 }} />}
        title="设置"
        aria-label="打开设置"
        onClick={onOpenSettings}
      />
      <PurrButton
        type="text"
        size="small"
        icon={theme === 'light' ? <MoonIcon style={{ fontSize: 16 }} /> : <SunIcon style={{ fontSize: 16 }} />}
        title={theme === 'light' ? '切换到深色' : '切换到浅色'}
        onClick={toggleTheme}
      />
    </div>
  )
}
