<script setup lang="ts">
import { computed, onMounted, onScopeDispose, ref, watch } from 'vue'
import { Connection } from '@element-plus/icons-vue'
import http, { ApiError } from '../api'
import StatePanel from './StatePanel.vue'
import type { AlgorithmResult, Explanation, Movie } from '../types'
import { useEvaluation } from '../composables/useEvaluation'
import { useExplanation } from '../composables/useExplanation'
import { useAuth } from '../stores/auth'
const props = withDefaults(defineProps<{ endpoint: string | null; label: string; emptySubject?: string; subject?: string }>(), { emptySubject: '登录后体验', subject: '' })
const loading = ref(false), error = ref<unknown>(null), unavailable = ref(false)
const result = ref<AlgorithmResult | null>(null)
const movieMap = ref<Record<string, Movie>>({})
// 该算法的离线准确率（统一协议评价，validation 分区 Top-10）
const { payload, load: loadEvaluation, metricsFor } = useEvaluation()
// 实验四 S4：大模型解释入口只在后端真的配了密钥时出现
const auth = useAuth()
const { configured: llmReady, loadStatus: loadLlmStatus, explain } = useExplanation()
const explanations = ref<Record<string, { loading: boolean; data: Explanation | null; message: string }>>({})
const explainSubject = computed(() => props.subject || auth.account?.subject_key || '')
onMounted(() => { void loadEvaluation(); void loadLlmStatus() })
async function toggleExplanation(movieId: string) {
  const current = explanations.value[movieId]
  if (current?.data || current?.message) { delete explanations.value[movieId]; return }
  explanations.value[movieId] = { loading: true, data: null, message: '' }
  try {
    const data = await explain(explainSubject.value, algorithmKey.value, movieId)
    explanations.value[movieId] = { loading: false, data, message: '' }
  } catch (e) {
    const message = e instanceof Error ? e.message : '暂时无法生成解释，请稍后重试'
    explanations.value[movieId] = { loading: false, data: null, message }
  }
}
const algorithmKey = computed(() => {
  if (result.value?.algorithm) return result.value.algorithm
  const matched = props.endpoint?.match(/\/recommendations\/([a-z_]+)/)
  return matched?.[1] || ''
})
const metrics = computed(() => (algorithmKey.value ? metricsFor(algorithmKey.value) : null))
const metricsLoaded = computed(() => payload.value !== null)
// 每个算法都必须说明"结果来自什么数据"，避免把热门榜当成个性化结果。
const DATA_NOTE: Record<string, string> = {
  content: '依据：电影类型 + 你自己的评分，不使用其他人的行为',
  itemcf: '依据：全站用户评分中共现的电影 + 你自己的评分；已排除你看过的电影',
  popular: '依据：全站高分（≥4 分）人数排序，再做类型多样性重排，与个人偏好无关',
  cold_start: '零评分零收藏时展示全站热门榜；产生收藏或 ≥3.5 分评分后切换为你的类型偏好',
  als: '依据：隐语义因子分解 + 用户/电影偏置基线',
  fusion: '依据：四个算法的推荐名单按 RRF 倒数排名融合；每条结果下方标出它来自哪几个算法、各排第几名',
}
// 融合来源标签用的中文名
const ALGO_NAMES: Record<string, string> = {
  popular: '热门', content: '内容', itemcf: 'ItemCF', als: 'ALS', cold_start: '冷启动',
}
const nonPersonalized = computed(() => {
  const first = result.value?.items?.[0]?.recall_sources?.[0] || ''
  return result.value?.algorithm === 'cold_start' && first.includes('非个性化')
})
const strategy = computed(() => {
  const algorithm = result.value?.algorithm || ''
  if (algorithm === 'cold_start') return nonPersonalized.value ? '冷启动 · 热门兜底（非个性化）' : '冷启动 · 基于你的偏好'
  if (algorithm === 'popular') return '热门榜 · 非个性化'
  if (algorithm === 'itemcf') return 'ItemCF · 物品协同过滤'
  if (algorithm === 'content') return '内容推荐 · 类型画像'
  if (algorithm === 'fusion') return '多算法融合 · RRF'
  return algorithm
})
let run = 0
onScopeDispose(() => { run++ })
async function loadMovies(movieIds: string[]) {
  const missing = movieIds.filter(id => !movieMap.value[id])
  if (!missing.length) return
  try {
    const movies = await Promise.all(
      missing.map(id => http.get<Movie>(`/movies/${id}`).then(r => r.data).catch(() => null))
    )
    for (const m of movies) {
      if (m) movieMap.value[m.movie_id] = m
    }
  } catch { /* ignore movie fetch errors */ }
}
async function load() {
  const seq = ++run
  error.value = null; result.value = null; unavailable.value = false; loading.value = false
  if (!props.endpoint) return
  loading.value = true
  try {
    const response = await http.get<AlgorithmResult>(props.endpoint)
    if (seq !== run) return
    result.value = response.data
    if (response.data.items.length) {
      await loadMovies(response.data.items.map(i => i.movie_id))
    }
  }
  catch (e) {
    if (seq !== run) return
    if (e instanceof ApiError && e.status === 501 && e.code === 'ALGORITHM_NOT_IMPLEMENTED') unavailable.value = true
    else error.value = e
  } finally { if (seq === run) loading.value = false }
}
watch(() => props.endpoint, load, { immediate: true })
// 换观众/换算法后，上一批解释不再适用
watch(() => [props.endpoint, explainSubject.value], () => { explanations.value = {} })
</script>
<template>
  <section class="algorithm-panel" :aria-label="label">
    <div class="section-top"><h3>{{ label }}</h3><span class="small-label">RECOMMENDATION</span></div>
    <div v-if="metrics" class="result-metrics" :aria-label="`${label} 离线准确率`">
      <span class="metric-chip"><small>准确率 Precision@10</small><strong>{{ metrics.precision_at_10.toFixed(4) }}</strong></span>
      <span class="metric-chip"><small>召回率 Recall@10</small><strong>{{ metrics.recall_at_10.toFixed(4) }}</strong></span>
      <span class="metric-chip"><small>命中数</small><strong>{{ metrics.total_hits }}</strong></span>
      <span class="metric-chip"><small>覆盖电影</small><strong>{{ metrics.coverage_movies }}</strong></span>
      <span class="metric-origin">离线准确率 · {{ metrics.model_version }} · {{ payload?.user_count }} 位固定用户 · {{ payload?.partition }} 分区 · Top-{{ payload?.top_k }} · 相关阈值 ≥ {{ payload?.positive_threshold?.toFixed(1) }}</span>
    </div>
    <p v-else-if="metricsLoaded && !metrics" class="metric-note">该算法不参与统一协议离线评价（仅评价 popular / content / itemcf / als / fusion）。</p>
    <StatePanel :loading="loading" :error="error" @retry="load">
      <div v-if="!endpoint || unavailable" class="algorithm-empty" role="status">
        <div class="algorithm-symbol"><el-icon aria-hidden="true"><Connection /></el-icon></div>
        <div><span class="status-pill">{{ !endpoint ? emptySubject : '尚未实现' }}</span><h3>{{ !endpoint ? '选择观众，开始探索' : '下一部好电影，等待算法发现' }}</h3><p>{{ !endpoint ? '先选择历史用户或登录注册账号。推荐功能等待算法接入。' : '等待算法接入；当前没有模型版本或推荐结果。' }}</p></div>
        <RouterLink v-if="!endpoint && emptySubject === '登录后体验'" class="text-link" to="/login">前往登录 →</RouterLink>
      </div>
      <div v-else-if="result" class="recommendation-results">
        <div class="result-strategy"><span class="status-pill">{{ strategy }}</span><span class="small-label">{{ result.algorithm }}</span></div>
        <p class="strategy-note">{{ DATA_NOTE[result.algorithm] || '依据：算法模型输出' }}<template v-if="nonPersonalized">。当前账号还没有评分或收藏，这里展示的是全站热门榜，不是个性化推荐。</template></p>
        <div class="result-meta"><span class="model-version">模型版本 {{ result.model_version }}</span><span class="dataset-version">{{ result.dataset_version }}</span></div>
        <p v-if="!result.items.length" class="empty-hint">本次没有推荐结果。</p>
        <ol v-else class="rec-list">
          <li v-for="item in result.items" :key="item.movie_id" class="rec-item">
            <span class="rec-rank" :class="{ 'top-three': item.rank <= 3 }">{{ item.rank }}</span>
            <div class="rec-info">
              <RouterLink :to="`/movies/${item.movie_id}`" class="rec-title">{{ movieMap[item.movie_id]?.movielens_title || `电影 #${item.movie_id}` }}</RouterLink>
              <div class="rec-meta">
                <span v-if="movieMap[item.movie_id]?.genres?.length" class="rec-genres">{{ movieMap[item.movie_id].genres.slice(0, 3).join(' · ') }}</span>
                <span v-if="item.score !== null" class="rec-score">分数 {{ Number(item.score).toFixed(4) }}</span>
              </div>
              <span v-if="item.recall_sources?.length" class="rec-reason">{{ item.recall_sources[0] }}</span>
              <div v-if="item.recall_ranks" class="rec-sources" :aria-label="`第 ${item.rank} 名由哪些算法贡献`">
                <span class="source-label">融合来源</span>
                <span v-for="(position, name) in item.recall_ranks" :key="name" class="source-chip">
                  {{ ALGO_NAMES[name as string] || name }} <strong>#{{ position }}</strong>
                </span>
              </div>
              <div v-if="llmReady === true && explainSubject" class="explain-row">
                <button type="button" class="explain-toggle" :disabled="explanations[item.movie_id]?.loading"
                  @click="toggleExplanation(item.movie_id)">
                  {{ explanations[item.movie_id]?.loading ? '正在生成解释…'
                    : (explanations[item.movie_id]?.data || explanations[item.movie_id]?.message ? '收起解释' : 'AI 解释为什么推荐') }}
                </button>
                <div v-if="explanations[item.movie_id]?.data" class="explain-box" aria-live="polite">
                  <p class="explain-reason">{{ explanations[item.movie_id].data!.reason }}</p>
                  <ul class="explain-evidence">
                    <li v-for="key in explanations[item.movie_id].data!.evidence" :key="key">
                      {{ explanations[item.movie_id].data!.evidence_detail[key] }}
                    </li>
                  </ul>
                  <p class="explain-foot">由大模型 {{ explanations[item.movie_id].data!.llm_model }} 依据上述证据生成 · {{ algorithmKey }} 模型 {{ explanations[item.movie_id].data!.model_version }}</p>
                </div>
                <p v-else-if="explanations[item.movie_id]?.message" class="explain-error">{{ explanations[item.movie_id].message }}</p>
              </div>
            </div>
          </li>
        </ol>
      </div>
    </StatePanel>
  </section>
