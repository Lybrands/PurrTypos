import {
  ArrowRightIcon,
  BookIcon,
  SettingsIcon,
  VideoCameraIcon,
} from '@/purr-components'
import { useTheme } from '../contexts/ThemeContext'
import homeLogoLight from '../imgs/home_logo_light.png'
import homeLogoDark from '../imgs/home_logo_dark.png'
import './index.scss'

interface HomePageProps {
  onEnterScreenplayAgent: () => void
  onEnterBookshelf: () => void
  onOpenSettings: () => void
}

export default function HomePage({
  onEnterScreenplayAgent,
  onEnterBookshelf,
  onOpenSettings,
}: HomePageProps) {
  const { theme } = useTheme()
  const homeLogo = theme === 'dark' ? homeLogoDark : homeLogoLight

  return (
    <div className="home-page">
      <div className="home-backdrop" aria-hidden>
        <span />
        <span />
      </div>
      <div className="home-content">
        <div className="home-brand">
          <span className="home-brand-glow" aria-hidden />
          <img src={homeLogo} alt="PurrTypos" className="home-app-logo" />
        </div>
        <div className="home-actions">
          <button
            className="home-entry-btn home-entry-btn--primary purr-entry-surface"
            onClick={onEnterBookshelf}
          >
            <span className="home-entry-icon"><BookIcon /></span>
            <span className="home-entry-copy">
              <span className="home-entry-label">BOOKSHELF</span>
              <strong>书架</strong>
            </span>
            <ArrowRightIcon className="home-entry-arrow" />
          </button>
          <button
            className="home-entry-btn home-entry-btn--primary purr-entry-surface"
            onClick={onEnterScreenplayAgent}
          >
            <span className="home-entry-icon"><VideoCameraIcon /></span>
            <span className="home-entry-copy">
              <span className="home-entry-label">SCREENPLAY</span>
              <strong>剧本</strong>
            </span>
            <ArrowRightIcon className="home-entry-arrow" />
          </button>
          <button
            className="home-entry-btn home-entry-btn--secondary purr-entry-surface"
            onClick={onOpenSettings}
          >
            <span className="home-entry-icon"><SettingsIcon /></span>
            <span className="home-entry-copy">
              <strong>偏好设置</strong>
            </span>
            <ArrowRightIcon className="home-entry-arrow" />
          </button>
        </div>
      </div>
    </div>
  )
}
