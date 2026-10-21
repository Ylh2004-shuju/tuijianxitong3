# 实验二 新用户冷启动与 ItemCF · 实验报告

- 数据版本：`rec-course-v3-train5-1.0.0`（split `temporal-v1-cold500-train5-v3`）
- 运行模式：`business`（业务 API 只连接 business 库，本次验收全部在业务模式下完成）
- 参与对比的历史用户：`ml:36`（122 条历史评分，其中 61 条 ≥ 4.0 分）
- 证据文件：`lab2/lab2-evidence.json`（由 `scripts/lab2_demo.py` 自动生成）

---

## 1. 任务与完成情况

| 实验任务 | 实现 | 证据 |
|---|---|---|
| 1. 基于训练数据计算可解释的热门榜单 | `recommender/student/popular.py` + `models/business/popular-v1/` | 贝叶斯平滑，最低 100 人评分门槛，Top-1 为《肖申克的救赎》（2577 人，均分 4.43） |
| 2. 新账号可操作的冷启动入口 | `recommender/student/cold_start.py` + 首页「新用户冷启动」面板 | 零行为 → 全站热门榜并标注“非个性化”；收藏/评分后 → 类型偏好推荐 |
| 3. ItemCF 相似度与个性化 Top-10 | `recommender/student/itemcf.py` + `models/business/itemcf-v1/` | ml:36 Top-10 正常输出，无重复、无已评分泄漏、全部在 8459 目录内 |
| 4. 同一历史用户的内容推荐与 ItemCF 对比 | `scripts/lab2_demo.py`、实验室「内容推荐 vs ItemCF」双栏 | Top-10 重叠 0 部；给出 Memento 的真实召回案例 |

## 2. 实现设计

### 2.1 数据与身份

- 业务模型只使用封存的 `business/history_ratings.csv`（798,775 条评分、5,000 名用户、8,459 部电影）。
- 目录固定为训练支持度 ≥ 5 的 8,459 部电影；所有候选都必须落在该目录内。
- `ml:<id>` 与 `app:<id>` 严格分离：历史用户走 `history_ratings`，注册账号走 `account_ratings` / `account_favorites`。

### 2.2 热门榜（任务 1）

单纯按平均分会把“3 个人打 5 分”的电影推到榜首，因此采用贝叶斯平均：

```
score = v/(v+m) · R + m/(v+m) · C
```

- `v`：评分数；`R`：平均分；`C`：全站平均分 3.5369；`m = 50`（先验强度）
- 门槛 `v ≥ 100`，共 1,890 部合格，保存前 500 名
- 每条结果都带 `rating_count` 与 `mean_rating`，接口以 `recall_sources` 返回“全站 N 人评分 · 平均 X 分”，前端直接展示

热门榜的排序与当前用户无关，因此接口和界面都标注“非个性化”；仅对已评分电影做过滤（不改变排序来源）。

### 2.3 冷启动（任务 2）

三态，全部由真实行为触发，不新增数据表：

| 状态 | 触发条件 | 行为 | 界面标注 |
|---|---|---|---|
| 零信号 | 无 ≥ 3.5 分评分，且无收藏 | 返回全站热门榜 | 冷启动 · 热门兜底（非个性化） |
| 有信号 | 至少 1 条 ≥ 3.5 分评分或 1 个收藏 | 类型画像 + 热门先验 | 冷启动 · 基于你的偏好 |
| 画像不可用 | 有信号但电影无类型信息 | 回退热门榜 | 冷启动 · 热门兜底（非个性化） |

有信号时的打分：

```
profile[g] = Σ (rating - 3.0) + 1.0 × 收藏
score(i)   = 0.85 · cos(profile, genres(i)) + 0.15 · popularity_norm(i)
```

收藏被当作“选择行为”`+1.0`，不等同于 5 星评分；已评分与已收藏电影都会从候选中排除。

### 2.4 ItemCF（任务 3）

- 交互定义：评分 ≥ 4.0 视为正反馈（与课程正反馈阈值一致）
- 相似度：二元共现矩阵上的余弦

```
sim(i, j) = |U_i ∩ U_j| / sqrt(|U_i| · |U_j|)
```

- 低支持度处理（数据稀疏）：
  1. 共同评分用户数 < 3 的物品对直接丢弃；
  2. 每部电影只保留相似度最高的 30 个邻居；
  3. 没有任何合格邻居的电影不参与候选，不用噪声相似度凑数。
- 打分：`score(u, i) = Σ_{j ∈ 正反馈种子} sim(i, j) · (rating_uj - 3.0)`
- 输出前统一做目录过滤、已评分过滤、四舍五入到 6 位后按 `(-score, movie_id)` 排序，保证“同分按 ID 升序”的契约。
- 每条结果用 `recall_sources` 给出贡献最大的 1–2 个种子电影，便于解释。

构建结果：8,459 部电影中 7,305 部拥有合格邻居（86.4%），工件 `item_neighbors.json.gz` 1.2 MB；无邻居的 1,154 部不会作为候选出现。

### 2.5 新增与修改文件

