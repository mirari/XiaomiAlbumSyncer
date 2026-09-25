<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount } from 'vue'
import Button from 'primevue/button'

const props = defineProps<{ taskId: number }>()
type Run = { id: string; startedAt: string; finishedAt?: string; status: string }
const runs = ref<Run[]>([])
const selected = ref('')
const details = ref<Record<string, unknown>>({})
const error = ref('')
let timer: ReturnType<typeof setTimeout> | undefined
let stopped = false
const labels: Record<string, string> = { running: '执行中', reported: '报告已生成', completed: '同步完成', failed: '执行失败', cancelled: '已停止', interrupted: '服务重启，执行中断', timed_out: '执行超时' }
async function request(path: string, method = 'GET') {
  const response = await fetch(`/api/crontab/${props.taskId}/mirror${path}`, {
    method,
    headers: window.__tenant ? { tenant: window.__tenant } : {},
  })
  if (!response.ok) throw new Error(`请求失败 (${response.status})`)
  const text = await response.text()
  return text ? JSON.parse(text) : null
}
async function loadDetails(id: string) {
  selected.value = id
  const response = await request(`/runs/${id}`)
  details.value = JSON.parse(response.report)
}
async function refresh() {
  try {
    runs.value = await request('/runs')
    if (!selected.value || !runs.value.some(r => r.id === selected.value)) selected.value = runs.value[0]?.id ?? ''
    if (selected.value) await loadDetails(selected.value)
    error.value = ''
  } catch (e) { error.value = e instanceof Error ? e.message : '加载失败' }
}
async function choose(id: string) {
  try { await loadDetails(id) } catch { error.value = '无法加载变更清单' }
}
async function stop() {
  try { await request('/stop', 'POST'); await refresh() } catch { error.value = '停止失败' }
}
async function poll() {
  await refresh()
  if (!stopped) timer = setTimeout(poll, 5000)
}
onMounted(poll)
onBeforeUnmount(() => { stopped = true; clearTimeout(timer) })
function downloadReport() {
  const url = URL.createObjectURL(new Blob([JSON.stringify(details.value, null, 2)], { type: 'application/json' }))
  const link = document.createElement('a')
  link.href = url; link.download = `mirror-${props.taskId}-${selected.value}.json`; link.click()
  URL.revokeObjectURL(url)
}
</script>

<template>
  <section class="space-y-3 text-sm">
    <div class="flex items-center gap-2">
      <strong>镜像执行记录</strong>
      <Button label="刷新" size="small" text @click="refresh" />
      <Button v-if="runs.some(r => r.status === 'running')" label="停止本次执行" severity="warn" size="small" @click="stop" />
    </div>
    <p v-if="error" class="text-red-500">{{ error }}</p>
    <p v-if="!runs.length">尚无镜像记录。可使用任务的“立即执行”按钮开始。</p>
    <div class="max-h-40 overflow-auto space-y-1">
      <button v-for="run in runs" :key="run.id" class="block text-left hover:underline" @click="choose(run.id)">
        {{ new Date(run.startedAt).toLocaleString() }} · {{ labels[run.status] || run.status }}
      </button>
    </div>
    <div v-if="selected" class="space-y-2">
      <p v-if="details.phase">当前阶段：{{ details.phase }} · {{ details.completed }} / {{ details.total }}</p>
      <p v-if="details.error" class="text-red-500">{{ details.error }}</p>
      <div class="flex flex-wrap gap-3">
        <span v-for="entry in [['added','新增'], ['modified','修改'], ['moved','移动'], ['deleted','删除候选'], ['quarantined','已隔离']]" :key="entry[0]">
          {{ entry[1] }}：{{ Array.isArray(details[entry[0]!]) ? (details[entry[0]!] as unknown[]).length : 0 }}
        </span>
      </div>
      <Button label="下载完整变更清单" size="small" @click="downloadReport" />
      <details><summary>查看清单（前 100 项）</summary>
        <pre class="max-h-80 overflow-auto whitespace-pre-wrap text-xs">{{ JSON.stringify(Object.fromEntries(Object.entries(details).map(([key,value]) => [key, Array.isArray(value) ? value.slice(0,100) : value])), null, 2) }}</pre>
      </details>
    </div>
  </section>
</template>
