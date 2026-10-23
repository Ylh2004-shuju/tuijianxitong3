import { ref } from 'vue'
import http, { ApiError } from '../api'
import type { Explanation } from '../types'

// 实验四 S4：大模型个性化推荐解释。
// 页面级共享：无论页面上有几个推荐面板，"是否配置了大模型"只问一次；
// 解释结果按 用户+算法+电影 缓存，重复点开不再请求，也不重复计费。
// null = 还没问过后端；false = 后端确实没配密钥（功能未启用，不是坏了）
const configured = ref<boolean | null>(null)
let statusPending: Promise<void> | null = null
const cache = new Map<string, Explanation>()

function loadStatus(): Promise<void> {
  if (statusPending) return statusPending
  statusPending = http.get<{ configured: boolean; model: string }>('/explanations/status')
    .then(response => { configured.value = response.data.configured })
    .catch(() => { configured.value = false }) // 未接入/不可用时不显示入口，而不是点了才报错
    .finally(() => { statusPending = null })
  return statusPending
}

async function explain(subject: string, algorithm: string, movieId: string): Promise<Explanation> {
  const key = `${subject}|${algorithm}|${movieId}`
  const hit = cache.get(key)
  if (hit) return hit
  const [kind, id] = subject.split(':')
  try {
    const { data } = await http.post<Explanation>('/explanations', {
      subject_kind: kind, subject_id: Number(id), algorithm, movie_id: movieId,
    })
    cache.set(key, data)
    return data
  } catch (e) {
    if (e instanceof ApiError && e.status === 503 && e.code === 'LLM_NOT_CONFIGURED') {
      throw new Error('后端未配置大模型密钥，无法生成解释')
    }
    throw e
  }
}

export function clearExplanationCache() {
  // 新增评分/收藏后，旧解释引用的证据已经过时，必须重新生成
  cache.clear()
}

export function useExplanation() {
  return { configured, loadStatus, explain, clearExplanationCache }
}
