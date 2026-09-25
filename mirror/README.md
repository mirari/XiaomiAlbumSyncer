# 小米云单向镜像执行模块

**完整应用部署及网页任务管理请阅读 [网页单向镜像说明](../README/MIRROR.md)。**
以下为执行模块的独立开发、测试方式；完整应用由 XAS 管理配置和调度，无需另起服务。

此 fork 基于 Coooolfan/XiaomiAlbumSyncer 主线，增加独立 Python 镜像服务。
设计参考 [Acckion fork](https://github.com/Acckion/XiaomiAlbumSyncer) 的云端对账思路，
镜像代码独立实现，沿用本仓库 GPL-3.0 许可。原服务继续负责账号登录和凭据维护。
本模块只读原 SQLite 数据库，不修改原服务的表结构或任务配置，不调用云端删除、移动或编辑接口。

## 功能与边界

- 每轮重新发现全部相册，包括相机和未来新增相册；按云端相册名/文件名存储。
- `reports/*.json` 记录新增、内容哈希变化、移动、删除候选和实际隔离记录。
  移动在 ID 保持不变或唯一 SHA1 匹配时识别；多重匹配按新增/删除报告，不猜测关系。
- 默认只生成清单；显式 `--apply` 才下载、建立基线和清理已管理的旧路径。
- 完整分页、相册数量一致性、下载 SHA1/大小、第二轮云端快照及本地文件状态检查通过后才清理。
  认证失败、接口缺字段、分页异常、同名冲突、扫描期间云端变化均停止清理和基线提交。
- 删除/移动旧路径需至少两次成功执行确认，间隔至少六小时。旧内容放入独立 state 下的
  `quarantine/`，不永久删除；内容替换也先保存旧版本。隔离区无自动过期策略，需要自行保留/清理。
- 首次执行只接管路径和 SHA1 均匹配的已有文件。云端已经不存在的历史本地文件不自动纳管或清除。
  本地修改的文件会阻止覆盖/清理。文件夹可能保留为空目录。
- 已建立基线且云端 SHA1、本地大小、mtime、ctime 均未变化的文件不再读取内容。
  这不是周期性磁盘完整性审计；不检测保持这些元数据不变的底层内容损坏。
- 已验证下载有持久化恢复记录，取消/重启后自动复用；旧版随机 `.part` 文件首次恢复会计算一次 SHA1。
  同文件系统优先原子重命名归位，跨文件系统复制才再次验证；最终检查文件状态，不重复全量哈希。
- 进度最多每两秒更新一次（阶段切换、完成时立即更新），不对每个文件执行进度 fsync。
  基线、报告和下载校验记录仍持久化保存。
- 同一照片同时属于两个云相册时保留两条路径，不做跨相册内容去重。
  因此“相机和手动相册中重复”是否消失取决于云端实际归属，不等于自动全库去重。
- 内容编辑以 SHA1 变化为依据；不保证识别只改日期、收藏等不改变内容的元数据编辑。
  不写 EXIF、不还原云端文件时间；不支持云端非法 Windows 文件名的自动改写。
- 每个账号用独立配置、照片目录、state 目录和容器。不能让两种下载器同时写同一照片目录。

## 运行

要求 Linux/WSL、Python 3.11+，或 Docker。账户须已在原 XAS 中完成认证。
复制 `config.example.json` 为 `config.json`，设置真实 `account_id`。
数据库挂载整个目录为只读，以便 SQLite 读取 WAL；账户凭据不复制到配置文件。
state 放在照片目录之外，不纳入 MT Photos 扫描，并限制目录访问权限。

```sh
pip install -r mirror/requirements.txt
# 一次只读云端扫描，写入报告，不下载或修改照片/基线
python mirror/main.py --config mirror/config.json
# 完成报告核对后建立基线并同步；首次可能下载整个相机相册
python mirror/main.py --config mirror/config.json --apply
# 原生定时运行：默认北京时间每天 03:00，无需 agent/token
python mirror/main.py --config mirror/config.json --apply --daily
```

Docker 示例位于 `compose.example.yml`，设置 `XAS_DB_DIR`、`PHOTO_DIR`、`MIRROR_STATE_DIR`
三个绝对路径。默认 command 是每日报告模式；经核对后自行加入 `--apply`。
`docker compose -f mirror/compose.example.yml run --rm mirror --config /config/config.json`
可立即生成一次报告。常驻服务启动后等待下一个配置时间，不立即同步。
认证失效后须在原服务恢复账号，下一次定时执行再尝试；日志仅输出摘要，不输出凭据。

每轮仍完整查询云端；本地只对新增、变化或缓存失效的文件计算 SHA1。
失败可能已发布校验通过的新文件或已隔离部分旧文件，操作不是全库原子事务；
基线不提交，隔离日志仍保存。停止容器后，可从 `quarantine/` 按相对路径恢复旧文件，
恢复前核对报告和同名文件，不直接覆盖。失败 staging 目录下有效下载会自动复用，
不再被云端引用的临时文件不会自动删除。
这不是独立备份，应保留原有备份。

### 经确认的首次快速接管

完整应用支持在指定任务的 `data/bootstrap.json` 写入一次性接管策略：

```json
{"mode":"path_and_size","root":"/app/download/Ji","account_id":2}
```

仅当任务尚无 `manifest.json`，且 root/account_id 与任务完全匹配时生效。
它信任同路径、同大小的已有文件，不验证其内容；新下载仍校验 SHA1。
首次执行使用扫描时的云端快照，不重复查询云端；因此应事先确认现有文件可信，
接管期间不编辑云端或本地文件。基线建立后自动恢复正常的增量对账与清理保护。
此策略不对其他任务隐式生效，也不能跳过下载失败、路径冲突或最终本地状态检查。

`resume-index.json` 是管理员导入的已校验临时文件索引，键为 state 内相对路径，
值为 `{"sha1":"...","fingerprint":{"size":0,"mtime_ns":0,"ctime_ns":0}}`。
仅在文件状态仍匹配时复用校验结果；该文件与基线一样属于可信状态，不应由其他用户写入。

## 自动跟随上游

`Mirror upstream sync` 每天 UTC 18:17（北京时间次日 02:17）及手动触发：
拉取官方 `main`，正常合并到候选提交，运行镜像测试及容器启动检查；有上游变化时额外编译
Java 25 后端和构建前端。全部成功才非强制推送本 fork 的 `main`。
冲突、权限限制、构建/测试失败会停止并创建/更新 issue，需要人工处理。
此机制跟随开发主线，不只稳定 release；不能保证未来任意 API/数据库变化均自动兼容。
测试不包含真实账号凭据，不代表真实云端接口永久兼容。

上游同步作业自身执行测试，不依赖机器人 push 再触发 CI。
自动更新源码不会重启 NAS 容器或切换已部署版本；部署应固定审阅过的提交。
首次 fork 需启用 GitHub Actions，定时运行仍受 GitHub 调度及仓库活动策略约束。

```sh
python -m unittest discover -s mirror -v
docker build -f mirror/Dockerfile -t xas-mirror:test .
docker run --rm xas-mirror:test --help
```
