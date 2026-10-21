# 实验二 交付物：新用户冷启动与 ItemCF

| 文件 | 说明 |
|---|---|
| `实验二-新用户冷启动与ItemCF-报告.md` | 实验报告：设计、验收对照、真实对比与案例 |
| `lab2-evidence.json` | 证据数据（ml:36 的内容/ItemCF Top-10、冷启动三态、案例贡献），由脚本生成 |

## 复现

```powershell
# 1) 构建业务模型工件（热门榜 / ItemCF / 冷启动）
python scripts/build_business_models.py

# 2) 复算对比与冷启动证据（不依赖 MySQL）
python scripts/lab2_demo.py --user 36

# 3) 重启 API 让新 Provider 生效（compose 已只读挂载 recommender/student 与 models/business）
powershell -ExecutionPolicy Bypass -File scripts/local-stack.ps1 restart
```

> 为什么不用 `04-启动推荐系统.bat`：受控脚本会先校验 `compose.yaml` 的 SHA-256，而本地这个文件被改过（多出三个 bind 挂载），校验必然失败。详见 [`环境排错-compose哈希不符.md`](环境排错-compose哈希不符.md)。

构建脚本需要 numpy / scipy；运行容器内的 Provider 不需要额外依赖。

`models/business/*` 与 `data/` 按课程 `.gitignore` 保持本地，不进入仓库；克隆仓库后先执行第 1 步即可重建。

## 验收对照

- 零评分新账号：首页「新用户冷启动」→ 全站热门榜，每条理由标注“热门榜（非个性化）”。
- 收藏或评分后：同一入口切换为类型偏好推荐，Top 结果变化。
- ItemCF：历史用户任意选择（如 `ml:36`）→ 10 条结果，无重复、不含已评分电影。
- 算法差异：推荐实验室「内容推荐 vs ItemCF」双栏，同一用户并列对比。
