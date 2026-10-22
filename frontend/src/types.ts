export interface Account { account_id: string; subject_key: string; login_name: string }
export interface Movie {
  movie_id: string; movielens_title: string; title_zh: string | null; genres: string[]
  release_year?: number | null; poster_path: string | null; original_title?: string | null
  overview?: string | null; release_date?: string | null; runtime_minutes?: number | null
  original_language?: string | null; directors?: unknown[] | null; cast_top10?: unknown[] | null
  rating?: string | null; created_at?: string; updated_at?: string; timestamp_utc?: number
}
export interface Page<T> { items: T[]; total: number; page: number; per_page: number }
export interface HistoryUser { ml_user_id: string; evaluation_role: string; subject_key: string }
export interface Algorithm { algorithm: string; status: string }
export interface AlgorithmResult {
  status: string; algorithm: string; model_version: string | null; batch_id: string | null
  dataset_version: string; split_version: string; runtime_mode: string
  items: { movie_id: string; rank: number; algorithm: string; model_version: string; score: number | null
    recall_sources?: string[] | null; recall_ranks?: Record<string, number> | null }[]
}
// 实验三统一协议离线评价：validation 分区 Precision@10 / Recall@10
export interface EvaluationRow {
  algorithm: string; model_version: string
  precision_at_10: number; recall_at_10: number
  total_hits: number; users_with_items: number; users_empty: number; coverage_movies: number; status: string
}
export interface EvaluationPayload {
  dataset_version: string; split_version: string; partition: string
  user_count: number; positive_threshold: number; top_k: number; items: EvaluationRow[]
}
