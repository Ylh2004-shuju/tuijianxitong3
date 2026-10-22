import { computed, ref } from 'vue'
import http, { ApiError } from '../api'
import type { EvaluationPayload, EvaluationRow } from '../types'

// 页面级共享：无论页面上有几个推荐面板，评价结果只取一次。
const payload = ref<EvaluationPayload | null>(null)
const missing = ref(false)
const error = ref<unknown>(null)
const loading = ref(false)
let pending: Promise<void> | null = null

function load(): Promise<void> {
  if (payload.value || missing.value || pending) return pending ?? Promise.resolve()
  loading.value = true
  pending = http.get<EvaluationPayload>('/experiments/evaluations')
    .then(response => { payload.value = response.data })
    .catch((e: unknown) => {
      if (e instanceof ApiError && e.status === 501 && e.code === 'EVALUATION_NOT_IMPLEMENTED') missing.value = true
      // 网络异常等其它情况静默降级：面板照常展示推荐结果，只是不显示指标。
      else error.value = e
    })
    .finally(() => { loading.value = false; pending = null })
  return pending
}

export function useEvaluation() {
  const rows = computed<EvaluationRow[]>(() => payload.value?.items ?? [])
  function metricsFor(algorithm: string): EvaluationRow | null {
    return rows.value.find(row => row.algorithm === algorithm) ?? null
  }
  return { payload, rows, loading, missing, error, load, metricsFor }
}
