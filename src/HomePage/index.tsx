import React from 'react'
import { ArrowRightOutlined, BookOutlined, SettingOutlined } from '../ui'
import { useTheme } from '../contexts/ThemeContext'
import homeLogoLight from '../imgs/home_logo_light.png'
import homeLogoDark from '../imgs/home_logo_dark.png'
import './index.scss'

interface HomePageProps {
  onEnterBookshelf: () => void
  onOpenSettings: () => void
}

export default function HomePage({ onEnterBookshelf, onOpenSettings }: HomePageProps) {
  const { theme } = useTheme()
  const homeLogo = theme === 'dark' ? homeLogoDark : homeLogoLight

  return (
    <div className="home-page">
      <div className="home-backdrop" aria-hidden>
        <span />
        <span />
      </div>
      <div className="home-content">
        <div className="home-hero">
          <img src={homeLogo} alt="PurrTypos" className="home-app-logo" />
          <span className="home-kicker">AI · 长篇创作工作台</span>
          <p className="home-subtitle">
            让灵感、大纲与正文自然流动，专注写好你的下一个故事。
          </p>
        </div>
        <div className="home-actions">
          <button className="home-entry-btn" onClick={onEnterBookshelf}>
            <span className="home-entry-icon"><BookOutlined /></span>
            <span className="home-entry-copy">
              <strong>进入书架</strong>
              <small>继续作品，或开启一段新故事</small>
            </span>
            <ArrowRightOutlined className="home-entry-arrow" />
          </button>
          <button className="home-entry-btn" onClick={onOpenSettings}>
            <span className="home-entry-icon"><SettingOutlined /></span>
            <span className="home-entry-copy">
              <strong>偏好设置</strong>
              <small>配置模型、外观与创作习惯</small>
            </span>
            <ArrowRightOutlined className="home-entry-arrow" />
          </button>
        </div>
      </div>
    </div>
  )
}