```
recommender/student/common.py                 共享特征、缓存、排序与空结果
recommender/student/popular.py                热门榜 Provider（popular-bayes-v1）
recommender/student/itemcf.py                 ItemCF Provider（itemcf-cosine-v1）
recommender/student/cold_start.py             冷启动 Provider（coldstart-v1）
recommender/student/content_recommender.py    接入共享缓存，修正舍入后排序
recommender/student/__init__.py               注册 content / popular / itemcf / cold_start
scripts/build_business_models.py              离线构建三个模型工件与清单
scripts/lab2_demo.py                          证据脚本（对比 + 冷启动三态 + 案例复算）
models/business/popular-v1/                   popular_top.json + manifest.json
models/business/itemcf-v1/                    item_neighbors.json.gz + manifest.json
models/business/coldstart-v1/                 popular_top.json + strategy.json + manifest.json
frontend/src/components/AlgorithmPanel.vue    展示推荐策略、数据来源、单条推荐理由
frontend/src/views/HomeView.vue               新增「新用户冷启动」入口与说明
frontend/src/views/ExperimentsView.vue        新增「内容推荐 vs ItemCF」双栏对比
frontend/src/types.ts                         补 recall_sources 字段
```

四个模型清单均通过官方校验：

```bash
python -m recommender.check model models/business/itemcf-v1/manifest.json --mode business
# {"status": "validated_model_binding_only", "model_version": "itemcf-cosine-v1", "runtime_mode": "business"}
```

## 3. 结果验证

| 检查项 | 结果 |
|---|---|
| ItemCF 结果条数 | 10 / 10 |
| 结果重复 | 0 |
| 是否包含已评分电影 | 0（ml:36 的 122 条已评分全部排除） |
| 是否超出 8,459 批准目录 | 否 |
| 冷启动零信号是否标注非个性化 | 是（每条理由均含“热门榜（非个性化）”） |
| 冷启动有信号后结果是否变化 | 是（收藏 Toy Story 后 Top-5 全部换为动画/家庭片） |
| 内容推荐与 ItemCF Top-10 重叠 | 0 / 10（说明两者确实依赖不同数据） |

### 3.1 冷启动三态实测

| 场景 | 策略 | Top-5 |
|---|---|---|
| 新账号 · 零评分零收藏 | 非个性化热门榜 | 肖申克的救赎、教父、非常嫌疑犯、十二怒汉、教父2 |
| 新账号 · 只收藏《玩具总动员》 | 偏好推荐 | 玩具总动员2、怪兽电力公司、心灵奇旅、海洋奇缘、蚁哥正传 |
| 新账号 · 3 条评分（玩具总动员1/2 + 侏罗纪公园） | 偏好推荐 | 怪兽电力公司、心灵奇旅、海洋奇缘、蚁哥正传、洛基与布温克尔历险记 |

第二种场景相比零信号，Top-5 全部发生变化（新增 ID：2294、3114、4886、166461、225173）；第三种场景中《玩具总动员2》因为已被评分而不再出现，顺序也随之改变，说明“评分 → 结果变化”的链路真实生效。

## 4. 同一历史用户：内容推荐 vs ItemCF

用户 `ml:36`，122 条历史评分，其中 61 条 ≥ 4.0。

| 排名 | 内容推荐（content-genre-v1） | 分数 | ItemCF（itemcf-cosine-v1） | 分数 |
|---:|---|---:|---|---:|
| 1 | Crossing Guard, The (1995) · 犯罪/剧情/惊悚 | 0.9066 | Memento (2000) · 悬疑/惊悚 | 12.5536 |
| 2 | Clear and Present Danger (1994) | 0.9066 | Prestige, The (2006) · 剧情/悬疑 | 8.8075 |
| 3 | Last Man Standing (1996) | 0.9066 | Eternal Sunshine of the Spotless Mind (2004) | 7.9190 |
| 4 | Killer, The (1989) | 0.9066 | Shawshank Redemption, The (1994) | 7.6322 |
| 5 | Face/Off (1997) | 0.9066 | Dark Knight Rises, The (2012) | 6.8991 |
| 6 | Cop Land (1997) | 0.9066 | Shining, The (1980) | 6.6050 |
| 7 | Corruptor, The (1999) | 0.9066 | Seven (1995) | 6.5933 |
| 8 | Double Jeopardy (1999) | 0.9066 | Blade Runner (1982) | 6.4961 |
| 9 | Patriot Games (1992) | 0.9066 | American History X (1998) | 6.4540 |
| 10 | Hard-Boiled (1992) | 0.9066 | Interstellar (2014) | 5.8390 |

**为什么两者完全不同：**

