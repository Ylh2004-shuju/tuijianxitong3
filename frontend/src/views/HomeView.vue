<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import { ArrowRight, Search, Reading, Connection, Film, Star } from '@element-plus/icons-vue'
import http from '../api'
import { useAuth } from '../stores/auth'
import { useRemote } from '../composables/useRemote'
import { useBehavior } from '../composables/useBehavior'
import type { Movie, Page } from '../types'
import MovieCard from '../components/MovieCard.vue'
import StatePanel from '../components/StatePanel.vue'
import AlgorithmPanel from '../components/AlgorithmPanel.vue'
const auth = useAuth()
const { data, error, loading, load } = useRemote<Page<Movie>>()
const fetchMovies = () => load(async () => (await http.get<Page<Movie>>('/movies', { params: { per_page: 8 } })).data)
// 实验五：本次推荐实际读取到的行为输入（含可核验的行为指纹）
const { revision, snapshot: behavior, loading: behaviorLoading, error: behaviorError, refresh: refreshBehavior } = useBehavior()
// 首页直接展示真实推荐：融合（实验四 S1）+ 三个个性化算法 + 热门对照
const PANELS = [
  { algorithm: 'fusion', label: '多算法融合（RRF）' },
  { algorithm: 'content', label: '内容推荐（类型画像）' },
  { algorithm: 'itemcf', label: 'ItemCF（物品共现）' },
  { algorithm: 'cold_start', label: '新用户冷启动' },
  { algorithm: 'popular', label: '全站热门（对照）' },
]
const ALGO_CN: Record<string, string> = { fusion: '融合', content: '内容', itemcf: 'ItemCF', cold_start: '冷启动', popular: '热门', als: 'ALS' }
const BEHAVIOR_CN: Record<string, string> = { full: '全量使用你的行为', inherited: '继承成员结果', exclude_only: '仅排除已看', none: '完全不读' }
const latestEvent = computed(() => behavior.value?.latest?.[0] || null)
onMounted(() => { void fetchMovies(); if (auth.account) void refreshBehavior() })
// 换账号后重新读取行为，避免沿用上一个账号的指纹
watch(() => auth.account?.account_id, id => { if (id) void refreshBehavior(); else behavior.value = null })
</script>
<template>
  <div class="page home-page">
    <!-- Hero Section -->
    <section class="hero">
      <div class="hero-bg" aria-hidden="true">
        <div class="glow glow-1" /><div class="glow glow-2" /><div class="glow glow-3" />
      </div>
      <div class="hero-content">
        <div class="hero-copy">
          <div class="hero-badge"><el-icon><Connection /></el-icon><span>推荐系统课程实验</span></div>
          <h1>在光影宇宙中<br /><span>发现你的下一部</span>好电影</h1>
          <p class="hero-description">基于 MovieLens 真实电影数据，体验内容推荐、协同过滤、矩阵分解等经典算法。记录你的评分，让 AI 更懂你的品味。</p>
          <div class="hero-actions">
            <RouterLink class="button primary" to="/movies">
              <el-icon><Film /></el-icon> 浏览电影库
            </RouterLink>
            <RouterLink class="button subtle" to="/experiments">
              <el-icon><Star /></el-icon> 推荐实验室
            </RouterLink>
          </div>
          <div class="hero-stats">
            <div class="stat-item"><strong>8,459</strong><span>电影数据</span></div>
            <div class="stat-divider" />
            <div class="stat-item"><strong>6 种</strong><span>推荐算法</span></div>
            <div class="stat-divider" />
            <div class="stat-item"><strong>5,000</strong><span>历史用户</span></div>
          </div>
        </div>
        <div class="hero-visual" aria-hidden="true">
          <div class="movie-stack">
            <div class="stack-card card-1"><div class="card-poster" /><div class="card-info"><span class="card-title">经典电影</span><span class="card-meta">探索 · 发现</span></div></div>
            <div class="stack-card card-2"><div class="card-poster" /><div class="card-info"><span class="card-title">个性化推荐</span><span class="card-meta">AI 驱动</span></div></div>
            <div class="stack-card card-3"><div class="card-poster" /><div class="card-info"><span class="card-title">你的片单</span><span class="card-meta">记录评分</span></div></div>
          </div>
          <div class="floating-badge badge-1"><el-icon><Star /></el-icon> 评分系统</div>
          <div class="floating-badge badge-2"><el-icon><Connection /></el-icon> 智能推荐</div>
        </div>
      </div>
    </section>

    <!-- Feature Strip -->
    <div class="feature-strip">
      <div class="feature-card">
        <div class="feature-icon"><el-icon><Film /></el-icon></div>
        <div class="feature-text"><strong>真实片库</strong><span>MovieLens 完整电影数据</span></div>
      </div>
      <div class="feature-card">
        <div class="feature-icon"><el-icon><Search /></el-icon></div>
        <div class="feature-text"><strong>自由探索</strong><span>片名类型多维搜索</span></div>
      </div>
      <div class="feature-card">
        <div class="feature-icon"><el-icon><Star /></el-icon></div>
        <div class="feature-text"><strong>评分记录</strong><span>构建你的观影档案</span></div>
      </div>
      <div class="feature-card">
        <div class="feature-icon"><el-icon><Connection /></el-icon></div>
        <div class="feature-text"><strong>算法推荐</strong><span>Content · CF · ALS</span></div>
      </div>
    </div>

    <!-- Recommendation Section -->
    <section class="recommendation-section">
      <div class="section-heading">
        <div>
          <p class="eyebrow">MADE FOR YOUR TASTE</p>
          <h2>为你推荐<span class="heading-dot">.</span></h2>
          <p class="muted" v-if="auth.account">当前登录 {{ auth.account.login_name }} · {{ auth.account.subject_key }}，下列结果由推荐接口实时计算</p>
          <p class="muted" v-else>登录后展示属于你的推荐；新账号无行为时走冷启动热门榜</p>
        </div>
        <RouterLink to="/experiments" class="text-link">查看实验室 <el-icon><ArrowRight /></el-icon></RouterLink>
      </div>

      <!-- 实验五：推荐依据（可核验的输入指纹） -->
      <section v-if="auth.account" class="basis-card" aria-label="本次推荐依据">
        <header class="basis-head">
          <div>
            <p class="eyebrow">WHY THESE MOVIES</p>
            <h3>本次推荐依据 · 实时读取</h3>
          </div>
          <button type="button" class="basis-refresh" :disabled="behaviorLoading" @click="refreshBehavior">
            {{ behaviorLoading ? '读取中…' : '重新读取并重算' }}
          </button>
        </header>
        <StatePanel :loading="behaviorLoading" :error="behaviorError" @retry="refreshBehavior">
          <div v-if="behavior" class="basis-body">
            <div class="basis-fingerprint">
              <span class="basis-label">行为指纹</span>
              <code>{{ behavior.fingerprint }}</code>
              <span class="basis-hint">sha256(全部评分 + 收藏) 取前 12 位</span>
            </div>
            <dl class="basis-facts">
              <div><dt>读取时间</dt><dd>{{ behavior.as_of }}</dd></div>
              <div><dt>读取到</dt><dd><strong>{{ behavior.rating_count }}</strong> 条评分 · <strong>{{ behavior.favorite_count }}</strong> 个收藏</dd></div>
              <div><dt>正反馈</dt><dd>{{ behavior.positive_rating_count }} 条 ≥3.5 分 · 加权种子 {{ behavior.weighted_seed_count }} 个</dd></div>
              <div><dt>当前策略</dt><dd :class="{ 'basis-warn': !behavior.personalized }">{{ behavior.strategy }}</dd></div>
              <div class="wide"><dt>类型画像</dt><dd>{{ behavior.genres.join('、') || '（暂无，等待第一条评分或收藏）' }}</dd></div>
            </dl>
            <p class="basis-subtitle">最近行为 · 数据库时间戳</p>
            <ul class="basis-latest">
              <li v-if="!behavior.latest.length" class="basis-empty">还没有任何评分或收藏，所以现在只能给全站热门榜。</li>
              <li v-for="event in behavior.latest" :key="event.kind + event.movie_id + event.at">
                <span class="event-kind" :class="event.kind">{{ event.kind === 'rating' ? '评分' : '收藏' }}</span>
                <strong v-if="event.rating">{{ event.rating }}</strong>
                <RouterLink :to="`/movies/${event.movie_id}`">《{{ event.title }}》</RouterLink>
                <time>{{ event.at }}</time>
              </li>
            </ul>
            <div class="basis-consumers">
              <span class="basis-label">算法如何使用它</span>
              <span v-for="consumer in behavior.consumers" :key="consumer.algorithm"
                class="consumer-chip" :class="'use-' + consumer.behavior" :title="consumer.note">
                {{ ALGO_CN[consumer.algorithm] || consumer.algorithm }} · {{ BEHAVIOR_CN[consumer.behavior] }}
              </span>
            </div>
            <p class="basis-note">
              新增任意一条评分或收藏，上面的指纹必然变化 —— 这是「系统确实重新读取了新行为」的证据，与推荐列表是否变化无关。
              <template v-if="latestEvent">最近一条行为是 {{ latestEvent.rating ? latestEvent.rating + ' 分' : '收藏' }}《{{ latestEvent.title }}》（{{ latestEvent.at }}）。</template>
            </p>
          </div>
        </StatePanel>
      </section>

      <div class="rec-grid">
        <AlgorithmPanel v-for="panel in PANELS" :key="panel.algorithm" :label="panel.label"
          :endpoint="auth.account ? `/me/recommendations/${panel.algorithm}` : null"
          :refresh-key="revision" />
      </div>
    </section>

    <!-- Browse Section -->
    <section class="browse-section">
      <div class="section-heading">
        <div>
          <p class="eyebrow">THE FILM ARCHIVE</p>
          <h2>热门片库<span class="heading-dot">.</span></h2>
          <p class="muted">精选热门电影，开启你的光影之旅</p>
        </div>
        <RouterLink to="/movies" class="text-link">查看全部 <el-icon><ArrowRight /></el-icon></RouterLink>
      </div>
      <StatePanel :loading="loading" :error="error" :empty="!!data && !data.items.length" @retry="fetchMovies">
        <div class="movie-grid"><MovieCard v-for="movie in data?.items" :key="movie.movie_id" :movie="movie" /></div>
      </StatePanel>
    </section>
  </div>
