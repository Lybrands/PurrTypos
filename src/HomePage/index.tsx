import React from 'react'
import { BookOutlined, SettingOutlined } from '@ant-design/icons'
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
      <div className="home-content">
        <div className="home-hero">
          <img src={homeLogo} alt="PurrTypos" className="home-app-logo" />
        </div>
        <div className="home-actions">
          <button className="home-entry-btn" onClick={onEnterBookshelf}>
            <BookOutlined className="home-entry-icon" />
            <span className="home-entry-label">书架</span>
          </button>
          <button className="home-entry-btn" onClick={onOpenSettings}>
            <SettingOutlined className="home-entry-icon" />
            <span className="home-entry-label">设置</span>
          </button>
        </div>
      </div>
    </div>
  )
}
