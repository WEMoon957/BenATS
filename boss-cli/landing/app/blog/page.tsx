import type { Metadata } from 'next'
import Link from 'next/link'
import Footer from '@/components/Footer'
import Navbar from '@/components/Navbar'
import StructuredData from '@/components/StructuredData'
import { getAllBlogPosts } from '@/lib/blog'
import { SITE_URL, SOCIAL_IMAGE } from '@/lib/site'

const blogTitle = 'boss-cli 博客：Boss直聘招聘自动化实践'
const blogDescription =
  'boss-cli 博客：Boss直聘招聘自动化、候选人管理、批量消息发送与 AI Agent 集成实践。'

export const metadata: Metadata = {
  title: blogTitle,
  description: blogDescription,
  alternates: {
    canonical: `${SITE_URL}/blog`,
  },
  openGraph: {
    type: 'website',
    url: `${SITE_URL}/blog`,
    title: blogTitle,
    description: blogDescription,
    locale: 'zh_CN',
    images: [SOCIAL_IMAGE],
  },
  twitter: {
    card: 'summary_large_image',
    title: blogTitle,
    description: blogDescription,
    images: [SOCIAL_IMAGE],
  },
}

export default function BlogIndexPage() {
  const posts = getAllBlogPosts()
  const jsonLd = {
    '@context': 'https://schema.org',
    '@type': 'Blog',
    '@id': `${SITE_URL}/blog#blog`,
    url: `${SITE_URL}/blog`,
    name: 'boss-cli 博客',
    description: 'Boss直聘招聘自动化教程、HR 效率技巧与产品实践。',
    inLanguage: 'zh-CN',
    blogPost: posts.map((post) => ({
      '@type': 'BlogPosting',
      headline: post.title,
      description: post.description,
      datePublished: post.date,
      url: `${SITE_URL}/blog/${post.slug}`,
    })),
  }

  return (
    <div className="bg-slate-950 min-h-screen">
      <StructuredData data={jsonLd} />
      <Navbar />
      <main className="max-w-3xl mx-auto px-6 pt-28 pb-16">
        <h1 className="text-3xl font-bold text-white mb-3">博客</h1>
        <p className="text-slate-400 mb-10">Boss直聘招聘自动化教程、HR 效率技巧与产品实践。</p>

        {posts.length === 0 ? (
          <p className="text-slate-500">暂无文章。</p>
        ) : (
          <ul className="space-y-4">
            {posts.map((post) => (
              <li key={post.slug}>
                <Link
                  href={`/blog/${post.slug}`}
                  className="block rounded-lg border border-slate-800 p-5 transition-colors hover:border-slate-700"
                >
                  {post.date ? <time className="text-xs text-slate-500">{post.date}</time> : null}
                  <h2 className="mt-2 text-xl font-semibold text-white">{post.title}</h2>
                  {post.description ? (
                    <p className="mt-2 text-sm leading-relaxed text-slate-400">{post.description}</p>
                  ) : null}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </main>
      <Footer />
    </div>
  )
}
