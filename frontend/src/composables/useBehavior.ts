import { ref } from 'vue'
import http from '../api'
import type { BehaviorSnapshot } from '../types'
import { clearExplanationCache } from './useExplanation'

// 实验五：行为驱动的推荐更新。
// 详情页打完分/收藏后调用 bump()，所有正在展示推荐的面板（首页、个人中心）
// 都会因为 revision 变化而重新请求接口 —— 不是前端把旧结果改一改，
// 而是真的再算一遍；同时清掉 AI 解释缓存，避免还引用旧评分的证据。
const revision = ref(0)
const snapshot = ref<BehaviorSnapshot | null>(null)
const loading = ref(false)
const error = ref<unknown>(null)
let pending: Promise<void> | null = null

function refresh(): Promise<void> {
  if (pending) return pending                                  // 多个面板同时挂载时只发一次请求
  loading.value = true
  error.value = null
  pending = http.get<BehaviorSnapshot>('/me/behavior')
    .then(response => { snapshot.value = response.data })
    .catch(e => { error.value = e; snapshot.value = null })
    .finally(() => { loading.value = false; pending = null })
  return pending
}

/** 记录了一次新行为：让依赖推荐结果的视图重新取数。 */
function bump() {
  revision.value += 1
  snapshot.value = null       // 旧指纹立即失效，避免页面短暂显示过期依据
  clearExplanationCache()
  void refresh()
}

export function useBehavior() {
  return { revision, snapshot, loading, error, refresh, bump }
}
