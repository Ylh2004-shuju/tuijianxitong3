<script setup lang="ts">
import { onScopeDispose, ref, watch } from 'vue'
import { Connection } from '@element-plus/icons-vue'
import http, { ApiError } from '../api'
import StatePanel from './StatePanel.vue'
import type { AlgorithmResult, Movie } from '../types'
const props = withDefaults(defineProps<{ endpoint: string | null; label: string; emptySubject?: string }>(), { emptySubject: '登录后体验' })
const loading = ref(false), error = ref<unknown>(null), unavailable = ref(false)
const result = ref<AlgorithmResult | null>(null)
const movieMap = ref<Record<string, Movie>>({})
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
</script>
<template>
  <section class="algorithm-panel" :aria-label="label">
    <div class="section-top"><h3>{{ label }}</h3><span class="small-label">RECOMMENDATION</span></div>
    <StatePanel :loading="loading" :error="error" @retry="load">
      <div v-if="!endpoint || unavailable" class="algorithm-empty" role="status">
        <div class="algorithm-symbol"><el-icon aria-hidden="true"><Connection /></el-icon></div>
        <div><span class="status-pill">{{ !endpoint ? emptySubject : '尚未实现' }}</span><h3>{{ !endpoint ? '选择观众，开始探索' : '下一部好电影，等待算法发现' }}</h3><p>{{ !endpoint ? '先选择历史用户或登录注册账号。推荐功能等待算法接入。' : '等待算法接入；当前没有模型版本或推荐结果。' }}</p></div>
        <RouterLink v-if="!endpoint && emptySubject === '登录后体验'" class="text-link" to="/login">前往登录 →</RouterLink>
      </div>
      <div v-else-if="result" class="recommendation-results">
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
            </div>
          </li>
        </ol>
      </div>
    </StatePanel>
  </section>
</template>
