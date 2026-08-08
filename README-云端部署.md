# 云端自动更新部署包（GitHub Actions + Gitee）

**效果**：每天凌晨 4 点，云端自动在 foodieguide（tonkiang 备用入口）扒取你配置的频道，
自动生成 `live.txt`（DIYP 用）和 `live.m3u8`（电脑播放器用），并自动发布到 Gitee。
你的盒子 DIYP 填一次 Gitee 地址后，**永久自动更新，零操作**。

```
GitHub Actions（每天04:00自动跑）
   ├─ 抓取 foodieguide → 过滤(近三个月/分辨率/排除项) → live.txt
   ├─ 生成 live.m3u8
   └─ 自动推送到 Gitee 仓库（国内访问快）
DIYP 影音 ──拉取──> https://gitee.com/你的用户名/live-src/raw/master/live.txt
```

---

## 一次性配置（约 15 分钟，之后不用再管）

### 第 1 步：准备两个免费账号
- **GitHub**：https://github.com （负责定时跑脚本）
- **Gitee 码云**：https://gitee.com （负责给盒子提供国内可访问的文件）

### 第 2 步：GitHub 建仓库并上传代码
1. GitHub 右上角 `+` → New repository → 名字填 `live-src` → Public → Create
2. 进入仓库页面 → Add file → Upload files → 把本文件夹里的这些文件全部拖进去：
   `simulate_fetch.py`、`txt2m3u.py`、`requirements.txt`、`.github/workflows/update.yml`
   （`.github` 文件夹也要传，文件选择器里勾上"显示隐藏文件"即可看到）
3. Commit changes 提交

### 第 3 步：Gitee 建仓库（空仓库，不用传文件）
1. Gitee `+` → 新建仓库 → 仓库名填 **`live-src`**（必须和 workflow 里一致）→ 公开 → 创建
2. 创建后**不要**勾选"初始化仓库"（保持完全空）——如果已经勾了，删掉重建

### 第 4 步：Gitee 生成私人令牌
1. Gitee 右上头像 → 设置 → **私人令牌** → 生成新令牌
2. 权限勾选 `projects` 即可 → 生成 → **立刻复制保存**（只显示一次，关掉就没了）

### 第 5 步：把令牌告诉 GitHub（存在加密的 Secrets 里，安全）
1. GitHub 进入 `live-src` 仓库 → Settings → Secrets and variables → Actions
2. New repository secret，创建两个：
   - 名字 `GITEE_TOKEN`，值 = 上一步复制的令牌
   - 名字 `GITEE_USER`，值 = 你的 Gitee 用户名（不是昵称，是登录名）

### 第 6 步：手动跑一次验证
1. GitHub 仓库 → Actions 页 → 左侧 `daily-live-update` → 右边 **Run workflow** 按钮
2. 等 2-5 分钟，看到绿色 ✓ 表示成功
3. 检查仓库里出现 `live.txt`、`live.m3u8`，且 Gitee 的 `live-src` 仓库里也有

### 第 7 步：DIYP 填地址（一次，永久）
DIYP 影音 → 设置 → 直播源 → 添加：
```
https://gitee.com/你的Gitee用户名/live-src/raw/master/live.txt
```
完成！之后每天凌晨 4 点自动更新，盒子打开就是最新源。

---

## 常见问题

| 现象 | 处理 |
|---|---|
| Actions 跑完是红色 ✗ | 点开看日志：若是「服务器繁忙/Server busy」，说明 foodieguide 拦了这个云 IP，见下条；其他报错把日志截图发回分析 |
| foodieguide 被拦 | 说明该时段云 IP 被限。先试手动再跑一次（有时是临时限流）；持续被拦就换执行时间（改 update.yml 里 cron，如 `0 11 * * *`=北京19点），或改用家用电脑定时（见 live-src-toolkit） |
| 想加/减频道 | 改 `simulate_fetch.py` 顶部 `CHANNELS` 列表（name=显示名，keywords=搜索词，res_rule=1080p 或 720p+，exclude=排除关键词），提交后 Actions 自动生效 |
| 想改执行时间 | 改 `update.yml` 里 `cron: "0 20 * * *"`（UTC 时间，北京=UTC+8），提交即可 |
| 费用 | GitHub Actions 公开仓库免费，Gitee 免费，全部 0 元 |

## 目录说明
```
live-src-cloud/
├── simulate_fetch.py        抓取+过滤+生成 live.txt（频道配置在文件顶部）
├── txt2m3u.py               txt → m3u8 转换
├── requirements.txt         Python 依赖
├── .github/workflows/update.yml   定时任务（每天04:00）
└── 本文件                   部署说明
```