</template>
<style scoped>
.result-metrics { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; margin: 0 0 12px; padding: 10px 12px; border: 1px solid var(--border); border-radius: 10px; background: var(--accent-tint); }
.metric-chip { display: flex; flex-direction: column; gap: 2px; padding: 4px 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); }
.metric-chip small { color: var(--muted); font-size: 11px; line-height: 1.4; }
.metric-chip strong { color: var(--accent); font-size: 17px; font-weight: 600; line-height: 1.2; font-variant-numeric: tabular-nums; }
.metric-origin { color: var(--muted); font-size: 11px; line-height: 1.6; }
.metric-note { margin: 0 0 12px; color: var(--muted); font-size: 12px; line-height: 1.7; }
.result-strategy { display: flex; align-items: center; gap: 8px; margin: 0 0 6px; flex-wrap: wrap; }
.strategy-note { font-size: 12px; line-height: 1.7; color: var(--muted); margin: 0 0 10px; }
.rec-reason { display: inline-block; margin-top: 6px; padding: 2px 8px; border-radius: 100px; background: var(--accent-tint); color: var(--accent); font-size: 11px; line-height: 1.6; }
.rec-sources { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.source-label { color: var(--muted); font-size: 11px; }
.source-chip { padding: 1px 8px; border: 1px solid var(--border); border-radius: 100px; font-size: 11px; line-height: 1.6; color: var(--muted); }
.source-chip strong { color: var(--accent); font-variant-numeric: tabular-nums; }
/* 实验四 S4：大模型解释 */
.explain-row { margin-top: 8px; }
.explain-toggle { padding: 2px 10px; border: 1px solid var(--border); border-radius: 100px; background: transparent; color: var(--accent); font-size: 11px; line-height: 1.8; cursor: pointer; }
.explain-toggle:hover:not(:disabled) { background: var(--accent-tint); }
.explain-toggle:disabled { color: var(--muted); cursor: progress; }
.explain-box { margin-top: 8px; padding: 10px 12px; border: 1px solid var(--border); border-left: 2px solid var(--accent); border-radius: 8px; background: var(--surface); }
.explain-reason { margin: 0; font-size: 13px; line-height: 1.9; color: var(--text); }
.explain-evidence { margin: 8px 0 0; padding-left: 18px; color: var(--muted); font-size: 11px; line-height: 1.9; }
.explain-evidence li { list-style: disc; }
.explain-foot { margin: 8px 0 0; color: var(--muted); font-size: 11px; line-height: 1.7; }
.explain-error { margin: 8px 0 0; color: var(--muted); font-size: 11px; line-height: 1.7; }
</style>
