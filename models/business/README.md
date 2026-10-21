此目录仅预留给学生之后生成的真实 business 模型及其版本/摘要清单。
Compose 将此目录只读挂载到 /app/models/business，沿用阶段4D的
model_manifest_path；不得放入 evaluation 模型用于业务推荐。

当前学生工件（均由 `scripts/build_business_models.py` 从封存的
`business/history_ratings.csv` 生成，可用 `python -m recommender.check model` 复核）：

- `content/model_manifest.json` + `genre_index.json`：内容推荐（content-genre-v1）
- `popular-v1/manifest.json` + `popular_top.json`：贝叶斯热门榜（popular-bayes-v1，非个性化）
- `itemcf-v1/manifest.json` + `item_neighbors.json.gz`：物品共现相似度（itemcf-cosine-v1）
- `coldstart-v1/manifest.json` + `popular_top.json` + `strategy.json`：冷启动（coldstart-v1）

修改数据包、阈值或算法后必须重新生成工件并更新清单摘要，不能手改哈希。
