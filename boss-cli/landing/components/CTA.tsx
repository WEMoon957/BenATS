'use client'

import { useState } from 'react'

const installCommand = 'npm install -g @joohw/boss-cli@latest'

export default function CTA() {
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'error'>('idle')

  async function copyInstallCommand() {
    try {
      await navigator.clipboard.writeText(installCommand)
      setCopyState('copied')
    } catch (error) {
      console.error('复制安装命令失败', error)
      setCopyState('error')
    }
  }

  return (
    <section className="py-24 px-6">
      <div className="max-w-6xl mx-auto">
        <div className="text-center">
          <h2 className="text-3xl font-bold text-white mb-4">立即开始使用</h2>
          <p className="text-slate-400 mb-8 max-w-lg mx-auto">
            一行命令安装，开箱即用。无论是个人 HR 还是招聘团队，boss-cli 都能大幅提升你的招聘效率。完全开源免费。
          </p>
          <div className="flex justify-center">
            <button
              type="button"
              onClick={copyInstallCommand}
              className="px-6 py-2.5 bg-teal-500 hover:bg-teal-600 text-white text-sm font-semibold rounded-md transition-colors"
            >
              {copyState === 'copied' ? '已复制安装命令' : '复制安装命令'}
            </button>
          </div>
          {copyState === 'error' && (
            <p className="text-red-400 text-sm mt-3">复制失败，请检查浏览器剪贴板权限。</p>
          )}
        </div>
      </div>
    </section>
  )
}
