# HMN 文档中心 / 数据中心 MVP

## 目标
- 统一把机器文档与服务文档落到 master 管理的数据中心
- 人类查看用 Markdown
- 机器消费用 JSON index
- 默认 dry-run，只有显式执行才写文件

## 第一版目录约定
- 根目录默认：`/srv/files`
- 机器文档：`/srv/files/docs/server/<host>/README.md`
- 机器索引：`/srv/files/docs/README.md`
- 机器 JSON 索引：`/srv/files/docs/index.json`
- 服务文档：`/srv/files/service/<service_id-or-slug>/README.md`
- 服务索引：`/srv/files/service/README.md`
- 服务 JSON 索引：`/srv/files/service/index.json`
- 额外映射：`/srv/files/service/domain-mapping.json`、`/srv/files/service/runbook-mapping.json`

## 写入边界
- 节点不能直接任意写 `/srv/files`
- 统一由 master 执行 docs sync apply
- 落盘前先做脱敏、路径规范化、索引生成
- apply 默认 dry-run，不加 `--execute` 不写文件

## 后续统一提交流程
1. 节点通过 master API 或 worker 上报 facts / service registry
2. master 负责 redaction、normalization、storage、indexing、audit
3. 当前版本只实现本地 apply 落盘边界
4. 公网 submit API 作为后续能力预留，不在本次 MVP 内

## 推荐命令
```bash
hmn docs sync plan --service-registry /var/lib/hermes-managed-network/service-registry.json --json
hmn docs sync apply --service-registry /var/lib/hermes-managed-network/service-registry.json --root /srv/files --json
hmn docs sync apply --service-registry /var/lib/hermes-managed-network/service-registry.json --root /srv/files --execute --json
```

## 安全要求
- 禁止路径逃逸，所有目标路径 resolve 后必须仍在 root 下
- 禁止 secret 明文写入文档或 JSON index
- 测试仅允许写 tmpdir
- 不连接真实服务器，不做 rsync，不上传外部
