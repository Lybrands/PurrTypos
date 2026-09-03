import { SunIcon, MoonIcon } from '@/purr-components'
import { PurrButton } from '@/purr-components'
import { useTheme } from '../../contexts/ThemeContext'
import './index.scss'

export default function GlobalActions() {
  const { theme, toggleTheme } = useTheme()

  return (
    <div className="global-actions">
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
