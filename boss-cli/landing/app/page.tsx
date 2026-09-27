import Navbar from '@/components/Navbar'
import Hero from '@/components/Hero'
import Features from '@/components/Features'
import Demo from '@/components/Demo'
import FAQ from '@/components/FAQ'
import CTA from '@/components/CTA'
import Footer from '@/components/Footer'
import StructuredData from '@/components/StructuredData'
import { SITE_URL } from '@/lib/site'

const homepageJsonLd = {
  '@context': 'https://schema.org',
  '@graph': [
    {
      '@type': 'WebSite',
      '@id': `${SITE_URL}/#website`,
      url: SITE_URL,
      name: 'boss-cli',
      description: '面向 HR 和招聘团队的开源 Boss直聘自动化工具。',
      inLanguage: 'zh-CN',
    },
    {
      '@type': 'SoftwareApplication',
      '@id': `${SITE_URL}/#software`,
      name: 'boss-cli',
      url: SITE_URL,
      description:
        '基于 CDP 的 Boss直聘自动化命令行工具，支持候选人管理、批量消息、简历筛选与 AI Agent 集成。',
      applicationCategory: 'BusinessApplication',
      applicationSubCategory: 'RecruitingApplication',
      operatingSystem: ['macOS', 'Windows', 'Linux'],
      downloadUrl: 'https://www.npmjs.com/package/@joohw/boss-cli',
      codeRepository: 'https://github.com/joohw/boss-cli',
      license: 'https://www.gnu.org/licenses/gpl-3.0.html',
      offers: {
        '@type': 'Offer',
        price: 0,
        priceCurrency: 'USD',
      },
    },
  ],
}

export default function Home() {
  return (
    <div className="bg-slate-950 min-h-screen">
      <StructuredData data={homepageJsonLd} />
      <Navbar />
      <main>
        <Hero />
        <Features />
        <Demo />
        <FAQ />
        <CTA />
      </main>
      <Footer />
    </div>
  )
}
