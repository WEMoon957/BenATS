import type { Metadata } from 'next'
import { SITE_URL } from '@/lib/site'
import './globals.css'

const siteUrl = SITE_URL
const title = 'Boss直聘自动化工具：候选人管理与批量消息 | boss-cli'
const description =
  'boss-cli 是面向 HR 和招聘团队的开源 Boss直聘自动化工具，支持候选人管理、批量消息、简历筛选与 AI Agent 集成，减少重复操作，提升招聘效率。'

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  applicationName: 'boss-cli',
  title: {
    default: title,
    template: '%s | boss-cli',
  },
  description,
  keywords: [
    'Boss直聘',
    'Boss直聘自动化',
    'HR工具',
    '招聘自动化',
    '候选人管理',
    '简历筛选',
    '批量发送消息',
    'boss-cli',
    '招聘命令行工具',
    'AI招聘助手',
    '开源招聘工具',
    'HR效率工具',
  ],
  authors: [{ name: 'Joo', url: 'https://github.com/joohw' }],
  creator: 'Joo',
  publisher: 'boss-cli',
  robots: {
    index: true,
    follow: true,
    googleBot: { index: true, follow: true },
  },
  alternates: {
    canonical: siteUrl,
  },
  openGraph: {
    type: 'website',
    url: siteUrl,
    siteName: 'boss-cli',
    title,
    description,
    locale: 'zh_CN',
  },
  twitter: {
    card: 'summary_large_image',
    title,
    description,
    creator: '@joohw',
  },
  icons: {
    icon: '/icon.svg',
    shortcut: '/icon.svg',
  },
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  )
}