1. **依赖的数据不同。** 内容推荐只用“这一位用户自己的评分 + 电影类型”，ItemCF 用“全站用户谁和谁一起被喜欢”的共现关系；两者没有任何共享的排序信号。
2. **内容推荐的粒度是类型组合。** 该用户的画像被 Drama / Thriller / Crime 主导，于是**所有“Action|Crime|Drama|Thriller”组合的电影拿到完全相同的余弦分 0.9066**，只能按 movie_id 升序打破并列。它无法区分同一类型里的“好片”和“平庸片”。
3. **ItemCF 能利用行为差异。** 同样喜欢犯罪/悬疑的用户，可能同时喜欢《搏击俱乐部》《低俗小说》《落水狗》，这三部电影的邻居里就出现了《记忆碎片》《七宗罪》《美国X档案》等口碑作品，分数也随种子评分强度拉开差距。

### 4.1 一个真实推荐案例

**ItemCF 第 1 名《Memento (2000)》（悬疑/惊悚），总分 12.5536，由三条正反馈共同贡献：**

| 种子电影（用户评分） | 与 Memento 的相似度 | 对该项的贡献 |
|---|---:|---:|
| Fight Club (1999)（4.5 分） | 0.5595 | 0.8392 |
| Pulp Fiction (1994)（4.5 分） | 0.4660 | 0.6991 |
| Reservoir Dogs (1992)（4.5 分） | 0.4404 | 0.6605 |

解释：在业务快照中，喜欢《搏击俱乐部》《低俗小说》《落水狗》的用户群体与喜欢《记忆碎片》的用户群体高度重叠（共同评分用户数均 ≥ 3），因此三者相似度分别为 0.56 / 0.47 / 0.44；用户对三部种子都打了 4.5 分（权重 1.5），加权求和后把《记忆碎片》推到第一名。这条链路完全可追溯：**用户评分 → 正反馈种子 → 物品相似度 → 候选得分 → 推荐理由**，接口返回的 `recall_sources` 也正是这三部种子电影。

## 5. 验收标准对照

| 验收标准 | 结果 |
|---|---|
| 新账号零评分得到明确来源的推荐，不把热门榜标成个性化 | 冷启动返回热门榜，每条理由写明“热门榜（非个性化）：全站 N 人评分”；界面策略标签同步显示“热门兜底（非个性化）” |
| 新用户产生偏好/评分后有可验证的推荐变化 | 零信号 Top-5 为剧情/犯罪经典；收藏《玩具总动员》后 Top-5 全变为动画/家庭片，新增 5 部电影 |
| ItemCF 基于真实训练交互，无重复、排除已评分，至少一个历史用户正常出结果 | ml:36 正常输出 10 条；重复 0，已评分泄漏 0，目录外 0 |
| 能说明两者分别依赖什么数据 | 内容：自己的评分 + 电影类型；ItemCF：全站评分共现 + 自己的评分。界面在每个面板下固定展示“依据：……” |

## 6. 复现步骤

1. 构建业务模型工件（宿主机，需要 numpy / scipy）：

   ```powershell
   python scripts/build_business_models.py
   ```

2. 生成对比与冷启动证据（不依赖数据库）：

   ```powershell
   python scripts/lab2_demo.py --user 36
   ```

3. 让 API 加载新代码。`compose.yaml` 已把 `recommender/student`、`recommender/__init__.py`、`models/business` 只读挂载进容器，因此**重启 api 即可**，只有修改 `contracts.py` 或 `backend/` 才需要重建镜像：

   ```powershell
   . .\deployment\offline\runtime.ps1
   Read-CourseRelease
   Open-CourseRuntime -RequireSaved
   Assert-CourseEngine
   Assert-CourseOwnership
   Invoke-CourseCompose restart api
   ```

4. 打开 `http://127.0.0.1:8080`：
   - 注册新账号 → 首页「新用户冷启动」显示非个性化热门榜；
   - 收藏《玩具总动员》或给它评分 → 刷新后结果切换为动画/家庭片；
   - 进入「推荐实验室」选择历史用户 → 查看「内容推荐 vs ItemCF」双栏对比。

5. 容器内自检：

   ```powershell
   Invoke-CourseCompose exec -T api python -m recommender.check model models/business/itemcf-v1/manifest.json --mode business
   Invoke-CourseCompose exec -T api python -m recommender.check selfcheck --algorithm itemcf --subject-kind ml --subject-id 36 --limit 10
   ```

## 7. 局限与后续改进

1. **内容推荐分数颗粒度粗**：只按类型组合计算余弦，同类型组合得分完全相同，导致并列靠 movie_id 决定。改进方向：类型 IDF 加权、加入年代/热度先验，或改为 TF-IDF 向量。
2. **ItemCF 存在热门偏置**：共现余弦会被高支持度电影放大（《肖申克的救赎》在多个用户上都出现），可加入热度惩罚或归一化（如 BM25 / 条件概率）。
3. **冷启动仅使用类型**：后续可加入“相似电影的 ItemCF 邻居”作为第二阶段召回，使少量评分时的结果更具体。
4. **未做离线指标**：本次为业务模式验收；Precision@10 / Recall@10 需要独立的 evaluation Provider（只读 `evaluation/train.csv`）与本实验无关，留给后续实验。
5. **热门榜与 ItemCF 工件需要重建**：数据包或阈值变化后必须重新运行构建脚本并更新清单，不能手改摘要。
