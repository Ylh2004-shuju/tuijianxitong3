# 04-启动推荐系统 报错排错记录

## 一、现象

运行 `deployment/student-bat/04-启动推荐系统.bat` 后立刻失败：

```
[执行4E工具] course.ps1 start
Release tool/input mismatch: compose.yaml. Restore the approved files.
[失败] 命令失败，原退出码=1；已停止后续步骤。
```

退出码 40。前两行（`[通过] 部署/构建输入及正式包文件存在…`、`[通过] Docker Engine为Linux…`）都是通过的，说明**镜像、密钥、数据包都没问题**，卡在受控文件的 SHA-256 闸门。

## 二、根因

`deployment/offline/runtime.ps1` 的 `Read-CourseRelease` 会逐个比对 `deployment/offline/release.json` 里 `files[]` 的 sha256：

```powershell
if (-not (Test-Path ...) -or (Get-CourseHash $path) -ne $item.sha256) {
    throw "Release tool/input mismatch: $($item.path). Restore the approved files."
}
```

逐文件核对结果（实测）：

| 文件 | release.json 期望 | 实际 | 结论 |
|---|---|---|---|
| `deployment/course.ps1` | 9c591b47… | 9c591b47… | 一致 |
| `deployment/build.ps1` | 69467b45… | 69467b45… | 一致 |
| `deployment/offline/images.ps1` | 8851eb83… | 8851eb83… | 一致 |
| `deployment/offline/runtime.ps1` | dbfed625… | dbfed625… | 一致 |
| `deployment/offline/runtime.env` | bdd7a4eb… | bdd7a4eb… | 一致 |
| `deployment/student-bat/*`（8 个） | 全部一致 | 全部一致 | 一致 |
| **`compose.yaml`** | **dfe75e11…** | **ef911b24…（3483 B）** | **不一致** |

也就是说：13 个受控文件全部原样，只有根目录的 `compose.yaml` 被改过。

佐证：

1. `compose.yaml` 的修改时间是 `2026-10-20 19:44`，而 `deployment/offline/release.json` 是 `2026-10-20 17:04`、包内其它文件都是 `2026-10-19 19:43` —— 它在清单生成之后又被改了一次。
2. 原始文件末尾多了一行 `#（注：内容由AI生成）`（已删除），这是被 AI 重写过的痕迹；`C:/Users/Lakers/Downloads/compose (1).yaml` 里还留着同一份 3514 字节的副本（哈希 `dfa4be08…`），是当时的备份。
3. 当前文件比包内清单 `PACKAGE-CHECKSUMS.json` 记录的版本多出三个 bind 挂载（`models/business`、`recommender/student`、`recommender/__init__.py`）和若干注释改写。这三个挂载本身是**有用**的：学生改完算法不用重建镜像，重启 api 就生效。

另外注意：`release.json`（期望 `dfe75e11…`）和 `PACKAGE-CHECKSUMS.json`（记录 `a3009408…`、3135 B）对 `compose.yaml` 的记录互相也不一致，说明这个文件在发到本地之后被改过不止一次，**原始字节已经在本地找不回来了**。

## 三、影响

- 只影响 `04/05/06` 这三个受控脚本（它们在执行任何 docker 命令前都会先跑这个闸门）。
- 不影响镜像完整性、数据包完整性、密钥、数据库卷，也不影响算法代码。

## 四、处理办法

### 现在就能跑（已验证）

`deployment/local.runtime.json` 里已经存好了项目名、端口、密钥目录和 Docker context，直接用同一份 compose 启动即可，跳过闸门：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/local-stack.ps1 start    # 启动
powershell -ExecutionPolicy Bypass -File scripts/local-stack.ps1 status   # 看状态
powershell -ExecutionPolicy Bypass -File scripts/local-stack.ps1 stop     # 停止
```

等价的手工命令：

```powershell
$env:COMPOSE_PROJECT_NAME='cinelab'; $env:COURSE_WEB_PORT='8080'
$env:COURSE_SECRET_DIR='./deployment/private'; $env:COMPOSE_PROFILES=''
docker --context desktop-linux compose up -d
```

启动后实测结果：

- `http://127.0.0.1:8080/` 返回 200
- `GET /health/ready` → `ready`，`manifest_sha256` 与课程固定值一致
- `GET /api/v1/experiments/algorithms` → `popular / content / itemcf / cold_start` 均 `registered`，`als` 为 `not_implemented`
- 新账号 `popular` 出 10 条（Top-1 = 318《肖申克的救赎》，4.408963），`cold_start` 走热门兜底，`content`/`itemcf` 因无评分返回 0 条（符合预期）
- 实验室历史用户对比：`/demo/history-users/36/recommendations/content` → 10 条（分数都 0.9066 并列按 ID 升序）；`itemcf` → 10 条（Top-1 = 4226，12.554，理由"因你看过《Fight Club (1999)》"）

### 想恢复 04/05/06 脚本

需要拿到与该 `release.json` 匹配的原版 `compose.yaml`（`dfe75e11af617a2b6a32b03db3d99749054e9b06c902a9302d570cb75c74e8e8`），向老师/助教索取 1.3.0-rc1 原始包覆盖即可。**不要手改 `release.json` 或 `PACKAGE-CHECKSUMS.json` 去凑哈希**（安装指南第 196 行也明确禁止），那会让后续真出问题时无法判断是包坏了还是本地改了。

恢复原版后要注意：原版 compose 里没有那三个 bind 挂载，学生算法改动必须走 `deployment/build.ps1` 重建 api 镜像才生效，不能再靠"重启 api"热加载。