</template>

<style scoped>
.hero {
  position: relative;
  padding: 0;
  border-radius: 24px;
  overflow: hidden;
  background: linear-gradient(135deg, #1a1b3d 0%, #0f1020 100%);
  border: 1px solid var(--border);
  min-height: 420px;
  /* 全局样式把 .hero 定义成两栏 grid（旧版插画布局），这里必须覆盖，
     否则 .hero-content 只会占 1.1fr 那一栏，正文被挤成 260px 的窄条 */
  display: block;
}
.hero-bg { position: absolute; inset: 0; overflow: hidden; }
.glow { position: absolute; border-radius: 50%; filter: blur(80px); opacity: .4; }
.glow-1 { width: 300px; height: 300px; background: #7c4dff; top: -80px; right: -50px; }
.glow-2 { width: 250px; height: 250px; background: #b388ff; bottom: -60px; left: 20%; opacity: .25; }
.glow-3 { width: 180px; height: 180px; background: #651fff; top: 40%; left: -40px; opacity: .3; }
.hero-content {
  position: relative;
  z-index: 1;
  display: grid;
  /* minmax(0,…) 很关键：否则右栏插画的 min-content 会把左栏正文挤成 233px 的窄条 */
  grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr);
  gap: 40px;
  padding: 56px 52px;
  align-items: center;
}
.hero-badge {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  background: rgba(124, 77, 255, .15);
  border: 1px solid rgba(124, 77, 255, .3);
  border-radius: 100px;
  font-size: 12px;
  color: var(--accent);
  font-weight: 500;
  margin-bottom: 20px;
}
.hero-badge .el-icon { font-size: 14px; }
.hero h1 {
  font-size: clamp(32px, 3.8vw, 52px);
  line-height: 1.2;
  font-weight: 700;
  letter-spacing: -.03em;
  margin: 0 0 18px;
}
.hero h1 span { color: var(--accent); }
.hero-description {
  color: var(--muted);
  font-size: 15px;
  line-height: 1.85;
  max-width: 460px;
}
.hero-actions {
  display: flex;
  gap: 12px;
  margin-top: 28px;
  flex-wrap: wrap;
}
.hero-actions .button { min-height: 48px; padding: 0 24px; font-size: 14px; }
.hero-stats {
  display: flex;
  align-items: center;
  gap: 24px;
  margin-top: 36px;
  padding-top: 28px;
  border-top: 1px solid var(--border);
}
.stat-item { display: flex; flex-direction: column; gap: 4px; }
.stat-item strong { font-size: 22px; font-weight: 700; color: var(--text); white-space: nowrap; }
.stat-item span { font-size: 12px; color: var(--muted); }
.stat-divider { width: 1px; height: 32px; background: var(--border); }

.hero-visual {
  position: relative;
  height: 360px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.movie-stack { position: relative; width: 280px; height: 340px; }
.stack-card {
  position: absolute;
  width: 200px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  overflow: hidden;
  box-shadow: 0 20px 40px -10px rgba(0, 0, 0, .4);
  transition: transform .4s cubic-bezier(.4, 0, .2, 1);
}
.card-poster {
  width: 100%;
  height: 120px;
  background: linear-gradient(135deg, #7c4dff30, #651fff20);
  position: relative;
}
.card-poster::after {
  content: '';
  position: absolute;
  inset: 0;
  background: radial-gradient(circle at 30% 40%, rgba(255,255,255,.1), transparent 60%);
}
.card-info { padding: 12px 14px; display: flex; flex-direction: column; gap: 4px; }
.card-title { font-size: 13px; font-weight: 600; color: var(--text); }
.card-meta { font-size: 11px; color: var(--muted); }

.card-1 {
  top: 0; left: 40px;
  transform: rotate(-8deg) translateY(0);
  z-index: 3;
  background: linear-gradient(180deg, #2d2055, #1e1b3d);
}
.card-1 .card-poster { background: linear-gradient(135deg, #9d4edd30, #7c4dff20); }
.card-2 {
  top: 80px; left: 0;
  transform: rotate(3deg);
  z-index: 2;
  background: linear-gradient(180deg, #231d4a, #181638);
}
.card-2 .card-poster { background: linear-gradient(135deg, #5c6bc030, #3f51b520); }
.card-3 {
  top: 160px; left: 50px;
  transform: rotate(-3deg);
  z-index: 1;
  background: linear-gradient(180deg, #1e1b3d, #14122e);
}
.card-3 .card-poster { background: linear-gradient(135deg, #7c4dff30, #e040fb20); }

.movie-stack:hover .card-1 { transform: rotate(-12deg) translateY(-8px); }
.movie-stack:hover .card-2 { transform: rotate(5deg) translateY(-4px); }
.movie-stack:hover .card-3 { transform: rotate(-5deg) translateY(4px); }

.floating-badge {
  position: absolute;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 100px;
  font-size: 12px;
  font-weight: 500;
  color: var(--text);
  box-shadow: 0 8px 24px -6px rgba(0, 0, 0, .4);
  animation: float 4s ease-in-out infinite;
}
.floating-badge .el-icon { color: var(--accent); font-size: 14px; }
.badge-1 { top: 20px; right: 0; animation-delay: 0s; }
.badge-2 { bottom: 40px; right: 10px; animation-delay: 1.5s; }
@keyframes float {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-8px); }
}

.feature-strip {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin: 36px 0;
}
.feature-card {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 18px 20px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  transition: all .3s ease;
}
.feature-card:hover {
  border-color: var(--accent);
  transform: translateY(-2px);
  box-shadow: 0 8px 24px -8px rgba(124, 77, 255, .25);
}
.feature-icon {
  width: 42px;
  height: 42px;
  display: grid;
  place-items: center;
  background: var(--accent-tint);
  border-radius: 10px;
  color: var(--accent);
  font-size: 20px;
  flex-shrink: 0;
}
.feature-text { display: flex; flex-direction: column; gap: 2px; }
.feature-text strong { font-size: 14px; font-weight: 600; }
.feature-text span { font-size: 12px; color: var(--muted); }

.recommendation-section { margin-top: 50px; }
/* 实验五：推荐依据卡片 */
.basis-card {
  margin: 18px 0 20px;
  padding: 18px 20px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
}
.basis-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 12px; }
.basis-head h3 { margin: 2px 0 0; font-size: 17px; font-weight: 600; }
.basis-refresh {
  padding: 6px 14px; border: 1px solid var(--border); border-radius: 100px;
  background: transparent; color: var(--accent); font-size: 12px; line-height: 1.6; cursor: pointer;
}
.basis-refresh:hover:not(:disabled) { background: var(--accent-tint); }
.basis-refresh:disabled { color: var(--muted); cursor: progress; }
.basis-body { display: flex; flex-direction: column; gap: 12px; }
.basis-fingerprint { display: flex; align-items: baseline; flex-wrap: wrap; gap: 10px; }
.basis-label { color: var(--muted); font-size: 11px; letter-spacing: .02em; }
.basis-fingerprint code {
  padding: 3px 10px; border-radius: 6px; background: var(--accent-tint); color: var(--accent);
  font-size: 14px; font-weight: 600; letter-spacing: .08em;
}
.basis-hint { color: var(--muted); font-size: 11px; }
.basis-facts { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px 16px; margin: 0; }
.basis-facts > div { display: flex; flex-direction: column; gap: 3px; }
.basis-facts > div.wide { grid-column: span 4; }
.basis-facts dt { color: var(--muted); font-size: 11px; }
.basis-facts dd { margin: 0; font-size: 13px; line-height: 1.6; color: var(--text); font-variant-numeric: tabular-nums; }
.basis-facts dd strong { color: var(--accent); font-weight: 600; }
.basis-warn { color: var(--muted); }
.basis-subtitle { margin: 2px 0 0; color: var(--muted); font-size: 11px; }
.basis-latest { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
.basis-latest li { display: flex; align-items: baseline; flex-wrap: wrap; gap: 8px; font-size: 12px; line-height: 1.7; }
.basis-latest a { color: var(--text); text-decoration: none; }
.basis-latest a:hover { color: var(--accent); }
.basis-latest strong { color: var(--accent); font-variant-numeric: tabular-nums; }
.basis-latest time { color: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.basis-latest .basis-empty { color: var(--muted); }
.event-kind { padding: 1px 8px; border-radius: 100px; border: 1px solid var(--border); font-size: 11px; color: var(--muted); }
.event-kind.rating { border-color: var(--accent); color: var(--accent); }
.basis-consumers { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; }
.consumer-chip { padding: 2px 10px; border-radius: 100px; border: 1px solid var(--border); font-size: 11px; line-height: 1.7; color: var(--muted); }
.consumer-chip.use-full { border-color: var(--accent); color: var(--accent); }
.consumer-chip.use-none { border-style: dashed; }
.basis-note { margin: 0; padding-top: 10px; border-top: 1px dashed var(--border); color: var(--muted); font-size: 11px; line-height: 1.8; }
.rec-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(330px, 1fr));
  gap: 20px;
}

.browse-section { margin-top: 50px; }

@media (max-width: 1100px) {
  .hero-content { padding: 48px 40px; gap: 30px; }
  .hero-stats { gap: 18px; }
  .stat-item strong { font-size: 20px; }
  .feature-strip { grid-template-columns: repeat(2, 1fr); }
  .basis-facts { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .basis-facts > div.wide { grid-column: span 2; }
}
@media (max-width: 780px) {
  .hero-content {
    grid-template-columns: 1fr;
    padding: 40px 32px;
    gap: 30px;
  }
  .hero-visual { height: 280px; }
  .movie-stack { transform: scale(.85); }
  .hero-stats { gap: 16px; margin-top: 28px; padding-top: 22px; }
  .stat-item strong { font-size: 18px; }
  .basis-facts { grid-template-columns: 1fr; }
  .basis-facts > div.wide { grid-column: span 1; }
}
@media (max-width: 540px) {
  .hero { border-radius: 18px; }
  .hero-content { padding: 32px 24px; gap: 24px; }
  .hero h1 { font-size: 30px; }
  .hero-visual { display: none; }
  .hero-stats { flex-wrap: wrap; gap: 12px; }
  .stat-divider { display: none; }
  .feature-strip { grid-template-columns: 1fr; gap: 12px; }
  .hero-actions .button { flex: 1; min-width: 140px; }
}
</style>
